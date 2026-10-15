"""P2 human-reviewed conflict and trade-event ledger.

Import is strictly manual and offline. No news headlines or GDELT entries are
silently promoted to verified events; no inference of price causation or severity.
"""
import hashlib
import json
import re
import sqlite3
import urllib.parse
from datetime import date, datetime, timezone

import geoeconomics as geo
import market_data

TYPES = frozenset(("armed_conflict", "ceasefire", "sanction", "embargo",
                   "production_disruption", "logistics_disruption",
                   "trade_policy", "recovery", "other"))
MECHANISMS = frozenset(("supply", "demand", "logistics", "sanctions",
                        "expectations", "other", "undetermined"))
STATUSES = frozenset(("reported", "corroborated", "official", "disputed"))
ROLES = frozenset(("primary", "corroborating", "context"))
BASIS = frozenset(("impact", "occurred", "announced", "effective", "published"))
_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{2,95}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NOW = lambda: datetime.now(timezone.utc).isoformat()


def _text(value, name, max_len, required=True):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    value = value.strip()
    if (required and not value) or len(value) > max_len:
        raise ValueError(f"Invalid {name}")
    return value


def _url(value):
    value = _text(value, "evidence URL", 1800)
    try:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or any(c.isspace() for c in value)):
            raise ValueError("Evidence URLs must be HTTPS without credentials")
    except ValueError as exc:
        raise ValueError("Invalid evidence URL") from exc
    return value


def _date(value, name, *, required=False):
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return None
    value = _text(value, name, 40)
    if _DAY.fullmatch(value):
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Invalid {name}") from exc
    else:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None or dt.utcoffset() is None:
                raise ValueError("Timezone required for sub-day timestamps")
        except ValueError as exc:
            raise ValueError(f"Invalid {name}") from exc
    return value


def _ident(value, field):
    value = _text(value, field, 96)
    if not _ID.fullmatch(value):
        raise ValueError(f"Invalid {field}")
    return value


def validate_batch(payload):
    """Validate whole batch before any event write; schema input v1 is explicit."""
    if not isinstance(payload, dict) or payload.get("format") != "conflictscope.events.v1":
        raise ValueError("Expected conflictscope.events.v1")
    definitions = payload.get("sources")
    events = payload.get("events")
    if (not isinstance(definitions, list) or not 1 <= len(definitions) <= 100
            or not isinstance(events, list) or not 1 <= len(events) <= 200):
        raise ValueError("Requires 1-100 sources and 1-200 events")
    sources = {}
    for item in definitions:
        if not isinstance(item, dict):
            raise ValueError("Source must be an object")
        sid = _ident(item.get("id"), "source.id")
        if sid in sources:
            raise ValueError("Duplicate source id")
        access = item.get("access_type")
        if access not in ("public", "registration", "restricted"):
            raise ValueError("Invalid source access type")
        sources[sid] = {
            "id": sid, "name": _text(item.get("name"), "source.name", 120),
            "homepage_url": _url(item.get("homepage_url")),
            "access_type": access,
            "license_note": _text(item.get("license_note", ""), "license_note", 1000, False)
        }
    seen = set()
    prepared = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("Event must be an object")
        eid = _ident(event.get("id"), "event.id")
        if eid in seen:
            raise ValueError("Duplicate event id in batch")
        seen.add(eid)
        typ = event.get("event_type")
        if typ not in TYPES:
            raise ValueError("Invalid event type")
        status = event.get("verification_status")
        if status not in STATUSES:
            raise ValueError("Invalid verification status")
        primary = event.get("source_id")
        if primary not in sources:
            raise ValueError("Event source_id must be declared")
        source_url = _url(event.get("source_url"))
        dates = {name: _date(event.get(name), name) for name in
                 ("occurred_at", "announced_at", "effective_at", "published_at")}
        if not any(dates.values()):
            raise ValueError("Event needs at least one typed date")
        precision = event.get("date_precision", "day")
        if precision not in ("day", "hour", "minute", "unknown"):
            raise ValueError("Invalid date precision")
        if precision == "day" and any(v and not _DAY.fullmatch(v) for v in dates.values()):
            raise ValueError("Day precision cannot contain timestamps")
        if precision in ("hour", "minute") and any(v and _DAY.fullmatch(v) for v in dates.values()):
            raise ValueError("Subday precision requires timezone timestamps")
        review = event.get("review")
        if not isinstance(review, dict) or review.get("decision") != "approved":
            raise ValueError("Only explicitly editor-approved events can enter the ledger")
        reviewer = _text(review.get("reviewer"), "reviewer", 120)
        reviewed_at = _date(review.get("reviewed_at"), "reviewed_at", required=True)
        review_note = _text(review.get("note", ""), "review.note", 1000, False)
        evidence = event.get("evidence")
        links = event.get("links")
        if (not isinstance(evidence, list) or not 1 <= len(evidence) <= 20
                or not isinstance(links, list) or not 1 <= len(links) <= 30):
            raise ValueError("An event needs evidence and at least one country/commodity link")
        evidence_rows = []
        evidence_urls = set()
        roles = set()
        for item in evidence:
            if not isinstance(item, dict) or item.get("source_id") not in sources:
                raise ValueError("Unregistered evidence source")
            role = item.get("role")
            if role not in ROLES:
                raise ValueError("Invalid evidence role")
            url = _url(item.get("url"))
            evidence_urls.add(url)
            roles.add(role)
            evidence_rows.append((item["source_id"], url, role,
                _date(item.get("published_at"), "evidence.published_at"),
                _text(item.get("note", ""), "evidence.note", 1000, False)))
        if "primary" not in roles or source_url not in evidence_urls:
            raise ValueError("Primary event citation must appear in evidence list")
        link_rows = []
        for item in links:
            if not isinstance(item, dict):
                raise ValueError("Invalid event link")
            country = item.get("country_id")
            commodity = item.get("commodity_id")
            infrastructure = item.get("infrastructure_label")
            if country is not None:
                country = _text(country, "country_id", 3)
                if not re.fullmatch("[A-Z]{3}", country):
                    raise ValueError("Invalid ISO3 country")
            if commodity is not None:
                commodity = _ident(commodity, "commodity_id")
            if infrastructure is not None:
                infrastructure = _text(infrastructure, "infrastructure_label", 200)
            if not any((country, commodity, infrastructure)):
                raise ValueError("Missing event link subject")
            mechanism = item.get("impact_mechanism", "undetermined")
            if mechanism not in MECHANISMS:
                raise ValueError("Invalid impact mechanism")
            url = _url(item.get("evidence_url"))
            if url not in evidence_urls:
                raise ValueError("Impact link must cite included event evidence")
            link_rows.append((country, commodity, infrastructure, mechanism, url,
                _text(item.get("evidence_note", ""), "evidence_note", 1000, False)))
        normalized = {
            "id": eid,
            "title": _text(event.get("title"), "title", 260),
            "event_type": typ, "verification_status": status, "source_id": primary,
            "source_url": source_url, "date_precision": precision,
            "description": _text(event.get("description", ""), "description", 4000, False),
            "dates": dates, "reviewer": reviewer, "reviewed_at": reviewed_at,
            "review_note": review_note, "evidence": evidence_rows, "links": link_rows
        }
        fingerprint = hashlib.sha256(json.dumps(normalized, ensure_ascii=False,
                     sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        prepared.append((normalized, fingerprint))
    return sources, prepared


def import_batch(payload, path=geo.DEFAULT_DB):
    """Atomic event import, immutable IDs, idempotent exact re-imports.

    The DB/country/commodity catalog is initialized before event transaction.
    A changed payload under an existing event ID is refused, not overwritten.
    """
    sources, prepared = validate_batch(payload)
    market_data.initialize(path)
    inserted = skipped = 0
    with geo.connect(path) as conn:
        if conn.execute("PRAGMA user_version").fetchone()[0] != geo.SCHEMA_VERSION:
            raise RuntimeError("Unsupported database schema")
        for src in sources.values():
            old = conn.execute("SELECT name,homepage_url,access_type FROM data_sources WHERE id=?",
                               (src["id"],)).fetchone()
            if old and old != (src["name"], src["homepage_url"], src["access_type"]):
                raise ValueError("Conflicting registered source identity")
            if not old:
                conn.execute("""INSERT INTO data_sources
                    (id,name,homepage_url,access_type,license_note)
                    VALUES (?,?,?,?,?)""",
                    (src["id"], src["name"], src["homepage_url"],
                     src["access_type"], src["license_note"]))
        for ev, fingerprint in prepared:
            current = conn.execute(
                "SELECT input_sha256 FROM event_reviews WHERE event_id=?", (ev["id"],)).fetchone()
            if current:
                if current[0] != fingerprint:
                    raise ValueError(f"Event ID {ev['id']} exists with different content; use a new revision ID")
                skipped += 1
                continue
            if conn.execute("SELECT id FROM events WHERE id=?", (ev["id"],)).fetchone():
                raise ValueError("Unreviewed existing event ID cannot be replaced")
            dt = ev["dates"]
            conn.execute("""INSERT INTO events
                (id,title,event_type,occurred_at,announced_at,effective_at,published_at,
                 date_precision,verification_status,source_id,source_url,description,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ev["id"], ev["title"], ev["event_type"], dt["occurred_at"],
                 dt["announced_at"], dt["effective_at"], dt["published_at"],
                 ev["date_precision"], ev["verification_status"], ev["source_id"],
                 ev["source_url"], ev["description"], _NOW()))
            conn.executemany("""INSERT INTO event_evidence
                (event_id,source_id,url,evidence_role,published_at,source_note)
                VALUES (?,?,?,?,?,?)""", [(ev["id"], *row) for row in ev["evidence"]])
            conn.executemany("""INSERT INTO event_links
                (event_id,country_id,commodity_id,infrastructure_label,
                 impact_mechanism,evidence_url,evidence_note)
                VALUES (?,?,?,?,?,?,?)""", [(ev["id"], *row) for row in ev["links"]])
            conn.execute("""INSERT INTO event_reviews
                (event_id,reviewer,reviewed_at,review_note,input_sha256,approval_state)
                VALUES (?,?,?,?,?,'approved')""",
                (ev["id"],ev["reviewer"],ev["reviewed_at"],ev["review_note"],fingerprint))
            inserted += 1
    return {"inserted": inserted, "unchanged": skipped, "approval_required": True}


_DATE_SQL = {
    "occurred": "coalesce(e.occurred_at,e.announced_at,e.effective_at,e.published_at)",
    "announced": "coalesce(e.announced_at,e.occurred_at,e.effective_at,e.published_at)",
    "effective": "coalesce(e.effective_at,e.occurred_at,e.announced_at,e.published_at)",
    "published": "coalesce(e.published_at,e.announced_at,e.occurred_at,e.effective_at)",
    "impact": """CASE WHEN e.event_type IN ('sanction','embargo','trade_policy')
             THEN coalesce(e.effective_at,e.announced_at,e.occurred_at,e.published_at)
             ELSE coalesce(e.occurred_at,e.effective_at,e.announced_at,e.published_at) END"""
}
TRACK = {
    "armed_conflict":"conflict", "ceasefire":"conflict",
    "sanction":"policy", "embargo":"policy", "trade_policy":"policy",
    "production_disruption":"supply", "recovery":"supply",
    "logistics_disruption":"logistics", "other":"other"
}


def timeline(*, country=None, commodity=None, event_type=None, basis="impact",
             start=None, end=None, limit=100, path=geo.DEFAULT_DB):
    """Read approved events only. Never consult RSS or perform network calls."""
    if not country and not commodity:
        raise ValueError("Country or commodity filter required")
    if country is not None and not re.fullmatch("[A-Z]{3}", country):
        raise ValueError("Invalid country")
    if commodity is not None:
        _ident(commodity, "commodity")
    if event_type is not None and event_type not in TYPES:
        raise ValueError("Invalid event type")
    if basis not in BASIS or type(limit) is not int or limit < 1 or limit > 200:
        raise ValueError("Invalid timeline options")
    for name, val in (("start",start),("end",end)):
        if val is not None and (not _DAY.fullmatch(val) or _date(val, name) is None):
            raise ValueError(f"Invalid {name}")
    if start and end and start > end:
        raise ValueError("Start later than end")
    expr = _DATE_SQL[basis]
    predicates = ["r.approval_state='approved'"]
    params = []
    if country:
        predicates.append("""EXISTS(SELECT 1 FROM event_links l WHERE l.event_id=e.id
                                AND l.country_id=?)""")
        params.append(country)
    if commodity:
        predicates.append("""EXISTS(SELECT 1 FROM event_links l WHERE l.event_id=e.id
                                AND l.commodity_id=?)""")
        params.append(commodity)
    if event_type:
        predicates.append("e.event_type=?")
        params.append(event_type)
    if start:
        predicates.append(f"substr(({expr}),1,10)>=?")
        params.append(start)
    if end:
        predicates.append(f"substr(({expr}),1,10)<=?")
        params.append(end)
    sql = f"""SELECT e.*, s.name AS source_name, r.reviewed_at,
         ({expr}) AS timeline_date
         FROM events e JOIN event_reviews r ON r.event_id=e.id
         JOIN data_sources s ON s.id=e.source_id
         WHERE {' AND '.join(predicates)}
         ORDER BY timeline_date DESC,e.id LIMIT ?"""
    params.append(limit)
    with geo.connect(path, readonly=True) as conn:
        conn.row_factory = sqlite3.Row
        events = [dict(row) for row in conn.execute(sql, params)]
        if events:
            ids = [row["id"] for row in events]
            marks = ",".join("?" for _ in ids)
            evidence = {eid: [] for eid in ids}
            links = {eid: [] for eid in ids}
            for row in conn.execute(f"""SELECT v.event_id,v.url,v.evidence_role,
                    v.published_at,v.source_note,s.name AS source_name
                    FROM event_evidence v JOIN data_sources s ON v.source_id=s.id
                    WHERE v.event_id IN ({marks})
                    ORDER BY v.event_id,CASE v.evidence_role WHEN 'primary' THEN 0
                    WHEN 'corroborating' THEN 1 ELSE 2 END,v.id""",ids):
                x=dict(row); evidence[x.pop("event_id")].append(x)
            for row in conn.execute(f"""SELECT event_id,country_id,commodity_id,
                    infrastructure_label,impact_mechanism,evidence_url,evidence_note
                    FROM event_links WHERE event_id IN ({marks}) ORDER BY id""",ids):
                x=dict(row);links[x.pop("event_id")].append(x)
            for item in events:
                item["evidence"] = evidence[item["id"]]
                item["links"] = links[item["id"]]
                item["track"] = TRACK[item["event_type"]]
                item["editor_reviewed"] = True
    return {
        "country": country, "commodity": commodity, "event_type": event_type,
        "basis": basis, "start": start, "end": end, "limit": limit,
        "events": events, "count": len(events),
        "note": "Editorially approved evidence records; not independent verification or proof of price causality."
    }
