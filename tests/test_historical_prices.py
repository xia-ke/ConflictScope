"""Offline fixture tests for price backfill: validation, dates and SQLite provenance."""
import io
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import historical_prices as backfill
import geoeconomics as geo
import market_data


def csv_fixture(series="DCOILBRENTEU"):
    return ("DATE,"+series+"\n"
            "2021-12-31,78.30\n"
            "2022-02-23,96.60\n"
            "2022-02-24,98.00\n"
            "2022-02-25,.\n"
            "2022-02-28,103.10\n"
            "2022-03-01,103.12\n").encode("utf-8")


class FredTests(unittest.TestCase):
    def test_daily_history_filter_missing_and_original_units(self):
        requested=[]
        rows=backfill.fred_csv("DCOILBRENTEU","2022-02-20","2022-02-28",
             loader=lambda url: (requested.append(url),csv_fixture())[1])
        self.assertEqual(rows,[("2022-02-23",96.6),("2022-02-24",98.0),
                               ("2022-02-28",103.1)])
        self.assertIn("id=DCOILBRENTEU",requested[0])
        self.assertEqual(backfill.FRED["DCOILBRENTEU"]["frequency"],"daily")
        self.assertEqual(backfill.FRED["DCOILBRENTEU"]["unit"],"barrel")

    def test_imf_monthly_frequency_is_not_inferred_daily(self):
        self.assertEqual(backfill.FRED["PWHEAMTUSDM"]["frequency"],"monthly")
        self.assertEqual(backfill.FRED["PCOPPUSDM"]["source"],"fred-imf")
        raw=b"DATE,PWHEAMTUSDM\n2022-01-01,250.0\n2022-02-01,260.2\n"
        rows=backfill.fred_csv("PWHEAMTUSDM","2022-01-01","2022-12-31",loader=lambda url:raw)
        self.assertEqual(rows,[("2022-01-01",250.0),("2022-02-01",260.2)])

    def test_reject_wrong_header_dates_duplicates_and_nonpositive(self):
        for raw in (
            b"DATE,other\n2022-02-24,90\n",
            b"DATE,DCOILBRENTEU\n2022-02-30,90\n",
            b"DATE,DCOILBRENTEU\n2022-02-24,90\n2022-02-24,91\n",
            b"DATE,DCOILBRENTEU\n2022-02-24,0\n",
            b"<html>blocked by server</html>"
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    backfill.fred_csv("DCOILBRENTEU","2022-01-01","2022-12-31",
                                      loader=lambda url:raw)

    def test_source_restrictions(self):
        with self.assertRaises(ValueError):
            backfill.https_bytes("http://fred.stlouisfed.org/graph/fredgraph.csv",
                                 loader=lambda url:b"ok")
        with self.assertRaises(ValueError):
            backfill.https_bytes("https://untrusted.example/x",loader=lambda url:b"ok")
        with self.assertRaises(ValueError):
            backfill.https_bytes("https://fred.stlouisfed.org/graph/fredgraph.csv",
                                 loader=lambda url:b"x"*(backfill.MAX_BYTES+1))

    def test_date_window_and_future_fail_closed(self):
        with self.assertRaises(ValueError):
            backfill.window("2022-09-12","2022-04-02")
        with self.assertRaises(ValueError):
            backfill.window("2022-02-30","2022-03-20")
        with self.assertRaises(ValueError):
            backfill.window("2022-02-01",(date.today()+timedelta(days=1)).isoformat())


class WBTests(unittest.TestCase):
    def test_workbook_parser_explicit_column_and_window(self):
        # Shared synthetic XLSX fixture from P1, no external network.
        from test_market import xlsx_fixture
        raw=xlsx_fixture()
        rows=backfill.wb_xlsx("Copper","2026-08-01","2026-08-31",raw=raw)
        self.assertEqual(rows,[("2026-08-01",9100.25)])
        with self.assertRaises(ValueError):
            backfill.wb_xlsx("Wheat","2026-08-01","2026-09-30",raw=raw)

    def test_pinned_url_is_official_and_has_explicit_benchmark(self):
        self.assertIn("thedocs.worldbank.org",backfill.WB_URL)
        self.assertIn("CMO-Historical-Data-Monthly.xlsx",backfill.WB_URL)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"market.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_fred_transactional_idempotent_distinct_benchmarks(self):
        self.assertEqual(backfill.store_fred("DCOILBRENTEU",[
            ("2022-02-23",96.6),("2022-02-24",98.0)],self.db),2)
        self.assertEqual(backfill.store_fred("DCOILBRENTEU",[
            ("2022-02-23",96.6),("2022-02-24",98.0)],self.db),2)
        self.assertEqual(backfill.store_fred("DCOILWTICO",[
            ("2022-02-24",92.1)],self.db),1)
        stats=backfill.coverage(self.db)
        self.assertEqual(stats["total_series"],2)
        self.assertEqual(geo.status(self.db)["tables"]["price_observations"],3)
        self.assertEqual(market_data.price_history("fred:DCOILBRENTEU",path=self.db)["metadata"]["unit"],"barrel")
        self.assertEqual(market_data.price_history("fred:DCOILBRENTEU",path=self.db)["metadata"]["source_name"],"FRED: EIA Spot Prices")

    def test_copyright_source_label_and_separate_monthly(self):
        backfill.store_fred("PWHEAMTUSDM",[("2022-02-01",290.5)],self.db)
        prices=market_data.price_history("fred:PWHEAMTUSDM",path=self.db)
        self.assertEqual(prices["metadata"]["frequency"],"monthly")
        with geo.connect(self.db) as db:
            license_note=db.execute("SELECT license_note FROM data_sources WHERE id='fred-imf'").fetchone()[0]
            self.assertIn("DO NOT redistribute",license_note)
            self.assertIsNone(db.execute("SELECT redistribution_allowed FROM data_sources WHERE id='fred-imf'").fetchone()[0])

    def test_wb_not_merged_into_fred_price_series(self):
        backfill.store_fred("PCOPPUSDM",[("2022-02-01",9000)],self.db)
        backfill.store_wb("wb:copper","copper","Copper","metric ton",
                          [("2022-02-01",9100)],self.db)
        assert backfill.coverage(self.db)["total_series"] == 2
        self.assertEqual(geo.status(self.db)["tables"]["price_observations"],2)

    def test_no_synthetic_market_values_before_import(self):
        market_data.initialize(self.db)
        self.assertEqual(backfill.coverage(self.db)["total_series"],0)
        self.assertEqual(geo.status(self.db)["tables"]["price_observations"],0)
        with self.assertRaises(ValueError):
            backfill.store_wb("wb:copper","copper","Copper","metric ton",
                              [("2022-02-01",0)],self.db)
        self.assertEqual(geo.status(self.db)["tables"]["price_observations"],0)

    def test_cli_dry_run_never_creates_db(self):
        data=csv_fixture()
        with patch.object(backfill,"https_bytes",return_value=data):
            report=backfill.cli(["--db",str(self.db),"fred",
              "--series","DCOILBRENTEU","--start","2022-02-01",
              "--end","2022-02-28","--dry-run"])
        self.assertEqual(report["records"],3)
        self.assertFalse(self.db.exists())


if __name__=="__main__":
    unittest.main()
