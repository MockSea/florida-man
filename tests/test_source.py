import unittest
from datetime import date

from fm import source
from fm.sanitize import clean_text, normalize_florida_man, safe_url
from tests.helpers import fixture


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.items = source.parse_rss(fixture("day_2025-10-14.xml"))
        self.by_link = {i["link"]: i for i in self.items}

    def test_non_http_links_dropped(self):
        links = [i["link"] for i in self.items]
        self.assertTrue(all(l.startswith("https://") for l in links))
        self.assertNotIn("Evil Site", [i["sourceName"] for i in self.items])
        self.assertNotIn("Data Site", [i["sourceName"] for i in self.items])
        self.assertEqual(len(self.items), 6)

    def test_source_suffix_stripped(self):
        it = self.by_link["https://news.google.com/rss/articles/CBMiAAA111?oc=5"]
        self.assertEqual(it["title"],
                         "Florida man arrested after hitting deputy with a chicken nugget at Waffle House")
        self.assertEqual(it["sourceName"], "WFLA")
        self.assertEqual(it["sourceSite"], "https://www.wfla.com")
        self.assertEqual(it["extra"], "")  # description only repeats title/source

    def test_script_tags_and_entities(self):
        it = self.by_link["https://news.google.com/rss/articles/CBMiDDD444?oc=5"]
        self.assertEqual(it["title"],
                         'Florida man\'s "emotional support" iguana & him banned from Publix')
        self.assertNotIn("<", it["title"])
        self.assertNotIn("alert", it["title"])
        self.assertEqual(it["extra"], "He said the iguana needed groceries. Store staff disagreed.")
        self.assertIsNone(it["sourceSite"])  # javascript: source url dropped

    def test_pubdate_parsed_tz_aware(self):
        it = self.by_link["https://news.google.com/rss/articles/CBMiBBB222?oc=5"]
        self.assertEqual(it["published"].utcoffset().total_seconds(), 0)

    def test_empty_feed(self):
        self.assertEqual(source.parse_rss(fixture("empty.xml")), [])

    def test_doctype_refused(self):
        with self.assertRaises(source.UpstreamError):
            source.parse_rss(fixture("doctype.xml"))

    def test_doctype_hidden_by_encoding_refused(self):
        # A byte scan for "<!doctype" misses UTF-16; expat would still parse it.
        bomb = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE r [<!ENTITY a "aaaa">]>'
                '<rss><channel><item><title>&a;</title></item></channel></rss>')
        for body in ("\ufeff" + bomb).encode("utf-16-le"), bomb.encode("utf-16"), b"<rss>\x00</rss>":
            with self.assertRaises(source.UpstreamError):
                source.parse_rss(body)

    def test_encoding_declaration_ignored(self):
        body = fixture("day_2025-10-14.xml").replace(b'encoding="UTF-8"', b'encoding="cp500"', 1)
        self.assertEqual(len(source.parse_rss(body)), len(self.items))
        self.assertEqual(len(source.parse_rss(b"\xef\xbb\xbf" + fixture("day_2025-10-14.xml"))),
                         len(self.items))

    def test_malformed_refused(self):
        with self.assertRaises(source.UpstreamError):
            source.parse_rss(b"<rss><channel><item>")


class SanitizeTests(unittest.TestCase):
    def test_clean_text(self):
        self.assertEqual(clean_text("<b>Hi</b>&amp;nbsp;there&amp;#39;s"), "Hi there's")
        self.assertEqual(clean_text("a‮b\x00c   d"), "abc d")
        self.assertEqual(clean_text("<script>x()</script>ok"), "ok")
        self.assertEqual(clean_text("&lt;img src=x onerror=alert(1)&gt;hi"), "hi")
        self.assertEqual(len(clean_text("x" * 1000, max_len=50)), 50)

    def test_normalize(self):
        self.assertEqual(normalize_florida_man("FLORIDA MAN bites dog"), "Florida Man bites dog")
        self.assertEqual(normalize_florida_man("Fla. man, florida  man"), "Florida Man, Florida Man")

    def test_safe_url(self):
        self.assertEqual(safe_url("https://a.com/x?y=1"), "https://a.com/x?y=1")
        for bad in ("javascript:alert(1)", "JAVASCRIPT:alert(1)", "data:text/html,x",
                    "//evil.com", "ftp://a.com", "https://user:pw@a.com/", "http://",
                    "https://a.com/\nx", "vbscript:x", " ", None, 5):
            self.assertIsNone(safe_url(bad), bad)


class QueryTests(unittest.TestCase):
    def test_query_shapes(self):
        self.assertEqual(source.build_query(date(2025, 10, 14)),
                         '"Florida man" after:2025-10-13 before:2025-10-15')
        self.assertEqual(source.build_query(date(2025, 3, 1), "Port St. Lucie"),
                         '"Florida man" "Port St. Lucie" after:2025-02-28 before:2025-03-02')
        url = source.build_url("x y")
        self.assertTrue(url.startswith("https://news.google.com/rss/search?q=x+y&"))
        self.assertIn("ceid=US%3AEN".lower(), url.lower())

    def test_fetch_refuses_other_hosts(self):
        for url in ("https://evil.com/rss", "http://news.google.com/rss",
                    "https://news.google.com.evil.com/rss", "file:///etc/passwd"):
            with self.assertRaises(source.UpstreamError):
                source.http_get(url)


class RateLimiterTests(unittest.TestCase):
    def test_window(self):
        t = [0.0]
        rl = source.RateLimiter(3, clock=lambda: t[0])
        rl.acquire(2)
        with self.assertRaises(source.RateLimited):
            rl.acquire(2)  # all-or-nothing
        rl.acquire(1)
        with self.assertRaises(source.RateLimited) as cm:
            rl.acquire()
        self.assertGreaterEqual(cm.exception.retry_after, 1)
        t[0] = 60.0
        rl.acquire(3)


if __name__ == "__main__":
    unittest.main()
