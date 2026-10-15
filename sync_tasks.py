#!/usr/bin/env python3
"""ConflictScope P4 explicit synchronization runner; designed for external cron/Task Scheduler.

No network access from GET handlers. Read-only configuration cannot execute shell
commands. Jobs use an allowlisted provider, attempt audit, bounded retries,
idempotent transactional writes, and a single-writer SQLite lease.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
from datetime import date, timedelta
from pathlib import Path

import event_data
import geoeconomics as geo
import historical_prices as history
import market_data
import market_sources
import sync_health as health

ROOT=Path(__file__).resolve().parent
DEFAULT_PLAN=ROOT/"data"/"sync-plan.example.json"
MAX_PLAN_BYTES=40_000
JOB_TYPES={"fred","eia","worldbank","event-watch"}


def read_plan(path):
    path=Path(path)
    if path.stat().st_size>MAX_PLAN_BYTES:
        raise ValueError("Sync configuration too large")
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict) or value.get("version")!=1 or not isinstance(value.get("jobs"),list):
        raise ValueError("Expected version=1 and jobs array")
    if not 1<=len(value["jobs"])<=20:
        raise ValueError("1-20 jobs required")
    ids=set()
    jobs=[]
    for row in value["jobs"]:
        if not isinstance(row,dict):
            raise ValueError("Each job must be a JSON object")
        jid=health._job_id(row.get("id"))
        if jid in ids:
            raise ValueError("Duplicate job identifier")
        ids.add(jid)
        provider=row.get("provider")
        if provider not in JOB_TYPES:
            raise ValueError("Unsupported provider, no command execution allowed")
        expected={
            "fred":{"id","provider","series","days_back"},
            "eia":{"id","provider","series","days_back"},
            "worldbank":{"id","provider","column","commodity","unit","series_id","days_back"},
            "event-watch":{"id","provider","file"},
        }[provider]
        if set(row)!=expected:
            raise ValueError("Job configuration requires documented exact fields")
        if provider=="fred" and row["series"] not in history.FRED:
            raise ValueError("Non-curated FRED symbol")
        if provider=="eia" and row["series"] not in market_sources.EIA_SERIES:
            raise ValueError("Non-curated EIA series")
        if provider=="worldbank":
            if row["commodity"] not in market_sources.COMMODITIES or not (
                    isinstance(row["column"],str) and 1<=len(row["column"])<=120
                    and isinstance(row["unit"],str) and 1<=len(row["unit"])<=80
                    and isinstance(row["series_id"],str)
                    and re.fullmatch(r"wb:[a-z0-9._:-]{2,80}",row["series_id"])):
                raise ValueError("Invalid World Bank commodity mapping")
        if provider!="event-watch":
            if type(row["days_back"]) is not int or not 2<=row["days_back"]<=365:
                raise ValueError("days_back must be between 2 and 365")
        else:
            if not isinstance(row["file"],str) or not row["file"].endswith(".json"):
                raise ValueError("Only curated JSON evidence sources can be watched")
            target=(ROOT/row["file"]).resolve()
            if not target.is_relative_to((ROOT/"data").resolve()):
                raise ValueError("Event-watch files must be under the project's data folder")
        jobs.append(row)
    return jobs


def _perform(job, db, *, today):
    """Fetch first, commit only after all source rows validate."""
    kind=job["provider"]
    if kind=="event-watch":
        target=(ROOT/job["file"]).resolve()
        if target.stat().st_size>2_000_000:
            raise ValueError("Reviewed source batch exceeds 2MB")
        raw=target.read_bytes()
        # Validate structure and proof references; never auto-approve/re-import.
        event_data.validate_batch(json.loads(raw))
        return {"count":0,"latest":None,"fingerprint":hashlib.sha256(raw).hexdigest(),
                "message":"Source batch checked only; change requires manual review before import"}
    start=(today-timedelta(days=job["days_back"])).isoformat()
    end=today.isoformat()
    if kind=="fred":
        symbol=job["series"]
        rows=history.fred_csv(symbol,start,end)
        digest=health.fingerprint(rows)
        written=history.store_fred(symbol,rows,db)
    elif kind=="eia":
        symbol=job["series"]
        rows=market_sources.eia_spot(symbol,start=start,end=end,key=os.getenv("EIA_API_KEY"))
        if not rows:
            raise ValueError("EIA returned no validated observations")
        digest=health.fingerprint(rows)
        item=market_sources.EIA_SERIES[symbol]
        market_data.initialize(db)
        written=market_data.save_prices("eia:"+symbol,item[0],"eia",
                    item[1],"daily",item[2],item[3],rows,db)
    else:
        rows=history.wb_xlsx(job["column"],start,end)
        digest=health.fingerprint(rows)
        written=history.store_wb(job["series_id"],job["commodity"],
                    job["column"],job["unit"],rows,db)
    if not rows or written<=0:
        raise ValueError("Source did not provide usable observations")
    return {"count":written,"latest":rows[-1][0],
            "fingerprint":digest,"message":"Validated source observations committed to SQLite"}


def _retryable(exc):
    if isinstance(exc,urllib.error.HTTPError):
        return exc.code==429 or 500<=exc.code<=599
    return isinstance(exc,(urllib.error.URLError,TimeoutError,ConnectionError,ConnectionResetError))


def run_once(plan=DEFAULT_PLAN, *, path=geo.DEFAULT_DB, today=None,
             retries=2, sleep=time.sleep):
    """One scheduled pass. Failed jobs do not overwrite previous observed prices."""
    if type(retries)is not int or not 0<=retries<=4:
        raise ValueError("retries must be 0..4")
    jobs=read_plan(plan)
    today=date.fromisoformat(today) if isinstance(today,str) else (today or date.today())
    token=health.lease(path)
    if token is None:
        raise RuntimeError("Another updater already owns the SQLite lease")
    results=[]
    try:
        previous=health.checked(path)
        for job in jobs:
            health.renew(path,token)
            rid=health.begin(job["id"],path)
            item=None
            failure=None
            attempts=0
            for n in range(retries+1):
                attempts=n+1
                try:
                    item=_perform(job,path,today=today)
                    break
                except (OSError,ValueError,KeyError,TypeError,UnicodeError) as exc:
                    failure=type(exc).__name__
                    if n>=retries or not _retryable(exc):
                        break
                    sleep(min(2**n,8))
            if item is not None:
                changed=bool(job["provider"]=="event-watch" and previous.get(job["id"])
                             and previous[job["id"]]!=item["fingerprint"])
                health.finish(rid,job["id"],"success",attempts,item["count"],
                              item["fingerprint"],item["latest"],path=path)
                results.append({"job_id":job["id"],"status":"success",
                                "attempts":attempts,"records":item["count"],
                                "latest_observation":item["latest"],
                                "manual_review_required":changed})
            else:
                health.finish(rid,job["id"],"error",attempts,error_code=failure or "UnknownError",path=path)
                results.append({"job_id":job["id"],"status":"error",
                                "attempts":attempts,"failure_code":failure or "UnknownError"})
        return {"jobs":results,"failed":sum(r["status"]=="error" for r in results),
                "manual_review_required":[r["job_id"] for r in results if r.get("manual_review_required")],
                "note":"Changed reviewed event JSON is never auto-approved or imported."}
    finally:
        health.release(path,token)


def main(argv=None):
    parser=argparse.ArgumentParser(description="ConflictScope P4 market/official-event source synchronization")
    parser.add_argument("--db",type=Path,default=geo.DEFAULT_DB)
    parser.add_argument("--plan",type=Path,default=DEFAULT_PLAN)
    sub=parser.add_subparsers(dest="cmd",required=True)
    p=sub.add_parser("once",help="Run allowlisted tasks once (use cron or Task Scheduler)")
    p.add_argument("--retries",type=int,default=2)
    sub.add_parser("validate",help="Validate plan without network or DB writes")
    sub.add_parser("status",help="Read local source freshness and run history")
    args=parser.parse_args(argv)
    try:
        if args.cmd=="validate":
            result={"valid":True,"jobs":[j["id"] for j in read_plan(args.plan)]}
        elif args.cmd=="status":
            result=health.report(args.db)
        else:
            result=run_once(args.plan,path=args.db,retries=args.retries)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 1 if args.cmd=="once" and result["failed"] else 0
    except (OSError,ValueError,TypeError,RuntimeError,KeyError) as exc:
        # Do not print repr(exc); HTTP errors can contain embedded URL/query secrets.
        print(f"Sync failed ({type(exc).__name__}). See job status for safe error category.",
              file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
