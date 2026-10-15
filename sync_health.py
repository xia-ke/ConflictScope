"""P4 offline data-health inventory and durable, redacted synchronization log.

Read-only health queries never create databases, fetch networks, or expose tokens.
"""
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone

import geoeconomics as geo

_JOB=re.compile(r"^[a-z0-9][a-z0-9._:-]{1,75}$")
LEASE_NAME="market-updater"
LEASE_HOURS=3


def utcnow():
    return datetime.now(timezone.utc)


def _iso(dt=None):
    return (dt or utcnow()).isoformat()


def _job_id(value):
    if not isinstance(value,str) or not _JOB.fullmatch(value):
        raise ValueError("Invalid sync job identifier")
    return value


def fingerprint(records):
    """Canonical checksum of fetched observations or a file checksum at call site."""
    return hashlib.sha256(json.dumps(records,sort_keys=True,ensure_ascii=False,
             separators=(",",":"),allow_nan=False).encode("utf-8")).hexdigest()


def initialize(path=geo.DEFAULT_DB):
    geo.initialize(path)


def lease(path=geo.DEFAULT_DB):
    """Exclusive SQLite lease. A second worker fails closed; expiration is logged."""
    initialize(path)
    token=uuid.uuid4().hex
    at=utcnow()
    expiry=at+timedelta(hours=LEASE_HOURS)
    with geo.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        prev=conn.execute("SELECT expires_at FROM sync_leases WHERE name=?",
                          (LEASE_NAME,)).fetchone()
        if prev and prev[0]>_iso(at):
            return None
        conn.execute("""INSERT INTO sync_leases(name,token,expires_at)
            VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET
            token=excluded.token,expires_at=excluded.expires_at""",
            (LEASE_NAME,token,_iso(expiry)))
    return token


def renew(path,token):
    with geo.connect(path) as conn:
        updated=conn.execute("""UPDATE sync_leases SET expires_at=?
            WHERE name=? AND token=?""",(_iso(utcnow()+timedelta(hours=LEASE_HOURS)),
                                        LEASE_NAME,token)).rowcount
    if not updated:
        raise RuntimeError("Synchronization lease was lost")


def release(path,token):
    with geo.connect(path) as conn:
        conn.execute("DELETE FROM sync_leases WHERE name=? AND token=?",
                     (LEASE_NAME,token))


def begin(job_id, path=geo.DEFAULT_DB):
    _job_id(job_id)
    with geo.connect(path) as conn:
        cur=conn.execute("""INSERT INTO sync_runs(job_id,started_at,status)
                             VALUES(?,?,'running')""",(job_id,_iso()))
        return cur.lastrowid


def finish(run_id, job_id, status, attempts, count=0,
           digest=None, latest_date=None, error_code=None, path=geo.DEFAULT_DB):
    _job_id(job_id)
    if status not in ("success","empty","error") or attempts<1 or count<0:
        raise ValueError("Invalid sync completion state")
    if digest is not None and not re.fullmatch("[a-f0-9]{64}",digest):
        raise ValueError("Invalid source checksum")
    if latest_date is not None:
        date.fromisoformat(latest_date)
    if error_code is not None and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,55}",error_code):
        raise ValueError("Unsafe error status")
    now=_iso()
    with geo.connect(path) as conn:
        updated=conn.execute("""UPDATE sync_runs
          SET completed_at=?,status=?,attempts=?,record_count=?,
              source_fingerprint=?,failure_code=?
          WHERE id=? AND job_id=? AND status='running'""",
          (now,status,attempts,count,digest,error_code,run_id,job_id)).rowcount
        if updated!=1:
            raise RuntimeError("Sync attempt is already finalized")
        old=conn.execute("SELECT last_success_at FROM source_checks WHERE job_id=?",
                         (job_id,)).fetchone()
        good = old[0] if old else None
        if status=="success":
            good=now
        conn.execute("""INSERT INTO source_checks(
            job_id,checked_at,status,last_success_at,latest_observation,
            fingerprint,failure_code) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(job_id) DO UPDATE SET
              checked_at=excluded.checked_at,status=excluded.status,
              last_success_at=excluded.last_success_at,
              latest_observation=COALESCE(excluded.latest_observation,source_checks.latest_observation),
              fingerprint=COALESCE(excluded.fingerprint,source_checks.fingerprint),
              failure_code=excluded.failure_code""",
            (job_id,now,status,good,latest_date,digest,error_code))


def checked(path=geo.DEFAULT_DB):
    with geo.connect(path,readonly=True) as conn:
        return {row[0]:row[1] for row in conn.execute(
            "SELECT job_id,fingerprint FROM source_checks WHERE fingerprint IS NOT NULL")}


def report(path=geo.DEFAULT_DB, *, today=None):
    """Observability-only endpoint, not a substitute for data-source licensing."""
    today=date.fromisoformat(today) if isinstance(today,str) else (today or utcnow().date())
    with geo.connect(path,readonly=True) as conn:
        conn.row_factory=sqlite3.Row
        ver=conn.execute("PRAGMA user_version").fetchone()[0]
        if ver!=geo.SCHEMA_VERSION:
            raise RuntimeError("Unsupported market database schema")
        jobs=[]
        for row in conn.execute("""SELECT c.*, (SELECT count(*) FROM sync_runs r
                   WHERE r.job_id=c.job_id AND r.status='error') failure_count
                   FROM source_checks c ORDER BY c.job_id"""):
            item=dict(row)
            if item["last_success_at"] is None:
                item["health"]="never_succeeded"
            else:
                last=datetime.fromisoformat(item["last_success_at"]).date()
                lag=(today-last).days
                item["health"]="failed_last_attempt" if item["status"]=="error" else (
                    "stale_update" if lag>10 else "current_update")
            jobs.append(item)
        prices=[]
        for row in conn.execute("""SELECT p.id,p.frequency,s.name source_name,
              count(o.observed_at) observations,max(o.observed_at) newest,
              min(o.observed_at) oldest
              FROM price_series p JOIN data_sources s ON s.id=p.source_id
              LEFT JOIN price_observations o ON o.series_id=p.id
              GROUP BY p.id ORDER BY p.id"""):
            item=dict(row)
            if item["newest"] is None:
                state="missing"
            else:
                lag=(today-date.fromisoformat(item["newest"][:10])).days
                threshold=10 if item["frequency"]=="daily" else 90
                state="stale_observations" if lag>threshold else "current_observations"
            item["health"]=state
            prices.append(item)
        revision_count=conn.execute("SELECT count(*) FROM price_revisions").fetchone()[0]
        unfinished=conn.execute("""SELECT count(*) FROM sync_runs
            WHERE status='running'""").fetchone()[0]
        recent=[dict(row) for row in conn.execute("""SELECT id,job_id,started_at,completed_at,
            status,attempts,record_count,failure_code FROM sync_runs
            ORDER BY id DESC LIMIT 40""")]
        alerts=[{"job_id":j["job_id"],"code":j["health"]}
                for j in jobs if j["health"] in ("never_succeeded","failed_last_attempt","stale_update")]
        alerts.extend({"series_id":p["id"],"code":p["health"]}
                      for p in prices if p["health"]!="current_observations")
        if unfinished:
            alerts.append({"code":"unfinished_attempts","count":unfinished})
        return {"schema_version":ver,"checked_at":_iso(),
                "jobs":jobs,"price_series":prices,"price_revision_count":revision_count,
                "unfinished_attempts":unfinished,"recent_runs":recent,
                "alerts":alerts,
                "note":"Dates indicate last successful fetch and last observed quote separately; historical-only price series may be intentionally stale. No automatic event approvals."}
