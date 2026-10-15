#!/usr/bin/env python3
"""ConflictScope V3 geoeconomic schema and offline initialization (standard library).

P0/P2: offline, provenance-first storage; no inferred conflict/price causality.
All business tables are independent of the existing map/news HTTP endpoints.
"""
import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "geoeconomics.sqlite3"
SCHEMA_VERSION = 3
TABLES = (
    "countries", "data_sources", "commodities", "commodity_hs_codes",
    "price_series", "price_observations", "trade_flows", "events", "event_links",
    "event_evidence", "event_reviews", "sync_runs", "source_checks", "price_revisions",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS countries (
  id TEXT PRIMARY KEY CHECK(length(id) = 3 AND id = upper(id)),
  name TEXT NOT NULL,
  region TEXT
);
CREATE TABLE IF NOT EXISTS data_sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  homepage_url TEXT NOT NULL,
  access_type TEXT NOT NULL CHECK(access_type IN ('public','registration','restricted')),
  license_note TEXT NOT NULL DEFAULT '',
  redistribution_allowed INTEGER CHECK(redistribution_allowed IN (0,1)),
  checked_at TEXT
);
CREATE TABLE IF NOT EXISTS commodities (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  group_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commodity_hs_codes (
  commodity_id TEXT NOT NULL REFERENCES commodities(id),
  hs_revision TEXT NOT NULL,
  hs_code TEXT NOT NULL CHECK(length(hs_code) = 6),
  mapping_note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (commodity_id, hs_revision, hs_code)
);
CREATE TABLE IF NOT EXISTS price_series (
  id TEXT PRIMARY KEY,
  commodity_id TEXT NOT NULL REFERENCES commodities(id),
  source_id TEXT NOT NULL REFERENCES data_sources(id),
  benchmark TEXT NOT NULL,
  market TEXT NOT NULL DEFAULT '',
  price_type TEXT NOT NULL CHECK(price_type IN ('spot','futures','index','freight')),
  frequency TEXT NOT NULL CHECK(frequency IN ('daily','weekly','monthly')),
  currency TEXT NOT NULL,
  unit TEXT NOT NULL,
  contract_note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS price_observations (
  series_id TEXT NOT NULL REFERENCES price_series(id),
  observed_at TEXT NOT NULL,
  value REAL NOT NULL,
  retrieved_at TEXT NOT NULL,
  source_published_at TEXT,
  PRIMARY KEY (series_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_price_date ON price_observations(observed_at);
CREATE TABLE IF NOT EXISTS trade_flows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  reporter_id TEXT NOT NULL REFERENCES countries(id),
  partner_id TEXT REFERENCES countries(id),
  flow TEXT NOT NULL CHECK(flow IN ('import','export')),
  period_start TEXT NOT NULL,
  period_type TEXT NOT NULL CHECK(period_type IN ('annual','monthly')),
  hs_revision TEXT NOT NULL,
  hs_code TEXT NOT NULL CHECK(length(hs_code) = 6),
  value_usd REAL NOT NULL CHECK(value_usd >= 0),
  quantity REAL,
  quantity_unit TEXT,
  source_id TEXT NOT NULL REFERENCES data_sources(id),
  retrieved_at TEXT NOT NULL,
  source_published_at TEXT,
  is_mirror_estimate INTEGER NOT NULL DEFAULT 0 CHECK(is_mirror_estimate IN (0,1)),
  CHECK (quantity IS NULL OR quantity >= 0),
  CHECK (quantity IS NULL OR (quantity_unit IS NOT NULL AND length(quantity_unit) > 0)),
  UNIQUE(reporter_id, partner_id, flow, period_start, period_type,
         hs_revision, hs_code, source_id, is_mirror_estimate)
);
-- SQLite UNIQUE treats NULL partner IDs as distinct: normalize only in identity index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_identity ON trade_flows(
  reporter_id, COALESCE(partner_id, '__ALL__'), flow, period_start, period_type,
  hs_revision, hs_code, source_id, is_mirror_estimate
);
CREATE INDEX IF NOT EXISTS idx_trade_reporter_period ON trade_flows(reporter_id, period_start);
CREATE INDEX IF NOT EXISTS idx_trade_hs_period ON trade_flows(hs_revision, hs_code, period_start);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  event_type TEXT NOT NULL CHECK(event_type IN (
    'armed_conflict','ceasefire','sanction','embargo','production_disruption',
    'logistics_disruption','trade_policy','recovery','other')),
  occurred_at TEXT,
  announced_at TEXT,
  effective_at TEXT,
  published_at TEXT,
  date_precision TEXT NOT NULL DEFAULT 'day'
    CHECK(date_precision IN ('day','hour','minute','unknown')),
  verification_status TEXT NOT NULL DEFAULT 'reported'
    CHECK(verification_status IN ('reported','corroborated','official','disputed')),
  source_id TEXT NOT NULL REFERENCES data_sources(id),
  source_url TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  CHECK (occurred_at IS NOT NULL OR announced_at IS NOT NULL
         OR effective_at IS NOT NULL OR published_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_effective_at ON events(effective_at);
CREATE TABLE IF NOT EXISTS event_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  country_id TEXT REFERENCES countries(id),
  commodity_id TEXT REFERENCES commodities(id),
  infrastructure_label TEXT,
  impact_mechanism TEXT NOT NULL CHECK(impact_mechanism IN (
    'supply','demand','logistics','sanctions','expectations','other','undetermined')),
  evidence_url TEXT NOT NULL,
  evidence_note TEXT NOT NULL DEFAULT '',
  CHECK(country_id IS NOT NULL OR commodity_id IS NOT NULL
        OR infrastructure_label IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_event_links_country ON event_links(country_id);
CREATE INDEX IF NOT EXISTS idx_event_links_commodity ON event_links(commodity_id);
-- P2 additive migration: multiple sourced references and explicit editorial approval.
CREATE TABLE IF NOT EXISTS event_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES data_sources(id),
  url TEXT NOT NULL,
  evidence_role TEXT NOT NULL CHECK(evidence_role IN ('primary','corroborating','context')),
  published_at TEXT,
  source_note TEXT NOT NULL DEFAULT '',
  UNIQUE(event_id, source_id, url)
);
CREATE INDEX IF NOT EXISTS idx_event_evidence_event ON event_evidence(event_id);
CREATE TABLE IF NOT EXISTS event_reviews (
  event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
  reviewer TEXT NOT NULL CHECK(length(trim(reviewer)) > 0),
  reviewed_at TEXT NOT NULL,
  review_note TEXT NOT NULL DEFAULT '',
  input_sha256 TEXT NOT NULL CHECK(length(input_sha256) = 64),
  approval_state TEXT NOT NULL CHECK(approval_state = 'approved')
);
-- P4: immutable per-attempt results, no API credentials / response-body storage.
CREATE TABLE IF NOT EXISTS sync_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  status TEXT NOT NULL CHECK(status IN ('running','success','empty','error')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
  record_count INTEGER NOT NULL DEFAULT 0 CHECK(record_count>=0),
  source_fingerprint TEXT,
  failure_code TEXT,
  CHECK(source_fingerprint IS NULL OR length(source_fingerprint)=64)
);
CREATE INDEX IF NOT EXISTS idx_sync_job_started ON sync_runs(job_id,started_at DESC);
CREATE TABLE IF NOT EXISTS source_checks (
  job_id TEXT PRIMARY KEY,
  checked_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('success','empty','error')),
  last_success_at TEXT,
  latest_observation TEXT,
  fingerprint TEXT,
  failure_code TEXT
);
CREATE TABLE IF NOT EXISTS price_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  series_id TEXT NOT NULL REFERENCES price_series(id),
  observed_at TEXT NOT NULL,
  previous_value REAL NOT NULL,
  replacement_value REAL NOT NULL,
  detected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_revisions_series ON price_revisions(series_id,observed_at);
"""


def connect(path=DEFAULT_DB, *, readonly=False):
    """Return SQLite connection with foreign keys enforced on every connection."""
    path = Path(path)
    if readonly:
        # mode=ro avoids silently creating a database when checking status.
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize(path=DEFAULT_DB, *, seed_countries=True):
    """Idempotent additive v0/v1/v2 to v3 migration. No destructive changes or remote fetch."""
    with connect(path) as conn:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        if current not in (0, 1, 2, SCHEMA_VERSION):
            raise RuntimeError(f"Unsupported database schema version: {current}")
        conn.executescript(SCHEMA)
        if seed_countries:
            config = json.loads((ROOT / "data" / "conflicts.json").read_text(encoding="utf-8"))
            # DO NOTHING preserves manually corrected country metadata on re-init.
            conn.executemany(
                """INSERT INTO countries(id, name, region) VALUES (?, ?, ?)
                   ON CONFLICT(id) DO NOTHING""",
                [(row["id"], row["name"], row.get("region")) for row in config["countries"]]
            )
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def status(path=DEFAULT_DB):
    """Report version and row counts; never create or modify the DB."""
    with connect(path, readonly=True) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported/uninitialized schema version: {version}")
        return {"schema_version": version, "tables": {
            name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in TABLES
        }}


def main():
    parser = argparse.ArgumentParser(description="ConflictScope V3 local geoeconomic database")
    parser.add_argument("command", choices=("init", "status"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize(args.db)
        print(json.dumps(status(args.db), ensure_ascii=False, indent=2))
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        parser.exit(1, f"geoeconomics: {exc}\n")


if __name__ == "__main__":
    main()
