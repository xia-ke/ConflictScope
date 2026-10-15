#!/usr/bin/env python3
"""Backfill historical benchmark prices into ConflictScope's existing SQLite store.

Modes:
  fred       - curated FRED CSV spot/IMF benchmarks (no FRED API key)
  worldbank  - World Bank Pink Sheet Monthly Prices XLSX (file or official download)
  eia        - EIA daily Brent/WTI with EIA_API_KEY (existing official adapter)
  coverage   - local series counts, date span and optional event coverage (offline)

Each fetch is bounded and validated before transactional upsert. No cron jobs,
no market API calls during HTTP GET, no price-to-conflict causality claims.
Python 3.10+ standard library; requires P2 branch database schema.
"""
import argparse
import csv
import io
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

import event_data
import geoeconomics as geo
import market_data
import market_sources

START_DEFAULT = "2021-01-01"
END_DEFAULT = "2025-12-31"
WB_URL = market_sources.PINK_SHEET_DOC
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
MAX_BYTES = 9_000_000
UA = "ConflictScope/3.0 (+https://github.com/xia-ke/ConflictScope)"

# All symbols have verified canonical publisher pages. The IMF series have
# non-open redistribution rights; storage is for locally authorized research.
FRED = {
    "DCOILBRENTEU": {
        "commodity": "oil", "benchmark": "Brent crude spot (EIA via FRED)",
        "frequency": "daily", "currency": "USD", "unit": "barrel",
        "origin": "U.S. Energy Information Administration",
        "license": "EIA spot observations via FRED; citation requested; check use terms",
        "source": "fred-eia"
    },
    "DCOILWTICO": {
        "commodity": "oil", "benchmark": "WTI Cushing spot (EIA via FRED)",
        "frequency": "daily", "currency": "USD", "unit": "barrel",
        "origin": "U.S. Energy Information Administration",
        "license": "EIA spot observations via FRED; citation requested; check use terms",
        "source": "fred-eia"
    },
    "PWHEAMTUSDM": {
        "commodity": "wheat", "benchmark": "IMF global wheat price (FRED)",
        "frequency": "monthly", "currency": "USD", "unit": "metric ton",
        "origin": "International Monetary Fund",
        "license": "IMF-republished FRED series; copyrighted, check redistribution terms",
        "source": "fred-imf"
    },
    "PCOPPUSDM": {
        "commodity": "copper", "benchmark": "IMF global copper price (FRED)",
        "frequency": "monthly", "currency": "USD", "unit": "metric ton",
        "origin": "International Monetary Fund",
        "license": "IMF-republished FRED series; copyrighted, check redistribution terms",
        "source": "fred-imf"
    },
}
SOURCES = {
    "fred-eia": ("FRED: EIA Spot Prices", "https://fred.stlouisfed.org/", "public",
                 "Underlying EIA, public domain citation requested; verify FRED display restrictions."),
    "fred-imf": ("FRED: IMF Commodity Prices", "https://fred.stlouisfed.org/", "public",
                 "IMF copyright / third-party FRED reuse restrictions apply; DO NOT redistribute prices without permission."),
}

def strict_day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Expected YYYY-MM-DD")
    date.fromisoformat(value)
    return value


def window(start, end):
    start, end = strict_day(start), strict_day(end)
    if start > end:
        raise ValueError("Start date must not exceed end date")
    if end > date.today().isoformat():
        raise ValueError("End date must not be in the future")
    return start, end


def https_bytes(url, *, loader=None):
    """Fetch bounded payload from fixed official origins only; verify redirects."""
    allowed = {"fred.stlouisfed.org", "thedocs.worldbank.org"}
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or u.hostname not in allowed or u.username or u.password:
        raise ValueError("Unapproved HTTPS source")
    if loader is not None:
        raw = loader(url)
    else:
        req = urllib.request.Request(url, headers={"User-Agent":UA,"Accept":"text/csv,application/octet-stream,*/*"})
        with urllib.request.urlopen(req, timeout=25) as response:
            dest = urllib.parse.urlsplit(response.geturl())
            if dest.scheme != "https" or dest.hostname not in allowed or dest.username or dest.password:
                raise ValueError("External redirect outside official source")
            raw = response.read(MAX_BYTES + 1)
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError("Unexpected or oversized source response")
    return raw


def fred_csv(symbol, start, end, *, loader=None):
    """Return validated (date,price) observations from official FRED graph CSV."""
    if symbol not in FRED:
        raise ValueError("Unsupported or unverified FRED symbol")
    start, end = window(start, end)
    url = FRED_URL + "?" + urllib.parse.urlencode({"id":symbol})
    raw = https_bytes(url, loader=loader)
    try:
        stream = io.StringIO(raw.decode("utf-8-sig", errors="strict"))
    except UnicodeDecodeError as exc:
        raise ValueError("Source is not UTF-8 CSV") from exc
    reader = csv.DictReader(stream)
    if reader.fieldnames is None or len(reader.fieldnames) != 2:
        raise ValueError("Unexpected FRED CSV header")
    date_field = reader.fieldnames[0].strip()
    symbol_field = reader.fieldnames[1].strip()
    if date_field not in ("DATE", "observation_date") or symbol_field != symbol:
        raise ValueError("CSV source/series mismatch")
    result = []
    seen = set()
    for row in reader:
        if None in row:
            raise ValueError("Malformed CSV row")
        day = strict_day(row[reader.fieldnames[0]])
        if day < start or day > end:
            continue
        raw_value = (row.get(reader.fieldnames[1]) or "").strip()
        if raw_value in ("", ".", "NaN", "nan"):
            continue
        value = market_sources.finite_price(raw_value)
        if day in seen:
            raise ValueError("Duplicate observation date")
        seen.add(day)
        result.append((day, value))
    result.sort()
    if not result:
        raise ValueError("No usable FRED observations for selected date range")
    return result


def wb_xlsx(column, start, end, *, raw=None, loader=None):
    """Official WB monthly workbook: explicit source column/units required."""
    start, end = window(start, end)
    if raw is None:
        raw = https_bytes(WB_URL, loader=loader)
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError("Invalid/oversized workbook")
    if column is None:
        return market_sources.pink_sheet_columns(raw)
    rows = market_sources.pink_sheet_rows(raw, column)
    selection = [(day, price) for day, price in rows if start <= day <= end]
    if not selection:
        raise ValueError("No World Bank monthly prices in the selected date range")
    return selection


def ensure_sources():
    """Register additional source identifiers recognized by P1 persistence."""
    market_data.SOURCES.update(SOURCES)


def store_fred(symbol, observations, db):
    item = FRED[symbol]
    ensure_sources()
    market_data.initialize(db)
    return market_data.save_prices(
        "fred:"+symbol, item["commodity"], item["source"], item["benchmark"],
        item["frequency"], item["currency"], item["unit"], observations, db
    )


def store_wb(series, commodity, column, unit, observations, db):
    if not re.fullmatch(r"wb:[a-z0-9][a-z0-9._:-]{1,80}",series):
        raise ValueError("Stable series ID must start with wb:")
    if commodity not in market_sources.COMMODITIES or not unit.strip():
        raise ValueError("Commodity and official unit required")
    market_data.initialize(db)
    return market_data.save_prices(
        series, commodity, "pink-sheet", column, "monthly",
        "USD", unit, observations, db
    )


def coverage(db, *, country=None):
    """No-network local diagnostics; do not interpolate monthly data into daily."""
    if not Path(db).exists():
        raise ValueError("Market DB missing; initialize and import before checking coverage")
    if country is not None and (not re.fullmatch(r"[A-Z]{3}",country)):
        raise ValueError("Invalid country filter")
    catalog = market_data.price_series_list(db)["series"]
    reports = []
    for item in catalog:
        reports.append({
            "series_id": item["id"], "benchmark": item["benchmark"],
            "commodity": item["commodity_id"], "frequency": item["frequency"],
            "unit": item["currency"]+"/"+item["unit"],
            "count": item["observation_count"],
            "first_date": item["first_date"], "last_date": item["last_date"],
            "source": item["source_name"]
        })
    summary = {"series": reports, "total_series": len(reports),
               "warning": "Monthly benchmarks are NOT daily event prices. No causal inference."}
    if country:
        events = event_data.timeline(country=country, limit=200, path=db)["events"]
        summary["event_country"] = country
        summary["event_count"] = len(events)
        summary["event_range"] = [
            min([e["timeline_date"] for e in events]) if events else None,
            max([e["timeline_date"] for e in events]) if events else None
        ]
    return summary


def cli(argv=None):
    parser = argparse.ArgumentParser(description="ConflictScope historical commodity spot-price backfill")
    parser.add_argument("--db", type=Path, default=geo.DEFAULT_DB, help="SQLite path (before subcommand)")
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("catalog", help="List verified symbols and source-use limits")
    f = subs.add_parser("fred", help="Backfill curated FRED CSV series (no API key)")
    f.add_argument("--series", required=True, choices=sorted(FRED))
    f.add_argument("--start", default=START_DEFAULT)
    f.add_argument("--end", default=END_DEFAULT)
    f.add_argument("--dry-run", action="store_true")
    wb = subs.add_parser("worldbank", help="Backfill official Pink Sheet XLSX monthly observations")
    loc = wb.add_mutually_exclusive_group(required=True)
    loc.add_argument("--file", type=Path)
    loc.add_argument("--download", action="store_true")
    wb.add_argument("--list-columns", action="store_true", help="Inspect workbook without import")
    wb.add_argument("--column", help="Exact official worksheet column")
    wb.add_argument("--commodity", choices=sorted(market_sources.COMMODITIES))
    wb.add_argument("--unit", help="Explicit workbook currency unit, e.g. metric ton")
    wb.add_argument("--series-id", help="Stable wb:... ID")
    wb.add_argument("--start", default=START_DEFAULT)
    wb.add_argument("--end", default=END_DEFAULT)
    wb.add_argument("--dry-run", action="store_true")
    e = subs.add_parser("eia", help="Backfill EIA Brent/WTI daily spot (requires EIA_API_KEY)")
    e.add_argument("--series", required=True, choices=sorted(market_sources.EIA_SERIES))
    e.add_argument("--start", default=START_DEFAULT)
    e.add_argument("--end", default=END_DEFAULT)
    e.add_argument("--dry-run", action="store_true")
    check = subs.add_parser("coverage", help="Show locally imported price range and optional event span")
    check.add_argument("--country", help="Optional ISO3 event country")

    args = parser.parse_args(argv)
    db = args.db
    if args.command == "catalog":
        return {"fred": FRED, "worldbank": {"url":WB_URL,"frequency":"monthly",
                      "note":"Explicit column, mapping and unit required"},
                "eia":market_sources.EIA_SERIES,
                "default_backfill":[START_DEFAULT,END_DEFAULT]}
    if args.command == "coverage":
        return coverage(db,country=args.country)
    start,end=window(args.start,args.end)
    if args.command == "fred":
        rows = fred_csv(args.series,start,end)
        series="fred:"+args.series
        count = len(rows) if args.dry_run else store_fred(args.series,rows,db)
    elif args.command == "worldbank":
        raw = args.file.read_bytes() if args.file else https_bytes(WB_URL)
        if args.list_columns:
            return {"columns":wb_xlsx(None,start,end,raw=raw),
                    "message":"Pick exact column/commodity/unit before import"}
        if not (args.column and args.commodity and args.unit and args.series_id):
            parser.error("worldbank import requires --column --commodity --unit --series-id")
        rows = wb_xlsx(args.column,start,end,raw=raw)
        series=args.series_id
        count = len(rows) if args.dry_run else store_wb(
            series,args.commodity,args.column,args.unit,rows,db)
    else:
        key=os.getenv("EIA_API_KEY")
        rows=market_sources.eia_spot(args.series,start=start,end=end,key=key)
        series="eia:"+args.series
        if not rows:
            raise ValueError("No EIA prices in the given interval")
        c,b,currency,unit=market_sources.EIA_SERIES[args.series]
        if args.dry_run:
            count=len(rows)
        else:
            market_data.initialize(db)
            count=market_data.save_prices(series,c,"eia",b,"daily",currency,unit,rows,db)
    return {"series":series,"records":count,"first_date":rows[0][0],
            "last_date":rows[-1][0],"dry_run":args.dry_run,
            "coverage_note":"Source dates; never forward-filled. No assumed price impact."}


if __name__ == "__main__":
    try:
        result = cli()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, UnicodeError) as exc:
        # Do not echo API request URLs or environment keys in terminal output.
        print(f"Price backfill rejected: {type(exc).__name__}. Check dates, source response and authorization.",file=sys.stderr)
        raise SystemExit(1)
