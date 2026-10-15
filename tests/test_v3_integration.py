"""Full V3 integration acceptance against a disposable SQLite database.

Price observations and Comtrade totals are SYNTHETIC FIXTURES. Historical event
records are repository-bundled AI-assisted official-source research, not certified
human-reviewed events. All API requests are local; all supplier fetches are
mocked at the HTTPS boundary. No network access or durable user DB changes.
"""
import csv
import io
import json
import math
import sys
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from contextlib import ExitStack
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import event_data
import event_price_study
import geoeconomics as geo
import historical_prices as history
import market_data
import market_sources
import server
import sync_health
import sync_tasks

EVENTS=ROOT/"data"/"official-events-2022-2024.json"
OIL_EVENT="rus-2022-12-05-crude-price-cap"
WHEAT_EVENT="ukr-2023-07-17-grain-initiative-ends"


def fake_fred_csv(symbol, *, start="2022-01-01", end="2023-12-31"):
    out=io.StringIO()
    writer=csv.writer(out)
    writer.writerow(["DATE",symbol])
    day=date.fromisoformat(start)
    finish=date.fromisoformat(end)
    n=0
    while day<=finish:
        if day.weekday()<5:
            # Only synthetic prices; use different factors for benchmark regression.
            number=(95+0.028*n+1.7*math.sin(n/7)
                    if symbol=="DCOILBRENTEU" else 84+0.022*n+2.1*math.sin(n/5))
            writer.writerow([day.isoformat(),f"{number:.6f}"])
            n+=1
        day+=timedelta(days=1)
    return out.getvalue().encode("utf-8")


def fake_wb_xlsx():
    uri="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    uri_r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    wb=(f'<workbook xmlns="{uri}" xmlns:r="{uri_r}"><sheets>'
        '<sheet name="Monthly Prices" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rel=('''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Target="worksheets/sheet1.xml"
      Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
      </Relationships>''')
    rows=['<row r="1"><c r="A1" t="inlineStr"><is><t>Month</t></is></c>'
          '<c r="B1" t="inlineStr"><is><t>Copper</t></is></c></row>']
    for k in range(1,13):
        rows.append(f'<row r="{k+1}"><c r="A{k+1}" t="inlineStr"><is>'
                    f'<t>2022M{k:02d}</t></is></c><c r="B{k+1}"><v>{8000+k*42}</v></c></row>')
    worksheet=f'<worksheet xmlns="{uri}"><sheetData>{"".join(rows)}</sheetData></worksheet>'
    out=io.BytesIO()
    with zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED) as arc:
        arc.writestr("xl/workbook.xml",wb)
        arc.writestr("xl/_rels/workbook.xml.rels",rel)
        arc.writestr("xl/worksheets/sheet1.xml",worksheet)
    return out.getvalue()


class V3Integration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.db=Path(cls.tmp.name)/"integration.sqlite3"
        cls.fixture_kind="SYNTHETIC_PRICE_DATA"
        market_data.initialize(cls.db)
        batch=json.loads(EVENTS.read_text(encoding="utf-8"))
        assert len(batch["events"])==11
        cls.event_result=event_data.import_batch(batch,cls.db)
        cls.reimport_result=event_data.import_batch(batch,cls.db)

        for symbol in ("DCOILBRENTEU","DCOILWTICO"):
            raw=fake_fred_csv(symbol)
            rows=history.fred_csv(symbol,"2022-01-01","2023-12-31",
                                  loader=lambda url,raw=raw:raw)
            history.store_fred(symbol,rows,cls.db)

        # Include monthly IMF wheat and official-workbook-style copper prices.
        monthly=("DATE,PWHEAMTUSDM\n"+
                 "".join(f"2022-{m:02d}-01,{270+m*2}\n" for m in range(1,13))).encode("utf-8")
        wheat=history.fred_csv("PWHEAMTUSDM","2022-01-01","2022-12-31",
                               loader=lambda url:monthly)
        history.store_fred("PWHEAMTUSDM",wheat,cls.db)
        copper=history.wb_xlsx("Copper","2022-01-01","2022-12-31",
                               raw=fake_wb_xlsx())
        history.store_wb("wb:copper","copper","Copper","metric ton",copper,cls.db)

        comtrade={"count":1,"data":[{
            "reporterCode":804,"period":"2024","partnerCode":0,
            "partner2Code":0,"motCode":0,"customsCode":"C00",
            "cmdCode":"100199","classificationCode":"H5","flowCode":"X",
            "primaryValue":123456.0,"netWgt":67000
        }]}
        flows=market_sources.comtrade("UKR",2024,commodity="wheat",flow="export",
                                      loader=lambda url:comtrade)
        market_data.save_trade(flows,cls.db)

        # Execute a real P4 orchestration cycle but intercept all source I/O.
        plan=Path(cls.tmp.name)/"plan.json"
        plan.write_text(json.dumps({"version":1,"jobs":[
            {"id":"brent-monitor","provider":"fred",
             "series":"DCOILBRENTEU","days_back":45}
        ]}),encoding="utf-8")
        with patch.object(sync_tasks.history,"fred_csv",
                          return_value=[("2026-10-13",91.4),("2026-10-14",92.1)]):
            cls.sync_result=sync_tasks.run_once(plan,path=cls.db,today="2026-10-15",
                                                retries=0)

        # Route real HTTP GET handlers to disposable SQLite, not developer DB.
        cls.patches=ExitStack()
        for mod,method in (
            (geo,"status"),(market_data,"commodity_catalog"),
            (market_data,"trade_by_country"),(market_data,"price_series_list"),
            (market_data,"price_history"),(event_data,"timeline"),
            (event_price_study,"study"),(event_price_study,"compare"),
            (sync_health,"report")
        ):
            original=getattr(mod,method)
            def routed(*args,_orig=original,**kwargs):
                kwargs["path"]=cls.db
                return _orig(*args,**kwargs)
            cls.patches.enter_context(patch.object(mod,method,side_effect=routed))
        cls.server=server.ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        cls.worker=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.worker.start()
        cls.base=f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.worker.join()
        cls.patches.close();cls.tmp.cleanup()

    def get(self,uri):
        with urllib.request.urlopen(self.base+uri,timeout=6) as rsp:
            self.assertEqual(rsp.status,200)
            return json.load(rsp)

    def test_p0_through_p2_provenance_accepted_without_duplicate(self):
        self.assertEqual(self.event_result["inserted"],11)
        self.assertEqual(self.reimport_result["unchanged"],11)
        overview=self.get("/api/market/status")
        self.assertEqual(overview["schema_version"],3)
        self.assertEqual(overview["tables"]["events"],11)
        self.assertEqual(overview["tables"]["event_reviews"],11)
        self.assertEqual(overview["tables"]["trade_flows"],1)
        result=self.get("/api/timeline?country=RUS&basis=impact")
        self.assertGreaterEqual(result["count"],3)
        oil=next(x for x in result["events"] if x["id"]==OIL_EVENT)
        self.assertEqual(oil["event_type"],"sanction")
        self.assertEqual(oil["effective_at"],"2022-12-05")
        self.assertTrue(oil["research_ai_assisted"])
        self.assertFalse(oil["editor_reviewed"])
        self.assertTrue(any(x["evidence_role"]=="primary" for x in oil["evidence"]))

    def test_p1_curated_trade_vs_monthly_and_daily_market(self):
        trade=self.get("/api/trade/UKR?year=2024&flow=export")
        self.assertEqual(trade["scope"],"selected_hs6_world_partner_only")
        self.assertTrue(trade["not_total_trade_ranking"])
        self.assertEqual(trade["rows"][0]["value_usd"],123456.0)
        series=self.get("/api/prices")["series"]
        ids={item["id"]:item for item in series}
        self.assertEqual(ids["fred:DCOILBRENTEU"]["frequency"],"daily")
        self.assertEqual(ids["fred:PWHEAMTUSDM"]["frequency"],"monthly")
        self.assertEqual(ids["wb:copper"]["frequency"],"monthly")
        source=self.get("/api/prices/fred:DCOILBRENTEU?start=2022-12-01&end=2022-12-08")
        self.assertTrue(any(x["observed_at"]=="2022-12-05" for x in source["observations"]))

    def test_p3_event_study_and_comparison_with_optional_control(self):
        url=(f"/api/analysis/event-study?event_id={OIL_EVENT}&series=fred:DCOILBRENTEU"
             "&control=fred:DCOILWTICO&window=20&basis=impact")
        analysis=self.get(url)
        self.assertEqual(analysis["status"],"ok")
        self.assertEqual(analysis["anchor_field"],"effective_at")
        self.assertEqual(analysis["event_trading_date"],"2022-12-05")
        self.assertEqual(analysis["baseline_date"],"2022-12-02")
        self.assertEqual(analysis["window_unit"],"observed_trading_sessions")
        self.assertEqual(analysis["event"]["research_ai_assisted"],True)
        self.assertEqual(analysis["market_model"]["status"],"ok")
        self.assertGreaterEqual(analysis["market_model"]["estimation_count"],30)
        other=self.get("/api/analysis/compare?country=RUS&series=fred:DCOILBRENTEU")
        self.assertTrue(any(x["event"]["id"]==OIL_EVENT for x in other["events"]))
        monthly=event_price_study.study(WHEAT_EVENT,"fred:PWHEAMTUSDM",path=self.db)
        self.assertEqual(monthly["status"],"unsupported_frequency")

    def test_p4_sync_audit_health_and_retry_ledger(self):
        self.assertEqual(self.sync_result["failed"],0)
        health=self.get("/api/sync/status")
        run=next(x for x in health["jobs"] if x["job_id"]=="brent-monitor")
        self.assertEqual(run["status"],"success")
        self.assertEqual(run["latest_observation"],"2026-10-14")
        self.assertEqual(health["unfinished_attempts"],0)
        self.assertTrue(any(x["job_id"]=="brent-monitor" and x["status"]=="success"
                            for x in health["recent_runs"]))

    def test_readonly_apis_did_not_mutate_market_data(self):
        before=geo.status(self.db)["tables"].copy()
        for _ in range(2):
            self.get("/api/prices")
            self.get("/api/timeline?country=RUS")
            self.get("/api/sync/status")
        after=geo.status(self.db)["tables"]
        self.assertEqual(before,after)


if __name__=="__main__":
    unittest.main()
