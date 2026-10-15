"""P1 transactional storage and read-only analysis queries for approved sources."""
import math
import sqlite3
from pathlib import Path

import geoeconomics as geo
import market_sources as sources

SOURCES = {
    "comtrade": ("UN Comtrade", "https://comtradeapi.un.org/", "registration",
        "API preview has record/rate limits; inspect UN Comtrade terms before redistribution."),
    "pink-sheet": ("World Bank Pink Sheet", sources.PINK_SHEET_PAGE, "public",
        "World Bank dataset terms govern redistribution; preserve benchmark metadata."),
    "eia": ("U.S. Energy Information Administration", sources.EIA_PAGE, "registration",
        "Review EIA data usage and underlying series attribution before redistribution."),
}


def _catalog(conn):
    for source, (name, url, access, note) in SOURCES.items():
        conn.execute("""INSERT INTO data_sources(id,name,homepage_url,access_type,license_note)
                        VALUES (?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
                     (source, name, url, access, note))
    for ident, (name, group, hs) in sources.COMMODITIES.items():
        conn.execute("""INSERT INTO commodities(id,name,group_name) VALUES(?,?,?)
                        ON CONFLICT(id) DO NOTHING""", (ident, name, group))
        conn.execute("""INSERT INTO commodity_hs_codes
            (commodity_id,hs_revision,hs_code,mapping_note) VALUES (?,?,?,?)
            ON CONFLICT DO NOTHING""",
            (ident, sources.HS_REVISION, hs,
             "Strict six-digit demonstration mapping; not total commodity trade."))


def initialize(path=geo.DEFAULT_DB):
    geo.initialize(path)
    with geo.connect(path) as conn:
        _catalog(conn)


def save_trade(rows, path=geo.DEFAULT_DB):
    """Import complete single-item aggregates. Entire batch commits or rolls back."""
    if not rows:
        return 0
    when = sources.retrieved_at()
    with geo.connect(path) as conn:
        if conn.execute("PRAGMA user_version").fetchone()[0] != geo.SCHEMA_VERSION:
            raise ValueError("Database must be initialized")
        _catalog(conn)
        for row in rows:
            if row["reporter"] not in sources.REPORTERS or row["hs_code"] not in (
                    item[2] for item in sources.COMMODITIES.values()):
                raise ValueError("Unapproved country or commodity")
            conn.execute("""INSERT INTO trade_flows(
                  reporter_id,partner_id,flow,period_start,period_type,
                  hs_revision,hs_code,value_usd,quantity,quantity_unit,source_id,retrieved_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT DO UPDATE SET value_usd=excluded.value_usd,
                      quantity=excluded.quantity,quantity_unit=excluded.quantity_unit,
                      retrieved_at=excluded.retrieved_at""",
                (row["reporter"], row["partner"], row["flow"], row["period_start"],
                 row["period_type"], row["hs_revision"], row["hs_code"],
                 row["value_usd"], row["quantity"], row["quantity_unit"], "comtrade", when))
        # NULL partner aggregate uniqueness is implemented with an expression index.
        # SQLite INSERT ON CONFLICT without target correctly matches that index.
    return len(rows)


def save_prices(series_id, commodity_id, source, benchmark, frequency,
                currency, unit, observations, path=geo.DEFAULT_DB):
    if not series_id or commodity_id not in sources.COMMODITIES or source not in SOURCES:
        raise ValueError("Unknown series, commodity or source")
    if not observations or len(observations) != len({date for date, _ in observations}):
        raise ValueError("Missing or duplicate observations")
    if frequency not in ("daily", "monthly") or not unit.strip() or not currency.strip():
        raise ValueError("Invalid series metadata")
    if source == "eia" and frequency != "daily":
        raise ValueError("EIA spot prices must be daily")
    if source == "pink-sheet" and frequency != "monthly":
        raise ValueError("Pink Sheet prices must be monthly")
    for date, value in observations:
        if not isinstance(date, str) or not math.isfinite(float(value)) or float(value) <= 0:
            raise ValueError("Invalid price")
    when = sources.retrieved_at()
    with geo.connect(path) as conn:
        if conn.execute("PRAGMA user_version").fetchone()[0] != geo.SCHEMA_VERSION:
            raise ValueError("Database must be initialized")
        _catalog(conn)
        price_type = "spot"
        old = conn.execute("""SELECT commodity_id,source_id,benchmark,price_type,
            frequency,currency,unit FROM price_series WHERE id=?""", (series_id,)).fetchone()
        identity = (commodity_id,source,benchmark,price_type,frequency,currency,unit)
        if old and old != identity:
            raise ValueError("Series metadata changed: use a new stable series ID")
        conn.execute("""INSERT INTO price_series
            (id,commodity_id,source_id,benchmark,price_type,frequency,currency,unit)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""", (series_id, *identity))
        # Track upstream corrections without treating a repeated sync as a revision.
        # No credentials or provider response payload is persisted in this audit table.
        conn.executemany("""INSERT INTO price_revisions
            (series_id,observed_at,previous_value,replacement_value,detected_at)
            SELECT series_id,observed_at,value,?,? FROM price_observations
            WHERE series_id=? AND observed_at=? AND value<>?""",
            [(float(value),when,series_id,day,float(value)) for day,value in observations])
        conn.executemany("""INSERT INTO price_observations
            (series_id,observed_at,value,retrieved_at) VALUES (?,?,?,?)
            ON CONFLICT(series_id,observed_at)
            DO UPDATE SET value=excluded.value,retrieved_at=excluded.retrieved_at""",
            [(series_id, day, float(value), when) for day, value in observations])
    return len(observations)


def commodity_catalog(path=geo.DEFAULT_DB):
    with geo.connect(path, readonly=True) as conn:
        conn.row_factory = sqlite3.Row
        return {"scope": "curated_hs6_mapping",
                "commodities": [dict(row) for row in conn.execute(
                    """SELECT c.id,c.name,c.group_name,m.hs_revision,m.hs_code
                       FROM commodities c LEFT JOIN commodity_hs_codes m ON m.commodity_id=c.id
                       ORDER BY c.id,m.hs_code""")]}


def trade_by_country(country, *, year=None, flow=None, path=geo.DEFAULT_DB):
    if country not in sources.REPORTERS:
        raise ValueError("Unsupported pilot country")
    if year is not None and not 1990 <= int(year) <= 2100:
        raise ValueError("Invalid year")
    if flow is not None and flow not in ("import","export"):
        raise ValueError("Invalid flow")
    clauses = ["t.reporter_id=?", "t.partner_id IS NULL"]
    args = [country]
    if year:
        clauses.append("substr(t.period_start,1,4)=?")
        args.append(str(year))
    if flow:
        clauses.append("t.flow=?")
        args.append(flow)
    with geo.connect(path, readonly=True) as conn:
        conn.row_factory = sqlite3.Row
        sql = """SELECT t.reporter_id,t.flow,t.period_start,t.hs_revision,t.hs_code,
                 c.id commodity_id,c.name commodity,t.value_usd,t.quantity,t.quantity_unit,
                 t.retrieved_at,t.is_mirror_estimate,s.name source_name,s.homepage_url source_url
                 FROM trade_flows t JOIN commodity_hs_codes h
                 ON t.hs_code=h.hs_code AND t.hs_revision=h.hs_revision
                 JOIN commodities c ON c.id=h.commodity_id
                 JOIN data_sources s ON s.id=t.source_id
                 WHERE """ + " AND ".join(clauses) + """
                 ORDER BY t.period_start DESC,t.value_usd DESC LIMIT 200"""
        rows = [dict(r) for r in conn.execute(sql, args)]
        return {"country": country, "year": year, "flow": flow,
                "scope": "selected_hs6_world_partner_only",
                "not_total_trade_ranking": True, "rows": rows}


def price_series_list(path=geo.DEFAULT_DB):
    with geo.connect(path, readonly=True) as conn:
        conn.row_factory = sqlite3.Row
        return {"series": [dict(r) for r in conn.execute(
            """SELECT p.id,p.commodity_id,p.benchmark,p.market,p.price_type,p.frequency,
                      p.currency,p.unit,s.name source_name,s.homepage_url source_url,
                      count(o.observed_at) observation_count,min(o.observed_at) first_date,
                      max(o.observed_at) last_date
               FROM price_series p JOIN data_sources s ON s.id=p.source_id
               LEFT JOIN price_observations o ON o.series_id=p.id
               GROUP BY p.id ORDER BY p.id""")]}


def price_history(series_id, *, start=None, end=None, path=geo.DEFAULT_DB):
    if start and end and start > end:
        raise ValueError("Invalid date range")
    with geo.connect(path, readonly=True) as conn:
        conn.row_factory = sqlite3.Row
        series = conn.execute("""SELECT p.id,p.commodity_id,p.benchmark,p.frequency,
                       p.currency,p.unit,s.name source_name,s.homepage_url source_url
                       FROM price_series p JOIN data_sources s ON s.id=p.source_id
                       WHERE p.id=?""", (series_id,)).fetchone()
        if series is None:
            return None
        filters = ["series_id=?"]
        args = [series_id]
        if start:
            filters.append("observed_at>=?")
            args.append(start)
        if end:
            filters.append("observed_at<=?")
            args.append(end)
        rows = [dict(row) for row in conn.execute(
            """SELECT observed_at,value,source_published_at,retrieved_at
               FROM price_observations WHERE """ + " AND ".join(filters) +
            " ORDER BY observed_at DESC LIMIT 5000", args)]
        rows.reverse()
        return {"metadata": dict(series), "observations": rows,
                "truncated_at_5000": len(rows) == 5000}
