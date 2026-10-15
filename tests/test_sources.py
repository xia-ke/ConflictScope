"""Offline tests for GDELT/ReliefWeb collectors and existing API integration."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import source_collectors as collectors
import server


class CollectorTests(unittest.TestCase):
    def test_gdelt_normalizes_and_rejects_unsafe_urls(self):
        requests = []
        def loader(url):
            requests.append(url)
            return {"articles": [
                {"title": "Ukraine &amp; conflict", "url": "https://example.org/report", "domain": "example.org", "seendate": "20261014090000"},
                {"title": "unsafe", "url": "javascript:alert(1)", "seendate": "20261014090000"}
            ]}
        rows = collectors.gdelt(server.COUNTRIES["UKR"], days=7, loader=loader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Ukraine & conflict")
        self.assertEqual(rows[0]["provider"], "GDELT")
        self.assertEqual(parse_qs(urlsplit(requests[0]).query)["timespan"], ["7d"])

    def test_reliefweb_requires_config(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                collectors.reliefweb(server.COUNTRIES["UKR"])

    def test_reliefweb_normalizes_reports(self):
        def loader(url):
            self.assertEqual(parse_qs(urlsplit(url).query)["appname"], ["approved-test"])
            return {"data": [{"fields": {"title": "Ukraine response", "url": "https://reliefweb.int/report/ukraine/test",
                      "date": {"created": "2026-10-14T08:00:00+00:00"}, "source": [{"name": "OCHA"}]}}]}
        rows = collectors.reliefweb(server.COUNTRIES["UKR"], appname="approved-test", loader=loader)
        self.assertEqual(rows[0]["kind"], "humanitarian")
        self.assertEqual(rows[0]["source"], "OCHA")

    def test_partial_failure(self):
        with patch.object(collectors, "gdelt", side_effect=OSError("offline")), patch.object(collectors, "reliefweb", return_value=[]):
            rows, good, bad = collectors.collect(server.COUNTRIES["UKR"], include_reliefweb=True)
        self.assertEqual((rows, good, bad), ([], ["ReliefWeb"], ["GDELT"]))

    def test_timestamp_and_link_security(self):
        self.assertEqual(collectors.timestamp("20261015090000"), "2026-10-15T09:00:00+00:00")
        self.assertEqual(collectors.safe_link("https://user:pass@example.org/"), "")
        self.assertIsNone(collectors.normalize("title", "file:///tmp/x", "X", "20261015090000", "GDELT", "media"))


if __name__ == "__main__":
    unittest.main()
