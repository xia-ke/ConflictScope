"""Offline P2 event ledger, provenance, filters, rollback and HTTP regression."""
import copy
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import event_data
import geoeconomics as geo
import server


def fixture():
    """Synthetic TEST FIXTURE only. These are not real-world incidents."""
    source = {"id": "example-institution", "name": "Fixture Agency",
              "homepage_url": "https://example.org/", "access_type": "public",
              "license_note": "Only a test fixture"}
    return {
        "format": "conflictscope.events.v1",
        "sources": [source],
        "events": [{
            "id": "fixture-event-2026-01", "title": "Synthetic test embargo",
            "event_type": "embargo", "verification_status": "official",
            "source_id": source["id"], "source_url": "https://example.org/primary",
            "occurred_at": "2026-01-01", "announced_at": "2026-01-02",
            "effective_at": "2026-01-05", "published_at": "2026-01-03",
            "date_precision": "day", "description": "Synthetic QA record, not factual.",
            "review": {"decision": "approved", "reviewer": "test reviewer",
                       "reviewed_at": "2026-01-06", "note": "Fixture validation only."},
            "evidence": [
                {"source_id": source["id"],"url": "https://example.org/primary",
                 "role": "primary", "published_at": "2026-01-03"},
                {"source_id": source["id"],"url": "https://example.org/context",
                 "role": "context"}
            ],
            "links": [
                {"country_id": "UKR", "commodity_id": "wheat",
                 "impact_mechanism": "sanctions",
                 "evidence_url": "https://example.org/primary",
                 "evidence_note": "Only for importer test"}
            ]
        }]
    }


class EventImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "events.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_auto_events_on_init(self):
        geo.initialize(self.db)
        count = geo.status(self.db)["tables"]
        self.assertEqual(count["events"], 0)
        self.assertEqual(count["event_reviews"], 0)
        self.assertEqual(count["event_evidence"], 0)

    def test_import_idempotent_and_four_dates(self):
        doc=fixture()
        result=event_data.import_batch(doc,self.db)
        self.assertEqual(result["inserted"],1)
        self.assertEqual(event_data.import_batch(doc,self.db)["unchanged"],1)
        counts=geo.status(self.db)["tables"]
        self.assertEqual(counts["events"],1)
        self.assertEqual(counts["event_reviews"],1)
        self.assertEqual(counts["event_evidence"],2)
        bycountry=event_data.timeline(country="UKR",path=self.db)
        self.assertEqual(bycountry["count"],1)
        event=bycountry["events"][0]
        self.assertEqual(event["timeline_date"],"2026-01-05")
        self.assertEqual(event["announced_at"],"2026-01-02")
        self.assertEqual(event["published_at"],"2026-01-03")
        self.assertEqual(event["occurred_at"],"2026-01-01")
        self.assertEqual(event["track"],"policy")
        self.assertTrue(event["editor_reviewed"])
        self.assertFalse(event["research_ai_assisted"])
        self.assertEqual(len(event["evidence"]),2)
        self.assertEqual(event_data.timeline(commodity="wheat",path=self.db)["count"],1)
        self.assertEqual(event_data.timeline(commodity="gold",path=self.db)["count"],0)

    def test_immutable_changed_event_id(self):
        doc=fixture()
        event_data.import_batch(doc,self.db)
        other=copy.deepcopy(doc)
        other["events"][0]["title"]="Different event in same id"
        with self.assertRaises(ValueError):
            event_data.import_batch(other,self.db)
        self.assertEqual(geo.status(self.db)["tables"]["events"],1)

    def test_incomplete_batch_rolls_back_events_and_review(self):
        doc=fixture()
        second=copy.deepcopy(doc["events"][0])
        second["id"]="fixture-second-2026-01"
        second["links"][0]["country_id"]="XXX"
        doc["events"].append(second)
        with self.assertRaises(Exception):
            event_data.import_batch(doc,self.db)
        self.assertEqual(geo.status(self.db)["tables"]["events"],0)
        self.assertEqual(geo.status(self.db)["tables"]["event_reviews"],0)

    def test_unapproved_event_is_rejected(self):
        doc=fixture()
        doc["events"][0]["review"]["decision"]="needs_review"
        with self.assertRaises(ValueError):
            event_data.import_batch(doc,self.db)
        self.assertFalse(self.db.exists())

    def test_missing_proof_invalid_dates_and_url(self):
        for change in ("primary_missing","link_uncited","date_bad","url_unsafe","unknown_commodity"):
            with self.subTest(change=change):
                doc=fixture()
                event=doc["events"][0]
                if change=="primary_missing":
                    event["evidence"][0]["role"]="context"
                elif change=="link_uncited":
                    event["links"][0]["evidence_url"]="https://example.org/not-evidence"
                elif change=="date_bad":
                    event["effective_at"]="2026-02-31"
                elif change=="url_unsafe":
                    event["evidence"][0]["url"]="javascript:alert(1)"
                else:
                    event["links"][0]["commodity_id"]="not-in-catalog"
                    # FK enforcement occurs transactionally.
                with self.assertRaises(Exception):
                    event_data.import_batch(doc,self.db)
                if self.db.exists():
                    self.assertEqual(geo.status(self.db)["tables"]["events"],0)

    def test_date_bases_and_period_bounds(self):
        event_data.import_batch(fixture(),self.db)
        self.assertEqual(event_data.timeline(country="UKR",basis="occurred",path=self.db)["events"][0]["timeline_date"],"2026-01-01")
        self.assertEqual(event_data.timeline(country="UKR",basis="announced",path=self.db)["events"][0]["timeline_date"],"2026-01-02")
        self.assertEqual(event_data.timeline(country="UKR",basis="published",path=self.db)["events"][0]["timeline_date"],"2026-01-03")
        self.assertEqual(event_data.timeline(country="UKR",start="2026-01-04",path=self.db)["count"],1)
        self.assertEqual(event_data.timeline(country="UKR",end="2026-01-04",path=self.db)["count"],0)
        self.assertEqual(event_data.timeline(country="UKR",event_type="sanction",path=self.db)["count"],0)
        with self.assertRaises(ValueError):
            event_data.timeline(path=self.db)
        with self.assertRaises(ValueError):
            event_data.timeline(country="UKR",start="2026-13-33",path=self.db)

    def test_unreviewed_legacy_records_never_published(self):
        geo.initialize(self.db)
        with geo.connect(self.db) as conn:
            conn.execute("""INSERT INTO data_sources
              (id,name,homepage_url,access_type) VALUES
              ('legacy','Fixture','https://example.org','public')""")
            conn.execute("""INSERT INTO events
              (id,title,event_type,occurred_at,source_id,source_url,created_at)
              VALUES('legacy-event','Unreviewed event','armed_conflict','2026-01-01',
                     'legacy','https://example.org','2026-01-02')""")
            conn.execute("""INSERT INTO event_links
              (event_id,country_id,impact_mechanism,evidence_url)
              VALUES('legacy-event','UKR','undetermined','https://example.org')""")
        self.assertEqual(event_data.timeline(country="UKR",path=self.db)["count"],0)


class EventHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http=server.ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def get(self,path):
        with urllib.request.urlopen(self.base+path,timeout=3) as response:
            return response.status,json.load(response)

    def test_valid_endpoint(self):
        with patch.object(server.event_data,"timeline",
                          return_value={"events":[],"count":0,"basis":"impact"}) as get:
            status,result=self.get("/api/timeline?country=UKR&commodity=wheat&basis=effective")
        self.assertEqual(status,200)
        self.assertEqual(result["events"],[])
        self.assertEqual(get.call_args.kwargs["basis"],"effective")
        self.assertEqual(get.call_args.kwargs["country"],"UKR")

    def test_invalid_queries(self):
        for path in ("/api/timeline", "/api/events?country=UKR&limit=-1",
                     "/api/timeline?country=UKR&basis=unsupported",
                     "/api/events?country=UKR&start=2026-66-99"):
            with self.subTest(path=path):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.get(path)
                self.assertEqual(caught.exception.code,400)

    def test_missing_db_is_explicit_503(self):
        with patch.object(server.event_data,"timeline",side_effect=__import__("sqlite3").OperationalError("no DB")):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.get("/api/timeline?country=UKR")
        self.assertEqual(caught.exception.code,503)


if __name__=="__main__":
    unittest.main()
