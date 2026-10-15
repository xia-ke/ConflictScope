"""P0 geoeconomic persistence contracts; fully offline and isolated."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geoeconomics as geo


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "nested" / "geo.sqlite3"
        geo.initialize(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_initialization_is_repeatable_and_seeds_only_existing_countries(self):
        before = geo.status(self.db)
        self.assertEqual(before["schema_version"], 2)
        self.assertEqual(before["tables"]["countries"], 19)
        self.assertEqual(before["tables"]["events"], 0)
        self.assertEqual(before["tables"]["event_reviews"], 0)
        self.assertEqual(before["tables"]["price_observations"], 0)
        geo.initialize(self.db)
        self.assertEqual(before, geo.status(self.db))

    def test_readonly_status_does_not_create_database(self):
        absent = Path(self.temp.name) / "absent.sqlite3"
        with self.assertRaises(sqlite3.OperationalError):
            geo.status(absent)
        self.assertFalse(absent.exists())

    def test_foreign_keys_and_checks(self):
        with geo.connect(self.db) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO price_series(id, commodity_id, source_id, benchmark, price_type, frequency, currency, unit) VALUES ('x','none','none','X','spot','daily','USD','bbl')")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO countries(id, name) VALUES ('invalid', 'bad')")

    def test_event_dates_provenance_and_relationships(self):
        with geo.connect(self.db) as conn:
            conn.execute("INSERT INTO data_sources(id, name, homepage_url, access_type, license_note) VALUES ('official','Official bulletin','https://example.org','public','Review source terms')")
            conn.execute("INSERT INTO commodities(id, name, group_name) VALUES ('wheat','Wheat','agriculture')")
            conn.execute("INSERT INTO commodity_hs_codes(commodity_id, hs_revision, hs_code) VALUES ('wheat','HS2017','100199')")
            conn.execute("""INSERT INTO events
              (id,title,event_type,occurred_at,announced_at,effective_at,published_at,
               verification_status,source_id,source_url,created_at)
               VALUES ('event1','Export restriction','embargo','2026-10-01','2026-10-02',
                       '2026-10-05','2026-10-03','official','official',
                       'https://example.org/notice','2026-10-06T00:00:00Z')""")
            conn.execute("""INSERT INTO event_links
               (event_id,country_id,commodity_id,impact_mechanism,evidence_url)
               VALUES ('event1','UKR','wheat','sanctions','https://example.org/notice')""")
            dates = conn.execute("SELECT occurred_at, announced_at, effective_at, published_at FROM events WHERE id='event1'").fetchone()
            self.assertEqual(dates, ("2026-10-01", "2026-10-02", "2026-10-05", "2026-10-03"))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO events
                   (id,title,event_type,source_id,source_url,created_at)
                   VALUES ('missing-date','bad','embargo','official','https://example.org','2026-10-06')""")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""INSERT INTO event_links
                  (event_id,impact_mechanism,evidence_url) VALUES ('event1','supply','https://example.org')""")

    def test_trade_and_price_are_separate_from_events(self):
        with geo.connect(self.db) as conn:
            conn.execute("INSERT INTO data_sources(id,name,homepage_url,access_type) VALUES ('trade','Example statistics','https://example.org','public')")
            conn.execute("INSERT INTO commodities(id,name,group_name) VALUES ('oil','Crude oil','energy')")
            conn.execute("""INSERT INTO price_series
              (id,commodity_id,source_id,benchmark,price_type,frequency,currency,unit)
              VALUES ('oil-monthly','oil','trade','example benchmark','spot','monthly','USD','barrel')""")
            conn.execute("""INSERT INTO price_observations(series_id,observed_at,value,retrieved_at)
              VALUES ('oil-monthly','2026-09-01',70.2,'2026-10-15T00:00:00Z')""")
            conn.execute("""INSERT INTO trade_flows(reporter_id,partner_id,flow,period_start,
              period_type,hs_revision,hs_code,value_usd,source_id,retrieved_at)
              VALUES ('UKR','RUS','export','2024-01-01','annual','HS2017','270900',
              1000,'trade','2026-10-15T00:00:00Z')""")
        self.assertEqual(geo.status(self.db)["tables"]["price_observations"], 1)
        self.assertEqual(geo.status(self.db)["tables"]["trade_flows"], 1)
        self.assertEqual(geo.status(self.db)["tables"]["events"], 0)

    def test_all_partners_rollup_rejects_duplicate(self):
        with geo.connect(self.db) as conn:
            conn.execute("INSERT INTO data_sources(id,name,homepage_url,access_type) VALUES ('comtrade','Comtrade','https://example.org','public')")
            values = ('UKR', None, 'export', '2024-01-01', 'annual',
                      'HS2017', '100199', 1000, 'comtrade', '2026-10-15T00:00:00Z')
            statement = """INSERT INTO trade_flows(reporter_id,partner_id,flow,period_start,
                period_type,hs_revision,hs_code,value_usd,source_id,retrieved_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)"""
            conn.execute(statement, values)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(statement, values)

    def test_v1_upgrade_is_additive(self):
        with geo.connect(self.db) as conn:
            conn.execute("PRAGMA user_version=1")
            conn.execute("INSERT INTO data_sources(id,name,homepage_url,access_type) VALUES ('old','Existing source','https://example.org','public')")
        geo.initialize(self.db)
        self.assertEqual(geo.status(self.db)["schema_version"],2)
        with geo.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT name FROM data_sources WHERE id='old'").fetchone()[0],"Existing source")

    def test_future_schema_version_fails_closed(self):
        with geo.connect(self.db) as conn:
            conn.execute("PRAGMA user_version=999")
        with self.assertRaises(RuntimeError):
            geo.initialize(self.db)


if __name__ == "__main__":
    unittest.main()
