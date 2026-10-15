"""P3 fully offline event-date/market-price analysis regression suite."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_events import fixture
import event_data
import event_price_study as study
import geoeconomics as geo
import market_data
import server


def trading_days(start,end):
    current=date.fromisoformat(start)
    finish=date.fromisoformat(end)
    rows=[]
    while current<=finish:
        if current.weekday()<5:
            rows.append(current.isoformat())
        current+=timedelta(days=1)
    return rows


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"geoeconomic.sqlite3"
        payload=fixture()
        # Synthetic evidence, NOT a real embargo: event effective Saturday.
        payload["events"][0]["links"][0]["commodity_id"]="oil"
        payload["events"][0]["occurred_at"]="2022-02-24"
        payload["events"][0]["announced_at"]="2022-02-25"
        payload["events"][0]["effective_at"]="2022-02-26"
        payload["events"][0]["published_at"]="2022-02-23"
        event_data.import_batch(payload,self.path)
        days=trading_days("2021-10-01","2022-06-30")
        self.rows=[(day,100.0+idx*0.35) for idx,day in enumerate(days)]
        market_data.save_prices("eia:RBRTE","oil","eia","Brent","daily",
                                "USD","barrel",self.rows,self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self,**opts):
        return study.study("fixture-event-2026-01","eia:RBRTE",path=self.path,**opts)

    def test_policy_effective_date_and_weekend_align(self):
        r=self.call()
        self.assertEqual(r["status"],"ok")
        self.assertEqual(r["anchor_field"],"effective_at")
        self.assertEqual(r["event_date"],"2022-02-26")
        self.assertEqual(r["event_trading_date"],"2022-02-28")
        self.assertEqual(r["baseline_date"],"2022-02-25")
        self.assertEqual(r["points"][0]["offset"],-20)
        self.assertEqual(r["points"][-1]["offset"],20)
        self.assertEqual(r["returns"]["20"]["trading_offset"],20)
        self.assertEqual(r["window_unit"],"observed_trading_sessions")
        expected=(dict(self.rows)["2022-02-28"]/dict(self.rows)["2022-02-25"]-1)*100
        self.assertAlmostEqual(r["returns"]["0"]["change_pct"],expected,places=5)
        self.assertTrue(r["event"]["editor_reviewed"])

    def test_publication_date_is_not_silently_effective_date(self):
        r=self.call(basis="published")
        self.assertEqual(r["anchor_field"],"published_at")
        self.assertEqual(r["event_date"],"2022-02-23")
        self.assertEqual(r["event_trading_date"],"2022-02-23")
        r2=self.call(basis="occurred")
        self.assertEqual(r2["event_trading_date"],"2022-02-24")

    def test_timefield_fallback_reported(self):
        with geo.connect(self.path) as c:
            c.execute("UPDATE events SET effective_at=NULL WHERE id='fixture-event-2026-01'")
        r=self.call()
        self.assertEqual(r["anchor_field"],"announced_at")
        self.assertTrue(r["date_fallback"])
        self.assertTrue(any("fallback" in x.lower() for x in r["warnings"]))

    def test_insufficient_window_returns_null_never_imputes(self):
        with geo.connect(self.path) as c:
            c.execute("DELETE FROM price_observations WHERE observed_at>'2022-03-02'")
        r=self.call()
        self.assertEqual(r["status"],"ok")
        self.assertIsNone(r["returns"]["5"])
        self.assertIsNone(r["returns"]["20"])
        self.assertLess(len(r["points"]),41)

    def test_stale_quote_outside_alignment_tolerance_fails(self):
        with geo.connect(self.path) as c:
            c.execute("""DELETE FROM price_observations WHERE
                         observed_at>='2022-02-20' AND observed_at<='2022-03-09'""")
        r=self.call()
        self.assertEqual(r["status"],"insufficient_data")
        self.assertEqual(r["points"],[])
        self.assertEqual(r["returns"],{})

    def test_long_gap_after_event_does_not_masquerade_as_next_trading_day(self):
        with geo.connect(self.path) as c:
            c.execute("""DELETE FROM price_observations
                WHERE observed_at>='2022-03-01' AND observed_at<='2022-03-19'""")
        result=self.call()
        self.assertEqual(result["status"],"ok")
        self.assertIsNone(result["returns"]["1"])
        self.assertIsNone(result["returns"]["5"])
        self.assertEqual(max(p["offset"] for p in result["points"]),0)

    def test_no_baseline_fails_without_synthetic_close(self):
        with geo.connect(self.path) as c:
            c.execute("DELETE FROM price_observations WHERE observed_at<'2022-02-26'")
        r=self.call()
        self.assertEqual(r["status"],"insufficient_data")

    def test_monthly_cannot_produce_daily_reaction(self):
        market_data.save_prices("wb:oil","oil","pink-sheet","World Bank crude",
                "monthly","USD","barrel",[("2022-02-01",99.0),("2022-03-01",110.0)],self.path)
        r=study.study("fixture-event-2026-01","wb:oil",path=self.path)
        self.assertEqual(r["status"],"unsupported_frequency")
        self.assertEqual(r["points"],[])
        self.assertIn("monthly",r["reason"].lower())

    def test_reject_unknown_series_unlinked_country_bad_window(self):
        with self.assertRaises(study.MissingStudyEntity):
            study.study("not-present","eia:RBRTE",path=self.path)
        with self.assertRaises(study.MissingStudyEntity):
            study.study("fixture-event-2026-01","nonexistent",path=self.path)
        market_data.save_prices("eia:RWTC","wheat","eia","Wheat-fixture",
            "daily","USD","barrel",[("2022-02-24",1),("2022-02-28",2)],self.path)
        with self.assertRaises(ValueError):
            study.study("fixture-event-2026-01","eia:RWTC",path=self.path)
        with self.assertRaises(ValueError):
            self.call(window=45)
        with self.assertRaises(ValueError):
            self.call(basis="nothing")

    def test_country_comparison_excludes_unlinked_and_returns_ordered(self):
        result=study.compare(country="UKR",series_id="eia:RBRTE",path=self.path)
        self.assertEqual(result["count"],1)
        self.assertEqual(result["events"][0]["event"]["id"],"fixture-event-2026-01")
        self.assertEqual(study.compare(country="SDN",series_id="eia:RBRTE",path=self.path)["count"],0)
        with self.assertRaises(ValueError):
            study.compare(country="UKR",series_id="eia:RBRTE",limit=100,path=self.path)

    def test_study_does_not_modify_sqlite(self):
        before=geo.status(self.path)
        self.call()
        self.assertEqual(before,geo.status(self.path))


class AnalysisApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=server.ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join()

    def get(self,path):
        with urllib.request.urlopen(self.base+path,timeout=4) as response:
            return response.status,json.load(response)

    def test_study_api_ok(self):
        with patch.object(server.event_price_study,"study",
                return_value={"status":"insufficient_data","returns":{},"points":[]}) as called:
            status,body=self.get("/api/analysis/event-study?event_id=a&series=b&window=20")
        self.assertEqual(status,200)
        self.assertEqual(body["status"],"insufficient_data")
        called.assert_called_once_with("a","b",basis="impact",window=20)

    def test_comparison_api_ok(self):
        with patch.object(server.event_price_study,"compare",return_value={"events":[]}) as called:
            status,result=self.get("/api/analysis/compare?country=UKR&series=eia%3ARBRTE&basis=effective&limit=3")
        self.assertEqual(status,200)
        called.assert_called_once_with(country="UKR",series_id="eia:RBRTE",
                                       basis="effective",window=20,limit=3)

    def test_bad_requests_fail_closed(self):
        for path in (
          "/api/analysis/event-study?event_id=a&series=b&window=999",
          "/api/analysis/event-study?event_id=a&series=b&basis=bogus",
          "/api/analysis/compare?country=UKR&series=s&limit=xx",
          "/api/analysis/event-study?event_id=a&event_id=b&series=c"
        ):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    self.get(path)
                self.assertEqual(raised.exception.code,400)

    def test_unknown_entities_404(self):
        with patch.object(server.event_price_study,"study",side_effect=study.MissingStudyEntity("missing")):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.get("/api/analysis/event-study?event_id=missing&series=s")
        self.assertEqual(raised.exception.code,404)

    def test_unavailable_db_503(self):
        import sqlite3
        with patch.object(server.event_price_study,"study",side_effect=sqlite3.OperationalError("missing database")):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.get("/api/analysis/event-study?event_id=a&series=b")
        self.assertEqual(raised.exception.code,503)


if __name__=="__main__":
    unittest.main()
