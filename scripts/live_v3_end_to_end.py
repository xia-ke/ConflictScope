#!/usr/bin/env python3
"""Non-destructive real-data V3 integration smoke on an ephemeral SQLite DB.

Uses public FRED/EIA-origin and World Bank sources, no EIA key required.
Imports only bundled AI-assisted primary-source historical events.
Never includes third-party quote values in report or committed artifacts.
"""
import json
import sys
import tempfile
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import event_data
import event_price_study as studies
import historical_prices as prices
import market_data
import sync_health
import sync_tasks
import geoeconomics as geo

EVENT="rus-2022-12-05-crude-price-cap"


def verify():
    result={"checked_at":datetime.now(timezone.utc).isoformat(),
            "kind":"REAL_MARKET_DATA_DISPOSABLE_DATABASE",
            "committed_data":False,
            "source_authorization":"public reads; redistribution still subject to original terms",
            "stages":[]}
    stage="initialize"
    try:
        with tempfile.TemporaryDirectory(prefix="conflictscope-v3-acceptance-") as tmp:
            db=Path(tmp)/"v3.sqlite3"
            market_data.initialize(db)
            payload=json.loads((ROOT/"data"/"official-events-2022-2024.json").read_text(encoding="utf-8"))
            imp=event_data.import_batch(payload,db)
            result["stages"].append({"stage":"official_event_import","status":"passed",
                                     "events":imp["inserted"]})
            stage="fred_brent_history"
            for symbol in ("DCOILBRENTEU","DCOILWTICO","PWHEAMTUSDM","PCOPPUSDM"):
                frequency="daily" if symbol.startswith("DCOIL") else "monthly"
                rows=prices.fred_csv(symbol,"2021-01-01","2025-12-31")
                prices.store_fred(symbol,rows,db)
                result["stages"].append({"stage":"fred_"+symbol,"status":"passed",
                      "frequency":frequency,"observations":len(rows),
                      "first_date":rows[0][0],"last_date":rows[-1][0]})
            stage="worldbank_2022_copper"
            wb=prices.wb_xlsx("Copper","2022-01-01","2022-12-31")
            prices.store_wb("wb:copper","copper","Copper","metric ton",wb,db)
            result["stages"].append({"stage":stage,"status":"passed",
                                     "observations":len(wb)})
            stage="event_market_model"
            doc=studies.study(EVENT,"fred:DCOILBRENTEU",control_series="fred:DCOILWTICO",
                              path=db,window=20)
            if doc["status"]!="ok" or doc["market_model"]["status"]!="ok":
                raise RuntimeError("P3 real-market event study does not have full coverage")
            result["stages"].append({"stage":stage,"status":"passed",
                   "event_id":EVENT,"event_date":doc["event_date"],
                   "baseline_date":doc["baseline_date"],
                   "event_trading_date":doc["event_trading_date"],
                   "ols_estimation_count":doc["market_model"]["estimation_count"],
                   "post_horizons_available":sum(v is not None for v in doc["returns"].values())})
            stage="p4_real_update_cycle"
            plan=Path(tmp)/"sync-plan.json"
            plan.write_text(json.dumps({"version":1,"jobs":[
                {"id":"fred-brent","provider":"fred","series":"DCOILBRENTEU","days_back":45},
                {"id":"fred-wti","provider":"fred","series":"DCOILWTICO","days_back":45},
                {"id":"official-events-watch","provider":"event-watch",
                 "file":"data/official-events-2022-2024.json"}
            ]}),encoding="utf-8")
            sync=sync_tasks.run_once(plan,path=db,retries=1)
            if sync["failed"]:
                raise RuntimeError("A real P4 update source failed")
            health=sync_health.report(db)
            if health["unfinished_attempts"] or not all(j["status"]=="success" for j in health["jobs"]):
                raise RuntimeError("P4 health report inconsistent with successful run")
            result["stages"].append({"stage":stage,"status":"passed",
                                     "jobs":len(sync["jobs"]),
                                     "failed":sync["failed"],
                                     "alerts":len(health["alerts"])})
            stage="db_integrity"
            with geo.connect(db,readonly=True) as conn:
                foreign_keys=conn.execute("PRAGMA foreign_key_check").fetchall()
                integrity=conn.execute("PRAGMA integrity_check").fetchone()[0]
            if foreign_keys or integrity!="ok":
                raise RuntimeError("SQLite integrity or FK check failed")
            result["stages"].append({"stage":stage,"status":"passed"})
    except Exception as exc:
        result["status"]="blocked"
        result["error_stage"]=stage
        result["error_type"]=type(exc).__name__
        result["note"]="Only error category is exposed; no URLs with secrets or prices are logged."
        return result
    result["status"]="passed"
    result["note"]="Real public market sources passed, but Comtrade and EIA key-gated paths require separate acceptance."
    return result


def main():
    report=verify()
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 0 if report["status"]=="passed" else 1


if __name__=="__main__":
    raise SystemExit(main())
