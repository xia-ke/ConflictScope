"""P1 adapters, persistence and HTTP regression (fixtures only; zero network calls)."""
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geoeconomics as geo
import market_data as store
import market_sources as src
import server


def sample_comtrade():
    return {"count": 1, "data": [{
        "reporterCode": 804, "period": "2024", "partnerCode": 0, "partner2Code": 0,
        "motCode": 0, "customsCode": "C00", "cmdCode": "100199",
        "classificationCode": "H5", "flowCode": "X",
        "primaryValue": 123456.75, "netWgt": 4000
    }]}


def xlsx_fixture():
    xmlns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    relns = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    workbook = (f'<workbook xmlns="{xmlns}" xmlns:r="{relns}">'
                '<sheets><sheet name="Monthly Prices" sheetId="1" r:id="rId1"/>'
                '</sheets></workbook>')
    relations = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Target="worksheets/sheet1.xml"'
                 ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
                 '</Relationships>')
    worksheet = (f'<worksheet xmlns="{xmlns}"><sheetData>'
                 '<row r="1"><c r="A1" t="inlineStr"><is><t>Month</t></is></c>'
                 '<c r="B1" t="inlineStr"><is><t>Copper</t></is></c></row>'
                 '<row r="2"><c r="A2" t="inlineStr"><is><t>2026M08</t></is></c>'
                 '<c r="B2"><v>9100.25</v></c></row>'
                 '<row r="3"><c r="A3" t="inlineStr"><is><t>2026M09</t></is></c>'
                 '<c r="B3"><v>9200.50</v></c></row>'
                 '</sheetData></worksheet>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml",workbook)
        z.writestr("xl/_rels/workbook.xml.rels",relations)
        z.writestr("xl/worksheets/sheet1.xml",worksheet)
    return out.getvalue()


class AdapterTests(unittest.TestCase):
    def test_comtrade_qualified_single_record(self):
        seen = []
        def loader(url):
            seen.append(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query))
            return sample_comtrade()
        rows = src.comtrade("UKR", 2024, commodity="wheat", loader=loader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hs_revision"], "HS2017")
        self.assertEqual(rows[0]["quantity_unit"], "kg")
        self.assertEqual(seen[0]["partnerCode"], ["0"])
        self.assertEqual(seen[0]["cmdCode"], ["100199"])
        self.assertEqual(seen[0]["flowCode"], ["X"])

    def test_comtrade_rejects_partial_and_wrong_dimension(self):
        response = sample_comtrade()
        response["count"] = 2
        with self.assertRaises(ValueError):
            src.comtrade("UKR", 2024, commodity="wheat", loader=lambda url: response)
        response["count"] = 1
        response["data"][0]["partnerCode"] = 156
        with self.assertRaises(ValueError):
            src.comtrade("UKR", 2024, commodity="wheat", loader=lambda url: response)

    def test_comtrade_empty_preview_is_not_a_zero_trade_claim(self):
        with self.assertRaisesRegex(ValueError,"no verified rows"):
            src.comtrade("UKR",2024,commodity="wheat",
                         loader=lambda url:{"count":0,"data":[]})

    def test_comtrade_rejects_unapproved_reporter(self):
        with self.assertRaises(ValueError):
            src.comtrade("USA", 2024, commodity="wheat", loader=lambda url: {})

    def test_eia_requires_key_and_has_explicit_dates(self):
        with self.assertRaises(ValueError):
            src.eia_spot("RBRTE",start="2026-10-01",end="2026-10-10",key=None)
        def loader(url):
            qs = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            self.assertEqual(qs["api_key"], ["test-key"])
            self.assertEqual(qs["facets[series][]"], ["RBRTE"])
            return {"response": {"total":"2","data":[
                {"period":"2026-10-01","series":"RBRTE","value":"88.23"},
                {"period":"2026-10-02","series":"RBRTE","value":"90.10"}]}}
        rows = src.eia_spot("RBRTE",start="2026-10-01",end="2026-10-10",
                            key="test-key",loader=loader)
        self.assertEqual(rows, [("2026-10-01",88.23),("2026-10-02",90.10)])

    def test_eia_refuses_pagination_truncation(self):
        def loader(url):
            return {"response":{"total":"6000","data":[]}}
        with self.assertRaises(ValueError):
            src.eia_spot("RWTC",start="2020-01-01",end="2026-10-15",
                         key="dummy",loader=loader)

    def test_pink_sheet_parser(self):
        data = xlsx_fixture()
        self.assertEqual(src.pink_sheet_rows(data, "Copper"),
                         [("2026-08-01",9100.25),("2026-09-01",9200.5)])
        with self.assertRaises(ValueError):
            src.pink_sheet_rows(data,"Oil")
        with self.assertRaises(ValueError):
            src.pink_sheet_rows(b'not an xlsx',"Copper")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name)/"subdir"/"market.sqlite3"
        store.initialize(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_provenance_and_empty_not_fake_totals(self):
        catalog=store.commodity_catalog(self.db)
        self.assertEqual(catalog["scope"],"curated_hs6_mapping")
        self.assertEqual(geo.status(self.db)["tables"]["trade_flows"],0)
        response = store.trade_by_country("UKR",path=self.db)
        self.assertTrue(response["not_total_trade_ranking"])
        self.assertEqual(response["rows"],[])

    def test_comtrade_transactional_upsert_world_partner(self):
        rows=src.comtrade("UKR",2024,commodity="wheat",loader=lambda url: sample_comtrade())
        self.assertEqual(store.save_trade(rows,self.db),1)
        self.assertEqual(store.save_trade(rows,self.db),1)
        self.assertEqual(geo.status(self.db)["tables"]["trade_flows"],1)
        response=store.trade_by_country("UKR",year=2024,flow="export",path=self.db)
        self.assertEqual(len(response["rows"]),1)
        self.assertEqual(response["rows"][0]["value_usd"],123456.75)

    def test_reject_metadata_mutation_without_partial_price_import(self):
        store.save_prices("wb:copper","copper","pink-sheet","Copper","monthly",
                          "USD","metric ton",[("2026-09-01",9300.0)],self.db)
        with self.assertRaises(ValueError):
            store.save_prices("wb:copper","copper","pink-sheet","Wheat","monthly",
                              "USD","metric ton",[("2026-10-01",1000.0)],self.db)
        p = store.price_history("wb:copper",path=self.db)
        self.assertEqual(len(p["observations"]),1)
        self.assertEqual(p["metadata"]["benchmark"],"Copper")

    def test_price_query_filters_and_empty_unknown(self):
        store.save_prices("eia:RBRTE","oil","eia","Brent","daily","USD","barrel",
                          [("2026-10-01",80),("2026-10-02",81)],self.db)
        p=store.price_history("eia:RBRTE",start="2026-10-02",path=self.db)
        self.assertEqual(len(p["observations"]),1)
        self.assertEqual(p["observations"][0]["value"],81)
        self.assertIsNone(store.price_history("missing",path=self.db))

    def test_readonly_missing_db_is_not_implicitly_created(self):
        missing=Path(self.tmp.name)/"absent.sqlite3"
        with self.assertRaises(Exception):
            store.commodity_catalog(missing)
        self.assertFalse(missing.exists())


class MarketHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def get(self,path):
        with urllib.request.urlopen(self.base+path,timeout=3) as response:
            return response.status, json.load(response)

    def test_catalog_endpoint(self):
        with patch.object(server.market_data,"commodity_catalog",
                          return_value={"scope":"curated_hs6_mapping","commodities":[]}):
            status,doc=self.get("/api/commodities")
        self.assertEqual(status,200)
        self.assertEqual(doc["commodities"],[])

    def test_trade_endpoint_and_reject_invalid(self):
        with patch.object(server.market_data,"trade_by_country",
                          return_value={"rows":[],"scope":"selected_hs6_world_partner_only"}) as f:
            status,_=self.get("/api/trade/UKR?year=2024&flow=export")
        self.assertEqual(status,200)
        f.assert_called_once_with("UKR",year=2024,flow="export")
        for path in ("/api/trade/UKR?year=abcd",
                     "/api/prices/test?start=bad-date"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(path)
            self.assertEqual(caught.exception.code,400)

    def test_unknown_price_series_returns_404(self):
        with patch.object(server.market_data,"price_history",return_value=None):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.get("/api/prices/does-not-exist")
        self.assertEqual(caught.exception.code,404)


if __name__=="__main__":
    unittest.main()
