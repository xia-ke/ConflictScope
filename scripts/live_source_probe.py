#!/usr/bin/env python3
"""Bounded, non-mutating public source contract probes for release acceptance.

No API keys or market data are logged. Nonzero exit in --strict mode means at
least one public source has NOT been verified in this environment. For provider
usage/redistribution consult original terms independently.
"""
import argparse
import json
import os
import sys
import urllib.parse
from pathlib import Path
from datetime import datetime,timezone

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import historical_prices as history
import market_sources as market


def probe(name,call, *, expected=True):
    try:
        rows=call()
        return {"source":name,"status":"passed","count":len(rows),
                "sample_start":rows[0][0] if rows and isinstance(rows[0],tuple) else None,
                "sample_end":rows[-1][0] if rows and isinstance(rows[-1],tuple) else None}
    except Exception as exc:
        # Deliberately print only the exception class to prevent leaking API
        # URLs containing api_key= or any inadvertently sensitive messages.
        return {"source":name,"status":"failed","error_type":type(exc).__name__}


def comtrade_classification_probe(classification):
    if classification not in ("H5","H6","HS"):
        raise ValueError("Invalid classification probe")
    base="https://comtradeapi.un.org/public/v1/preview/C/A/"+classification
    query=urllib.parse.urlencode({
        "reporterCode":804,"period":2024,"partnerCode":0,
        "cmdCode":"100199","flowCode":"X","maxRecords":10
    })
    body=market.fetch_json(base+"?"+query)
    data=body.get("data")
    if not isinstance(data,list):
        raise ValueError("Unexpected Comtrade diagnostic format")
    return data


def check():
    checks=[
        probe("fred_brent_2022_daily",lambda:history.fred_csv(
            "DCOILBRENTEU","2022-12-01","2022-12-16")),
        probe("fred_wheat_2022_monthly",lambda:history.fred_csv(
            "PWHEAMTUSDM","2022-06-01","2022-12-31")),
        probe("worldbank_copper_2022_monthly",lambda:history.wb_xlsx(
            "Copper","2022-01-01","2022-12-31")),
        probe("uncomtrade_ukraine_wheat_export_2024",lambda:market.comtrade(
            "UKR",2024,commodity="wheat",flow="export")),
        probe("uncomtrade_H6_preview_2024",lambda:comtrade_classification_probe("H6")),
        probe("uncomtrade_HS_preview_2024",lambda:comtrade_classification_probe("HS"))
    ]
    # Distinguish an empty supplier response from provider verification success.
    for item in checks:
        if item["status"]=="passed" and item["count"]<=0:
            item["status"]="failed"
            item["error_type"]="EmptyResponse"
    if os.getenv("EIA_API_KEY"):
        item=probe("eia_brent_2022_with_key",lambda:market.eia_spot(
            "RBRTE",start="2022-12-01",end="2022-12-16",key=os.environ["EIA_API_KEY"]))
        if item["status"]=="passed" and item["count"]<=0:
            item["status"]="failed"
            item["error_type"]="EmptyResponse"
        checks.append(item)
    else:
        checks.append({"source":"eia_brent_2022_with_key","status":"skipped",
                       "reason":"EIA_API_KEY not provided; official endpoint requires a key"})
    return {"checked_at":datetime.now(timezone.utc).isoformat(),
            "kind":"REAL_SOURCE_CONNECTIVITY_ONLY",
            "database_modified":False,
            "market_data_exported":False,
            "results":checks,
            "passed":sum(c["status"]=="passed" for c in checks),
            "failed":sum(c["status"]=="failed" for c in checks),
            "skipped":sum(c["status"]=="skipped" for c in checks),
            "warning":"Passing GET/parse does not establish data licensing, statistical coverage or published-data accuracy."}


def main(argv=None):
    cli=argparse.ArgumentParser(description="ConflictScope live-source acceptance probe")
    cli.add_argument("--strict",action="store_true",
                     help="Exit 1 when any attempted public source is unavailable")
    args=cli.parse_args(argv)
    data=check()
    print(json.dumps(data,ensure_ascii=False,indent=2))
    return 1 if args.strict and data["failed"] else 0


if __name__=="__main__":
    raise SystemExit(main())
