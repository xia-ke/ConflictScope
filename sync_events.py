#!/usr/bin/env python3
"""Offline P2 event ledger: validate/import an explicitly reviewed JSON batch."""
import argparse
import json
import sys
from pathlib import Path

import event_data
import geoeconomics as geo


def read_batch(path):
    if path.stat().st_size > 2_000_000:
        raise ValueError("JSON batch larger than 2 MB")
    return json.loads(path.read_text(encoding="utf-8"))


def cli(argv=None):
    parser = argparse.ArgumentParser(description="ConflictScope manually reviewed event ledger")
    parser.add_argument("command", choices=("validate", "import", "list"))
    parser.add_argument("--file", type=Path, help="Reviewed JSON event batch")
    parser.add_argument("--db", type=Path, default=geo.DEFAULT_DB)
    parser.add_argument("--country", help="ISO3 country for list")
    parser.add_argument("--commodity", help="Commodity ID for list")
    parser.add_argument("--basis", choices=tuple(sorted(event_data.BASIS)), default="impact")
    args = parser.parse_args(argv)
    if args.command in ("validate", "import") and args.file is None:
        parser.error("validate/import require --file")
    if args.command == "validate":
        sources, rows = event_data.validate_batch(read_batch(args.file))
        return {"valid": True, "source_count":len(sources),
                "reviewed_events":len(rows),"network_access":False}
    if args.command == "import":
        return event_data.import_batch(read_batch(args.file),args.db)
    if not args.country and not args.commodity:
        parser.error("list requires --country or --commodity")
    return event_data.timeline(country=args.country, commodity=args.commodity,
                               basis=args.basis, path=args.db)


if __name__ == "__main__":
    try:
        print(json.dumps(cli(),ensure_ascii=False,indent=2))
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"Event batch rejected: {type(exc).__name__}: {exc}",file=sys.stderr)
        raise SystemExit(1)
