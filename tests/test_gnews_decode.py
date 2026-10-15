import json
import unittest
from urllib.parse import parse_qs

from fm import gnews_decode as gd
from fm import source
from tests.helpers import fixture

SRC = fixture("gnews/src.txt").decode().strip()
ART = fixture("gnews/article_id.txt").decode().strip()
PAGE = fixture("gnews/article_page_snippet.html")
BATCH = fixture("gnews/batchexecute_response.txt")
ATL = ("https://abovethelaw.com/2019/05/florida-man-lawyer-kicks-raccoon-off-his-boat"
       "-into-the-sea-because-florida/")


class FakeGoogle:
    """Stands in for source.http_get: page on GET, batch answer on POST."""

    def __init__(self, page=PAGE, batch=BATCH, error=None):
        self.page, self.batch, self.error = page, batch, error
        self.calls = []

    def __call__(self, url, data=None, headers=None):
        self.calls.append((url, data, headers))
        if self.error:
            raise self.error
        return self.page if data is None else self.batch


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class DecodeTests(unittest.TestCase):
    def test_real_fixtures_decode_to_publisher(self):
        g = FakeGoogle()
        self.assertEqual(gd.Decoder(fetch=g).decode(SRC), ATL)
        (u1, d1, h1), (u2, d2, h2) = g.calls
        self.assertEqual(u1, "https://news.google.com/rss/articles/%s?hl=en-US&gl=US&ceid=US%%3Aen" % ART)
        self.assertIsNone(d1)
        self.assertIn("Chrome", h1["User-Agent"])
        self.assertEqual(u2, gd.BATCH)
        self.assertTrue(h2["Content-Type"].startswith("application/x-www-form-urlencoded"))
        f_req = json.loads(parse_qs(d2.decode())["f.req"][0])
        inner = json.loads(f_req[0][0][1])
        self.assertEqual(f_req[0][0][0], "Fbv4je")
        self.assertEqual(inner, ["garturlreq", gd.CTX, ART, 1792012151, "AbIaSL8absTOWMUvSDLgaohYIpMm"])

    def test_only_google_news_article_links(self):
        for bad in ("https://evil.example/rss/articles/CBMiAAA111", "http://news.google.com/rss/articles/CBMiAAA111",
                    "https://news.google.com:8443/rss/articles/CBMiAAA111", "https://news.google.com/search?q=x",
                    "https://news.google.com/rss/articles/../../x", None):
            g = FakeGoogle()
            with self.assertRaises(gd.DecodeError, msg=bad):
                gd.Decoder(fetch=g).decode(bad)
            self.assertEqual(g.calls, [])

    def assertTrouble(self, g):
        clock = Clock()
        dec = gd.Decoder(fetch=g, clock=clock)
        with self.assertRaises(gd.DecoderTrouble):
            dec.decode(SRC)
        self.assertTrue(dec.backing_off())
        n = len(g.calls)
        with self.assertRaises(gd.DecoderTrouble):
            dec.decode(SRC)  # no request while backing off
        self.assertEqual(len(g.calls), n)
        clock.t += gd.BACKOFF + 1
        self.assertFalse(dec.backing_off())

    def test_redirect_429_and_network_errors_back_off(self):
        for msg in ("upstream redirected (302); not following", "upstream status 429",
                    "upstream status 500", "upstream unreachable: timed out"):
            self.assertTrouble(FakeGoogle(error=source.UpstreamError(msg)))

    def test_missing_attrs_and_garbage_back_off(self):
        self.assertTrouble(FakeGoogle(page=b"<html><body>Our systems have detected unusual traffic</body></html>"))
        self.assertTrouble(FakeGoogle(page=PAGE.replace(b"data-n-a-ts", b"data-x")))
        for batch in (b")]}'\n\n<html>nope", b")]}'\n\n[[\"wrb.fr\",\"Fbv4je\",\"{not json\"]]",
                      b")]}'\n\n[[\"wrb.fr\",\"Fbv4je\",null,null]]", b")]}'\n\n[[\"er\",null]]",
                      b")]}'\n\n[[\"wrb.fr\",\"Fbv4je\",\"[\\\"garturlres\\\"]\"]]", b""):
            self.assertTrouble(FakeGoogle(batch=batch))

    def test_decoded_url_validated(self):
        def batch_for(url):
            inner = json.dumps(["garturlres", url, 1])
            return (")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None]])).encode()
        for bad in ("javascript:alert(1)", "https://user:pw@example.com/x", "file:///etc/passwd",
                    "https://example.com/" + "a" * 3000, "https://exa mple.com/"):
            dec = gd.Decoder(fetch=FakeGoogle(batch=batch_for(bad)))
            with self.assertRaises(gd.DecodeError, msg=bad):
                dec.decode(SRC)
            self.assertFalse(dec.backing_off())  # Google itself was fine
        # http is a usable link; the publisher fetcher refuses it later.
        dec = gd.Decoder(fetch=FakeGoogle(batch=batch_for("http://example.com/story")))
        self.assertEqual(dec.decode(SRC), "http://example.com/story")


class HttpGetTests(unittest.TestCase):
    def test_http_get_only_talks_to_google_news(self):
        for bad in ("https://example.com/", "http://news.google.com/", "https://news.google.com:444/"):
            with self.assertRaises(source.UpstreamError):
                source.http_get(bad)


if __name__ == "__main__":
    unittest.main()
