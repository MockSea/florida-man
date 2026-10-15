import copy
import json

from fm import gnews_decode, publisher, server
from fm.summaries import (QUEUE_MAX, RETRY_FAILED, RETRY_REJECTED, Summaries, article_key,
                          make_summary)
from tests import test_server
from tests.helpers import DAY_ROUTES, AppTestCase, FakeFetch

HEADLINE = "Florida Man arrested after hitting deputy with a chicken nugget at Waffle House"
BLURB = ("A Florida man was arrested Tuesday after deputies said he hit one of them with a "
         "chicken nugget at a Waffle House in Fla. St. Petersburg. He said it slipped.")
PUB = "https://www.wfla.com/news/nugget-man/"


class FakeDecoder:
    backoff = 900

    def __init__(self, result=PUB, error=None):
        self.result, self.error = result, error
        self.calls = []
        self.trouble = False

    def backing_off(self):
        return self.trouble

    def decode(self, url):
        self.calls.append(url)
        if self.error:
            if isinstance(self.error, gnews_decode.DecoderTrouble):
                self.trouble = True
            raise self.error
        return self.result


class FakeFetcher:
    def __init__(self, blurb=BLURB, error=None):
        self.blurb_text, self.error = blurb, error
        self.calls = []

    def blurb(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.blurb_text


class Mono:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class SummaryTextTests(AppTestCase):
    def test_first_sentence_and_abbreviations(self):
        self.assertEqual(
            make_summary(BLURB, HEADLINE),
            "A Florida Man was arrested Tuesday after deputies said he hit one of them with a "
            "chicken nugget at a Waffle House in Fla. St. Petersburg.")

    def test_long_blurb_capped_on_word_boundary(self):
        long = "Deputies say the chicken nugget " + "flew across the Waffle House counter " * 20 + "today."
        out = make_summary(long, HEADLINE)
        self.assertLessEqual(len(out), 240)
        self.assertTrue(out.endswith("…"))
        self.assertTrue(long.startswith(out[:-1]))
        self.assertEqual(long[len(out) - 1], " ")  # cut on a word boundary

    def test_boilerplate_rejected(self):
        for blurb in ("Latest news.", "Florida's source for breaking news, weather, traffic and sports.",
                      "WFLA News Channel 8 is your local source for Tampa Bay news and weather.",
                      "Subscribe for unlimited access to the Florida Times-Union, all for $1.", ""):
            self.assertIsNone(make_summary(blurb, HEADLINE), blurb)

    def test_paraphrasing_blurbs_kept(self):
        # Real og:descriptions from 2026-10-14 that the old word-overlap rule rejected.
        for blurb in ("A trip to an Orlando grocery store turned into a life-changing payday for one local resident.",
                      "A Florida man we told you about over the summer completed his 3,100-mile journey last week.",
                      "Gregorio Delgado tells CBS News Miami he and his wife, 61-year-old Delia Annet Delgado, were soulmates."):
            self.assertIsNotNone(make_summary(blurb, HEADLINE), blurb)

    def test_html_in_blurb_cleaned(self):
        out = make_summary("<p>A <b>FLORIDA MAN</b> threw a chicken nugget &amp; a waffle at a deputy.</p>",
                           HEADLINE)
        self.assertEqual(out, "A Florida Man threw a chicken nugget & a waffle at a deputy.")


class EnrichTestCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self.t = [1_000_000.0]
        self.mono = Mono()

    def make(self, decoder=None, fetcher=None, per_minute=6, **kw):
        self.decoder = decoder or FakeDecoder()
        self.fetcher = fetcher or FakeFetcher()
        self.fetch = FakeFetch(DAY_ROUTES)
        app = self.make_app(self.fetch)
        self.stores[0].clock = lambda: self.t[0]
        self.sm = Summaries(self.stores[0], self.decoder, self.fetcher, per_minute=per_minute,
                            clock=lambda: self.t[0], monotonic=self.mono, log=lambda *a: None, **kw)
        app.summaries = self.sm
        return app


class OverlayTests(EnrichTestCase):
    def test_pending_then_overlaid(self):
        app = self.make()
        before = app.today_pick()
        st = before["story"]
        self.assertTrue(st["summaryPending"])
        self.assertEqual(st["summarySource"], "generated")
        self.assertTrue(st["sourceUrl"].startswith("https://news.google.com/"))
        cached = copy.deepcopy(self.stores[0].get("florida-man:2026-10-14")[0])

        self.assertTrue(self.sm.process_one())
        self.assertEqual(self.decoder.calls, [cached["story"]["sourceUrl"]])
        self.assertEqual(self.fetcher.calls, [PUB])

        after = app.today_pick()["story"]
        self.assertFalse(after["summaryPending"])
        self.assertEqual(after["summarySource"], "publisher")
        self.assertTrue(after["summary"].startswith("A Florida Man was arrested Tuesday"))
        self.assertEqual(after["sourceUrl"], PUB)
        self.assertEqual(app.story(st["id"])["story"], after)
        # The cached payload and the permalink row are untouched.
        self.assertEqual(self.stores[0].get("florida-man:2026-10-14")[0], cached)
        self.assertEqual(self.stores[0].get_story(st["id"])["summarySource"], "generated")
        self.assertNotIn("summaryPending", self.stores[0].get_story(st["id"]))

    def test_search_results_overlaid_and_queued(self):
        app = self.make()
        res = app.search(None, "2025-10-14")["results"]
        self.assertTrue(all(r["summaryPending"] for r in res))
        self.assertEqual(len(self.sm.queued()), len(res))
        while self.sm.process_one():
            pass
        res2 = app.search(None, "2025-10-14")["results"]
        self.assertTrue(all(not r["summaryPending"] for r in res2))
        self.assertEqual({r["sourceUrl"] for r in res2}, {PUB})

    def test_rejected_keeps_generated_but_links_publisher(self):
        app = self.make(fetcher=FakeFetcher(blurb="Your trusted source for Tampa Bay news and weather."))
        sid = app.today_pick()["story"]["id"]
        self.sm.process_one()
        st = app.story(sid)["story"]
        self.assertEqual(st["summarySource"], "generated")
        self.assertEqual(st["sourceUrl"], PUB)
        self.assertFalse(st["summaryPending"])

    def test_blocked_url_not_linked(self):
        app = self.make(fetcher=FakeFetcher(error=publisher.Blocked("resolves to a non-public address")))
        sid = app.today_pick()["story"]["id"]
        self.sm.process_one()
        st = app.story(sid)["story"]
        self.assertTrue(st["sourceUrl"].startswith("https://news.google.com/"))
        self.assertFalse(st["summaryPending"])

    def test_http_publisher_linked_not_fetched(self):
        app = self.make(decoder=FakeDecoder(result="http://old.example/story"))
        sid = app.today_pick()["story"]["id"]
        self.sm.process_one()
        self.assertEqual(self.fetcher.calls, [])
        self.assertEqual(app.story(sid)["story"]["sourceUrl"], "http://old.example/story")

    def test_no_summaries_means_not_pending(self):
        app = self.make_app(FakeFetch(DAY_ROUTES))
        self.assertFalse(app.today_pick()["story"]["summaryPending"])

    def test_not_pending_while_google_backs_off(self):
        app = self.make()
        self.decoder.trouble = True
        self.assertFalse(app.today_pick()["story"]["summaryPending"])


class NegativeCacheTests(EnrichTestCase):
    def check_retry(self, app, retry_in):
        sid = app.today_pick()["story"]["id"]
        self.assertTrue(self.sm.process_one())
        while self.sm.queued():  # the day's runners-up are queued behind the pick
            self.mono.t += 60
            self.sm.process_one()
        n = (len(self.decoder.calls), len(self.fetcher.calls))
        self.t[0] += retry_in - 10
        self.mono.t += 3600
        self.assertFalse(app.story(sid)["story"]["summaryPending"])
        self.assertFalse(self.sm.process_one())
        self.assertEqual((len(self.decoder.calls), len(self.fetcher.calls)), n)
        self.t[0] += 20
        self.assertTrue(app.story(sid)["story"]["summaryPending"])
        self.assertTrue(self.sm.process_one())

    def test_failed_retried_after_a_day(self):
        self.check_retry(self.make(fetcher=FakeFetcher(error=publisher.FetchError("status 404"))), RETRY_FAILED)

    def test_rejected_retried_after_a_month(self):
        self.check_retry(self.make(fetcher=FakeFetcher(blurb="Breaking news.")), RETRY_REJECTED)

    def test_decoder_trouble_retries_after_backoff(self):
        dec = FakeDecoder(error=gnews_decode.DecoderTrouble("upstream status 429"))
        app = self.make(decoder=dec)
        app.today_pick()
        self.assertTrue(self.sm.process_one())
        app.search(None, "2025-10-14")  # more work queued...
        self.assertFalse(self.sm.process_one())  # ...but nothing runs while backing off
        self.assertEqual(len(dec.calls), 1)
        row = list(self.stores[0].get_summaries(
            [article_key(app.today_pick()["story"]["sourceUrl"])]).values())[0]
        self.assertEqual(row["status"], "failed")
        self.assertAlmostEqual(row["retryAt"] - self.t[0], dec.backoff)

    def test_unexpected_error_becomes_failed_row(self):
        app = self.make(fetcher=FakeFetcher(error=RuntimeError("bug")))
        app.today_pick()
        self.assertTrue(self.sm.process_one())
        rows = self.stores[0].get_summaries(self.sm.queued() + [article_key(
            "https://news.google.com/rss/articles/CBMiAAA111?oc=5")])
        self.assertEqual([r["status"] for r in rows.values()], ["failed"])


class BudgetAndQueueTests(EnrichTestCase):
    def test_budget(self):
        app = self.make(per_minute=2)
        app.search(None, "2025-10-14")
        self.assertGreater(len(self.sm.queued()), 2)
        self.assertTrue(self.sm.process_one())
        self.assertTrue(self.sm.process_one())
        self.assertFalse(self.sm.process_one())
        self.assertEqual(len(self.decoder.calls), 2)
        self.mono.t += 61
        self.assertTrue(self.sm.process_one())

    def test_zero_disables(self):
        app = self.make(per_minute=0)
        st = app.today_pick()["story"]
        self.assertFalse(st["summaryPending"])
        self.assertEqual(self.sm.queued(), [])
        self.assertFalse(self.sm.process_one())
        self.assertIsNone(self.sm.start())
        self.assertEqual(self.decoder.calls, [])

    def test_config_range(self):
        import os
        from unittest import mock
        from fm import config
        for val, want in (("", 6), ("0", 0), ("30", 30)):
            with mock.patch.dict(os.environ, {"FM_SUMMARIES_PER_MINUTE": val}):
                self.assertEqual(config.Config.from_env().summaries_per_minute, want)
        for bad in ("31", "-1"):
            with mock.patch.dict(os.environ, {"FM_SUMMARIES_PER_MINUTE": bad}):
                with self.assertRaises(ValueError):
                    config.Config.from_env()

    def test_bound_dedupe_priority(self):
        self.make(queue_max=3)
        urls = ["https://news.google.com/rss/articles/CBMi%06d?oc=5" % i for i in range(5)]
        keys = [article_key(u) for u in urls]
        for u in urls[:4]:
            self.sm.enqueue(u, "h")
        self.assertEqual(self.sm.queued(), keys[:3])  # bounded
        self.assertTrue(self.sm.enqueue(urls[0], "h"))  # dedupe
        self.assertEqual(self.sm.queued(), keys[:3])
        self.assertTrue(self.sm.enqueue(urls[2], "h", priority=True))  # jumps the queue
        self.assertEqual(self.sm.queued(), [keys[2], keys[0], keys[1]])
        self.assertTrue(self.sm.enqueue(urls[4], "h", priority=True))  # evicts newest normal
        self.assertEqual(self.sm.queued(), [keys[4], keys[2], keys[0]])
        self.assertEqual(QUEUE_MAX, 200)

    def test_story_request_jumps_the_queue(self):
        app = self.make()
        res = app.search(None, "2025-10-14")["results"]
        last = res[-1]
        app.story(last["id"])
        self.assertEqual(self.sm.queued()[0], article_key(last["sourceUrl"]))
        self.sm.process_one()
        self.assertEqual(self.decoder.calls, [last["sourceUrl"]])

    def test_overflowing_queue_is_not_pending(self):
        app = self.make(queue_max=1)
        res = app.search(None, "2025-10-14")["results"]
        self.assertEqual([r["summaryPending"] for r in res], [True] + [False] * (len(res) - 1))


class CacheHeaderTests(EnrichTestCase):
    serve_one = test_server.HttpTests.serve_one
    request = test_server.HttpTests.request
    get_json = test_server.HttpTests.get_json

    def setUp(self):
        super().setUp()
        self.app = self.make()
        self.handler = server.make_handler(self.app, test_server.STATIC)
        self.handler.log_message = lambda *a: None

    def test_pending_story_not_browser_cached(self):
        _, h, data = self.get_json("/api/today")
        self.assertTrue(data["story"]["summaryPending"])
        self.assertEqual(h["cache-control"], "no-store")
        sid = data["story"]["id"]
        _, h, data = self.get_json("/api/story?id=" + sid)
        self.assertEqual(h["cache-control"], "no-store")
        self.sm.process_one()
        _, h, data = self.get_json("/api/story?id=" + sid)
        self.assertEqual(data["story"]["summarySource"], "publisher")
        self.assertEqual(h["cache-control"], "public, max-age=3600")
        json.dumps(data)
