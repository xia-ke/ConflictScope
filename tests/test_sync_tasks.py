"""P4 synchronization lifecycle tests — all external fetches mocked."""
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import geoeconomics as geo
import market_data
import sync_health as health
import sync_tasks
import server

TODAY="2026-10-15"


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Path(self.temp.name)/"data.sqlite3"
        self.plan=Path(self.temp.name)/"tasks.json"

    def tearDown(self):
        self.temp.cleanup()

    def write_plan(self,jobs):
        self.plan.write_text(json.dumps({"version":1,"jobs":jobs}),encoding="utf-8")

    def fred_job(self):
        return {"id":"brent","provider":"fred","series":"DCOILBRENTEU","days_back":45}

    def test_validate_allowlist_rejects_commands_and_invalid_parameters(self):
        for jobs in (
          [{"id":"a1","provider":"shell","command":"rm -rf /"}],
          [{**self.fred_job(),"series":"FAKE"}],
          [{**self.fred_job(),"days_back":9999}],
          [self.fred_job(),self.fred_job()]
        ):
            self.write_plan(jobs)
            with self.assertRaises(ValueError):
                sync_tasks.read_plan(self.plan)
        self.write_plan([self.fred_job()])
        self.assertEqual(len(sync_tasks.read_plan(self.plan)),1)
        self.assertFalse(self.db.exists())

    def test_success_retry_and_idempotent_pricing(self):
        self.write_plan([self.fred_job()])
        values=[("2026-10-13",90.3),("2026-10-14",91.3)]
        calls=0
        def fetch(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==1:
                raise urllib.error.URLError("simulated transient network failure")
            return values
        with patch.object(sync_tasks.history,"fred_csv",side_effect=fetch):
            first=sync_tasks.run_once(self.plan,path=self.db,today=TODAY,retries=2,sleep=lambda s:None)
        self.assertEqual(first["failed"],0)
        self.assertEqual(first["jobs"][0]["attempts"],2)
        self.assertEqual(calls,2)
        with patch.object(sync_tasks.history,"fred_csv",return_value=values):
            second=sync_tasks.run_once(self.plan,path=self.db,today=TODAY,retries=0)
        self.assertEqual(second["failed"],0)
        self.assertEqual(geo.status(self.db)["tables"]["price_observations"],2)
        report=health.report(self.db,today=TODAY)
        self.assertEqual(report["jobs"][0]["health"],"current_update")
        self.assertEqual(report["jobs"][0]["failure_count"],0)
        self.assertEqual(report["price_series"][0]["health"],"current_observations")
        self.assertEqual(report["price_revision_count"],0)
        self.assertEqual(len(report["recent_runs"]),2)

    def test_failure_is_recorded_without_deleting_prior_prices(self):
        self.write_plan([self.fred_job()])
        with patch.object(sync_tasks.history,"fred_csv",return_value=[("2026-10-14",90)]):
            sync_tasks.run_once(self.plan,path=self.db,today=TODAY,retries=0)
        with patch.object(sync_tasks.history,"fred_csv",side_effect=ValueError("secret key abc123")):
            result=sync_tasks.run_once(self.plan,path=self.db,today=TODAY,retries=4,sleep=lambda _:None)
        self.assertEqual(result["failed"],1)
        self.assertEqual(result["jobs"][0]["attempts"],1)
        self.assertEqual(result["jobs"][0]["failure_code"],"ValueError")
        rpt=health.report(self.db,today=TODAY)
        self.assertEqual(rpt["jobs"][0]["health"],"failed_last_attempt")
        self.assertTrue(rpt["jobs"][0]["last_success_at"])
        self.assertEqual(geo.status(self.db)["tables"]["price_observations"],1)
        self.assertNotIn("abc123",json.dumps(rpt))
        self.assertTrue(any(a["code"]=="failed_last_attempt" for a in rpt["alerts"]))

    def test_concurrent_runner_is_blocked_by_lease(self):
        self.write_plan([self.fred_job()])
        token=health.lease(self.db)
        self.assertTrue(token)
        try:
            self.assertIsNone(health.lease(self.db))
            with self.assertRaises(RuntimeError):
                sync_tasks.run_once(self.plan,path=self.db,today=TODAY)
            self.assertEqual(geo.status(self.db)["tables"]["sync_runs"],0)
        finally:
            health.release(self.db,token)
        with patch.object(sync_tasks.history,"fred_csv",return_value=[("2026-10-14",88)]):
            self.assertEqual(sync_tasks.run_once(self.plan,path=self.db,today=TODAY)["failed"],0)

    def test_upstream_revisions_record_only_real_value_changes(self):
        market_data.initialize(self.db)
        rows=[("2026-10-13",99.3)]
        with patch.object(sync_tasks.history,"fred_csv",return_value=rows):
            self.write_plan([self.fred_job()])
            sync_tasks.run_once(self.plan,path=self.db,today=TODAY)
        with patch.object(sync_tasks.history,"fred_csv",return_value=[("2026-10-13",100.1)]):
            sync_tasks.run_once(self.plan,path=self.db,today=TODAY)
            sync_tasks.run_once(self.plan,path=self.db,today=TODAY)
        report=health.report(self.db,today=TODAY)
        self.assertEqual(report["price_revision_count"],1)
        with geo.connect(self.db) as conn:
            old,new=conn.execute("SELECT previous_value,replacement_value FROM price_revisions").fetchone()
        self.assertEqual((old,new),(99.3,100.1))

    def test_stale_and_missing_observations_are_not_marked_fresh(self):
        market_data.save_prices("eia:RBRTE","oil","eia","Brent",
                "daily","USD","barrel",[("2023-10-14",90.0)],self._initialized_db())
        report=health.report(self.db,today=TODAY)
        self.assertEqual(report["price_series"][0]["health"],"stale_observations")
        self.assertTrue(report["alerts"])

    def _initialized_db(self):
        market_data.initialize(self.db)
        return self.db

    def test_review_watch_never_auto_imports_events_and_persists_alert(self):
        watch_file=sync_tasks.ROOT/"data"/("test-watch-"+uuid.uuid4().hex+".json")
        # Minimal fixture uses a legally structured event but is never imported.
        from test_events import fixture
        self.write_plan([{"id":"review-packet","provider":"event-watch",
                          "file":"data/"+watch_file.name}])
        try:
            watch_file.write_text(json.dumps(fixture()),encoding="utf-8")
            first=sync_tasks.run_once(self.plan,path=self.db,today=TODAY)
            self.assertEqual(first["manual_review_required"],[])
            self.assertEqual(geo.status(self.db)["tables"]["events"],0)
            doc=fixture()
            doc["events"][0]["description"]="Changed editorial description (test only)"
            watch_file.write_text(json.dumps(doc),encoding="utf-8")
            second=sync_tasks.run_once(self.plan,path=self.db,today=TODAY)
            self.assertEqual(second["manual_review_required"],["review-packet"])
            info=health.report(self.db,today=TODAY)
            self.assertEqual(info["jobs"][0]["review_pending"],1)
            self.assertTrue(health.acknowledge_watch("review-packet",self.db))
            self.assertFalse(health.acknowledge_watch("review-packet",self.db))
            self.assertFalse(health.report(self.db,today=TODAY)["jobs"][0]["review_pending"])
            self.assertEqual(geo.status(self.db)["tables"]["events"],0)
        finally:
            watch_file.unlink(missing_ok=True)

    def test_wrong_schema_fails_closed(self):
        geo.initialize(self.db)
        with geo.connect(self.db) as conn:
            conn.execute("PRAGMA user_version=999")
        with self.assertRaises(RuntimeError):
            health.report(self.db,today=TODAY)


class SyncHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=server.ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join()

    def test_readonly_status_and_503_on_missing_db(self):
        with patch.object(server.sync_health,"report",
                          return_value={"alerts":[],"recent_runs":[],"jobs":[]}):
            with urllib.request.urlopen(self.base+"/api/sync/status",timeout=3) as r:
                self.assertEqual(r.status,200)
                self.assertEqual(json.load(r)["alerts"],[])
        with patch.object(server.sync_health,"report",side_effect=sqlite3.OperationalError("missing")):
            with self.assertRaises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(self.base+"/api/sync/status",timeout=3)
            self.assertEqual(err.exception.code,503)


if __name__=="__main__":
    unittest.main()
