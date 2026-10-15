#!/usr/bin/env python3
"""Explicit market imports: no scheduler, no hidden API fetching on page load."""
import argparse
import json
import os
from pathlib import Path

import geoeconomics as geo
import market_data as data
import market_sources as sources


def cli(argv=None):
    p = argparse.ArgumentParser(description="Sync curated ConflictScope P1 market data")
    p.add_argument("--db", type=Path, default=geo.DEFAULT_DB)
    cmds = p.add_subparsers(dest="command", required=True)
    cmds.add_parser("init", help="Initialize SQLite catalog")
    cmds.add_parser("catalog", help="List available source/commodity metadata")
    trade = cmds.add_parser("comtrade", help="Fetch ONE specified HS6 good, not a complete ranking")
    trade.add_argument("--country", choices=tuple(sources.REPORTERS), required=True)
    trade.add_argument("--year", type=int, required=True)
    trade.add_argument("--commodity", choices=tuple(sources.COMMODITIES), required=True)
    trade.add_argument("--flow", choices=("import","export"), required=True)
    trade.add_argument("--hs-revision", choices=tuple(sources.HS_REVISION_CODES),
                       default="HS2017",
                       help="Explicit source classification; HS2022 currently verified for wheat only")
    energy = cmds.add_parser("eia", help="Fetch Brent or WTI EIA daily spot series")
    energy.add_argument("--series", choices=tuple(sources.EIA_SERIES), required=True)
    energy.add_argument("--start", required=True, help="YYYY-MM-DD")
    energy.add_argument("--end", required=True, help="YYYY-MM-DD")
    pink = cmds.add_parser("pink-sheet", help="Load a local monthly XLSX, exact column required")
    pink.add_argument("--file", type=Path, required=True)
    pink.add_argument("--column", help="Exactly match the official XLSX column header")
    pink.add_argument("--commodity", choices=tuple(sources.COMMODITIES))
    pink.add_argument("--unit", help="Explicit price unit from official XLSX, e.g. metric ton")
    pink.add_argument("--series-id", help="Stable unique series identifier")
    args = p.parse_args(argv)

    if args.command == "init":
        data.initialize(args.db)
        return geo.status(args.db)
    if args.command == "catalog":
        return {"commodities": sources.COMMODITIES, "reporters_m49": sources.REPORTERS,
                "eia_series": sources.EIA_SERIES, "pink_sheet_page": sources.PINK_SHEET_PAGE}
    if args.command == "comtrade":
        rows = sources.comtrade(args.country, args.year, commodity=args.commodity,
                   flow=args.flow, hs_revision=args.hs_revision,
                   key=os.getenv("COMTRADE_API_KEY"))
        if not rows:
            raise ValueError("Authenticated Comtrade final data produced no verified trade observation")
        data.initialize(args.db)
        return {"status":"imported","imported":data.save_trade(rows,args.db),"reporter":args.country,
                "hs_code":sources.COMMODITIES[args.commodity][2],
                "coverage":"one_HS6_world_partner","hs_revision":args.hs_revision,
                "access_mode":"authenticated_final",
                "redistribution_permission":"deployment_specific_review_required"}
    if args.command == "eia":
        key = os.getenv("EIA_API_KEY")
        rows = sources.eia_spot(args.series,start=args.start,end=args.end,key=key)
        if not rows:
            return {"status":"empty","imported":0}
        data.initialize(args.db)
        commodity, benchmark, currency, unit = sources.EIA_SERIES[args.series]
        count = data.save_prices("eia:"+args.series,commodity,"eia",benchmark,"daily",
                                 currency,unit,rows,args.db)
        return {"status":"imported","imported":count,"series":"eia:"+args.series}
    if args.command == "pink-sheet":
        raw = args.file.read_bytes()
        if not args.column:
            return {"available_columns":sources.pink_sheet_columns(raw),
                    "message":"Choose exact --column, --commodity, --unit and --series-id"}
        if not args.commodity or not args.unit or not args.series_id:
            p.error("pink-sheet import requires --commodity --unit --series-id")
        if not args.series_id.startswith("wb:"):
            p.error("Pink Sheet series-id must start with wb:")
        rows = sources.pink_sheet_rows(raw,args.column)
        data.initialize(args.db)
        count = data.save_prices(args.series_id,args.commodity,"pink-sheet",
                         args.column,"monthly","USD",args.unit,rows,args.db)
        return {"status":"imported","imported":count,"series":args.series_id,
                "source":"World Bank Pink Sheet","note":"Confirm unit from workbook; series is not a futures quote"}


if __name__ == "__main__":
    try:
        print(json.dumps(cli(), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError) as exc:
        # Avoid embedding request URLs or API keys in user-visible errors.
        raise SystemExit(f"Market data import failed ({type(exc).__name__}). Check input/source availability.")
