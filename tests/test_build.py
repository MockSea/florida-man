"""Static build: archive merge, blocked upstream, the written site. Offline."""
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

from fm import build, gnews_decode, server, source
from fm.summaries import article_key
from tests.helpers import DAY_ROUTES, NOW, FakeFetch
from tests.test_summaries import PUB, FakeDecoder, FakeFetcher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
TOWNS = ["Tampa", "Ocala", "Port St. Lucie"]


def feed_for(day, n=3):
    """A small RSS feed with n stories published on `day`, one per town."""
    items = []
    for i in range(n):
        town = TOWNS[i % len(TOWNS)]
        items.append(
            "<item><title>Florida man in %s arrested with alligator number %d - WFLA</title>"
            "<link>https://news.google.com/rss/articles/CBMi%s%02d?oc=5</link>"
            "<pubDate>%s 15:00:00 GMT</pubDate><description>x</description>"
            '<source url="https://www.wfla.com">WFLA</source></item>'
            % (town, i, day.strftime("%Y%m%d"), i, day.strftime("%a, %d %b %Y")))
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>%s</channel></rss>'
            % "".join(items)).encode("utf-8")


class AnyDayFetch:
    """Every query gets n stories on the day its after:/before: window brackets.
    `block_after` makes the k-th and later calls look like Google's sorry page."""

    def __init__(self, n=3, block_after=None):
        self.n = n
        self.block_after = block_after
        self.queries = []

    def __call__(self, url, **kw):
        q = parse_qs(urlsplit(url).query)["q"][0]
        self.queries.append(q)
        if self.block_after is not None and len(self.queries) > self.block_after:
            raise source.UpstreamError("upstream redirected (302); not following")
        after = re.search(r"after:(\d{4}-\d{2}-\d{2})", q).group(1)
        return feed_for(date.fromisoformat(after) + timedelta(days=1), self.n)


class Clock:
    """Wall and monotonic time that sleep() advances."""

    def __init__(self, start=NOW.timestamp()):
        self.t = start

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class BuildTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fm-build-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.archive = os.path.join(self.tmp, "archive")
        self.out = os.path.join(self.tmp, "site")
        self.log = []

    def run_build(self, fetch, now=NOW, decoder=None, fetcher=None, **kw):
        clock = Clock(now.timestamp())
        kw.setdefault("summaries_per_minute", 30)
        with redirect_stderr(io.StringIO()):
            return build.run(self.out, archive_dir=self.archive, static_dir=STATIC, fetch=fetch,
                             decoder=decoder or FakeDecoder(), fetcher=fetcher or FakeFetcher(),
                             now=lambda: now, clock=clock, monotonic=clock, sleep=clock.sleep,
                             log=lambda fmt, *a: self.log.append(fmt % a), **kw)

    def archive_files(self):
        out = {}
        for dirpath, _, files in os.walk(self.archive):
            for fn in files:
                p = os.path.join(dirpath, fn)
                with open(p, encoding="utf-8") as f:
                    out[os.path.relpath(p, self.archive)] = f.read()
        return out

    def site_json(self, rel):
        with open(os.path.join(self.out, rel), encoding="utf-8") as f:
            return json.load(f)


class WritesSiteTests(BuildTestCase):
    def test_expected_files(self):
        self.assertEqual(self.run_build(FakeFetch(DAY_ROUTES)), 0)
        self.assertEqual(list(self.archive_files()), [os.path.join("2026", "10-14.json")])
        for rel in ("index.html", "app.js", "style.css", "fonts/archivo.woff2",
                    "data/today.json", "data/days/2026-10-14.json", "data/index.json",
                    "data/towns.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, rel)), rel)
        self.assertFalse(any(n.startswith(".") for n in os.listdir(self.out)))
        today = self.site_json("data/today.json")
        self.assertEqual(today, self.site_json("data/days/2026-10-14.json"))
        self.assertEqual(today["date"], "2026-10-14")
        self.assertEqual(today["shareUrl"], "?date=2026-10-14")
        st = today["story"]
        # The publisher summary and link are baked in; nothing is pending.
        self.assertEqual(st["summarySource"], "publisher")
        self.assertEqual(st["sourceUrl"], PUB)
        self.assertFalse(st["summaryPending"])
        self.assertEqual(st["shareUrl"], "?story=" + st["id"])
        for s in today["more"]:
            self.assertFalse(s["summaryPending"])
            self.assertTrue(s["shareUrl"].startswith("?story="))
        self.assertNotIn("cacheKey", today)
        towns = self.site_json("data/towns.json")
        self.assertIn("Tampa", towns["towns"])
        self.assertEqual(towns["aliases"]["st pete"], "St. Petersburg")

    def test_archive_keeps_raw_stories_and_summary_rows(self):
        self.run_build(FakeFetch(DAY_ROUTES))
        rec = json.loads(self.archive_files()[os.path.join("2026", "10-14.json")])
        self.assertTrue(rec["story"]["sourceUrl"].startswith("https://news.google.com/"))
        row = rec["summaries"][article_key(rec["story"]["sourceUrl"])]
        self.assertEqual((row["status"], row["publisherUrl"]), ("ok", PUB))
        self.assertIsInstance(row["fetched"], int)

    def test_html_paths_relative_and_meta_csp(self):
        self.run_build(FakeFetch(DAY_ROUTES))
        with open(os.path.join(self.out, "index.html"), encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(self.out, "style.css"), encoding="utf-8") as f:
            css = f.read()
        self.assertEqual(build.absolute_urls(html), [])
        self.assertEqual(build.absolute_urls(css), [])
        for attr in re.findall(r'(?:href|src|action)="([^"]*)"', html):
            self.assertFalse(attr.startswith("/") or "//" in attr, attr)
        m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', html)
        self.assertIsNotNone(m)
        csp = m.group(1)
        self.assertLess(html.index("Content-Security-Policy"), html.index("<link"))
        for directive in ("default-src 'self'", "script-src 'self'", "style-src 'self'",
                          "font-src 'self'", "img-src 'self' data:", "connect-src 'self'",
                          "object-src 'none'", "base-uri 'none'"):
            self.assertIn(directive, csp)
        self.assertNotIn("unsafe", csp)
        self.assertNotIn("http", csp)
        self.assertIn('<meta name="fm-mode" content="static">', html)
        self.assertIn('<meta name="referrer" content="no-referrer">', html)
        self.assertNotIn("<script>", html)

    def test_server_csp_has_no_third_parties(self):
        self.assertNotIn("http", server.CSP)
        self.assertIn("frame-ancestors 'none'", server.CSP)

    def test_absolute_url_check_catches_slashes(self):
        self.assertEqual(build.absolute_urls('<a href="/x">'), ["/x"])
        self.assertEqual(build.absolute_urls('<script src="//evil.example/a.js">'),
                         ["//evil.example/a.js"])
        self.assertEqual(build.absolute_urls('src: url("/fonts/a.woff2")'), ["/fonts/a.woff2"])
        self.assertEqual(build.absolute_urls('<a href="./"><link href="style.css">'), [])

    def test_refuses_to_clear_a_foreign_directory(self):
        os.makedirs(self.out)
        with open(os.path.join(self.out, "precious.txt"), "w") as f:
            f.write("keep me")
        with self.assertRaises(build.BuildError):
            self.run_build(FakeFetch(DAY_ROUTES))
        self.assertTrue(os.path.exists(os.path.join(self.out, "precious.txt")))
        with self.assertRaises(build.BuildError):
            build._prepare_out(ROOT, (STATIC,))

    def test_rebuild_replaces_old_output(self):
        self.run_build(FakeFetch(DAY_ROUTES))
        stray = os.path.join(self.out, "data", "days", "1999-01-01.json")
        with open(stray, "w") as f:
            f.write("{}")
        self.run_build(FakeFetch(DAY_ROUTES))
        self.assertFalse(os.path.exists(stray))


class MergeTests(BuildTestCase):
    def test_rerun_is_idempotent(self):
        self.run_build(FakeFetch(DAY_ROUTES))
        first = self.archive_files()
        fetch = FakeFetch(DAY_ROUTES)
        decoder = FakeDecoder()
        self.run_build(fetch, decoder=decoder)
        self.assertEqual(self.archive_files(), first)
        # The archived pick is served as cached: no news queries, no decodes.
        self.assertEqual(fetch.queries, [])
        self.assertEqual(decoder.calls, [])

    def test_pick_survives_a_changed_feed(self):
        self.run_build(FakeFetch(DAY_ROUTES))
        pick = self.site_json("data/today.json")["story"]["id"]
        self.run_build(AnyDayFetch(n=5))  # different stories for the same day
        self.assertEqual(self.site_json("data/today.json")["story"]["id"], pick)

    def test_stable_order(self):
        self.run_build(FakeFetch(DAY_ROUTES))
        text = self.archive_files()[os.path.join("2026", "10-14.json")]
        rec = json.loads(text)
        self.assertEqual(text, build.dump_archive(rec))
        self.assertEqual(list(rec), sorted(rec))
        self.assertEqual(list(rec["story"]), sorted(rec["story"]))
        self.assertTrue(text.endswith("}\n"))
        # Shuffled key order and the same content dump to the same bytes.
        shuffled = json.loads(text, object_pairs_hook=lambda kv: dict(reversed(kv)))
        self.assertEqual(build.dump_archive(shuffled), text)

    def test_backfill_adds_days_and_rerun_keeps_them(self):
        self.assertEqual(self.run_build(AnyDayFetch(), backfill=3), 0)
        files = self.archive_files()
        self.assertEqual(sorted(files), [os.path.join("2026", "10-%02d.json" % d)
                                         for d in range(11, 15)])
        self.run_build(AnyDayFetch(), backfill=3)
        self.assertEqual(self.archive_files(), files)

    def test_backfill_waits_out_the_news_budget(self):
        fetch = AnyDayFetch()
        # 4 queries a day, 8 a minute: the 3rd day has to wait for budget.
        self.run_build(fetch, backfill=4, fetches_per_minute=8)
        self.assertEqual(len(self.archive_files()), 5)
        self.assertEqual(len(fetch.queries), 20)

    def test_summaries_wait_out_their_budget(self):
        fetcher = FakeFetcher()
        self.run_build(AnyDayFetch(n=5), summaries_per_minute=2, fetcher=fetcher)
        self.assertEqual(len(fetcher.calls), 5)
        rec = json.loads(self.archive_files()[os.path.join("2026", "10-14.json")])
        fetched = sorted(row["fetched"] for row in rec["summaries"].values())
        self.assertGreaterEqual(fetched[-1] - fetched[0], 120)  # 2 a minute

    def test_failed_summary_retried_later_and_kept(self):
        self.run_build(FakeFetch(DAY_ROUTES),
                       fetcher=FakeFetcher(error=build_fetch_error()))
        rec = json.loads(self.archive_files()[os.path.join("2026", "10-14.json")])
        row = rec["summaries"][article_key(rec["story"]["sourceUrl"])]
        self.assertEqual(row["status"], "failed")
        # Same day again: not due yet, so not retried.
        fetcher = FakeFetcher()
        self.run_build(FakeFetch(DAY_ROUTES), fetcher=fetcher)
        self.assertEqual(fetcher.calls, [])
        # Two days later it is due, and the retry sticks.
        later = NOW + timedelta(days=2)
        fetcher = FakeFetcher()
        self.run_build(AnyDayFetch(), now=later, fetcher=fetcher)
        self.assertIn(PUB, fetcher.calls)
        old = json.loads(self.archive_files()[os.path.join("2026", "10-14.json")])
        row = old["summaries"][article_key(old["story"]["sourceUrl"])]
        self.assertEqual(row["status"], "ok")
        self.assertEqual(self.site_json("data/days/2026-10-14.json")["story"]["summarySource"],
                         "publisher")

    def test_summary_cap_keeps_existing_rows(self):
        self.run_build(AnyDayFetch(n=6), max_summaries=2)
        rec = json.loads(self.archive_files()[os.path.join("2026", "10-14.json")])
        self.assertEqual(len(rec["summaries"]), 2)
        # The next run does the rest and keeps the first two.
        self.run_build(AnyDayFetch(n=6), max_summaries=10)
        rec2 = json.loads(self.archive_files()[os.path.join("2026", "10-14.json")])
        self.assertEqual(len(rec2["summaries"]), 6)
        for k, row in rec["summaries"].items():
            self.assertEqual(rec2["summaries"][k], row)

    def test_broken_archive_file_is_skipped_not_rewritten(self):
        path = os.path.join(self.archive, "2026", "10-01.json")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write("{not json")
        self.run_build(FakeFetch(DAY_ROUTES))
        with open(path) as f:
            self.assertEqual(f.read(), "{not json")
        self.assertTrue(any("skipping" in line for line in self.log))
        self.assertFalse(os.path.exists(os.path.join(self.out, "data", "days", "2026-10-01.json")))


def build_fetch_error():
    from fm.publisher import FetchError
    return FetchError("network: timeout")


class BlockedTests(BuildTestCase):
    def test_blocked_leaves_archive_intact_and_publishes(self):
        self.run_build(AnyDayFetch(), backfill=2)
        before = self.archive_files()
        # A day later Google sends its sorry page after the first query.
        later = NOW + timedelta(days=1)
        fetch = AnyDayFetch(block_after=1)
        code = self.run_build(fetch, now=later, backfill=5)
        self.assertEqual(code, 0)
        self.assertEqual(self.archive_files(), before)
        self.assertEqual(len(fetch.queries), 2)  # stopped at the first refusal
        blocked = [l for l in self.log if "blocking this runner" in l]
        self.assertEqual(len(blocked), 1)
        # The site still has every archived day; today falls back to the latest.
        index = self.site_json("data/index.json")
        self.assertEqual(sorted(index["days"]), ["2026-10-12", "2026-10-13", "2026-10-14"])
        self.assertEqual(self.site_json("data/today.json")["date"], "2026-10-14")

    def test_index_marks_grim_stories_off_the_dice(self):
        self.assertEqual(self.run_build(FakeFetch(DAY_ROUTES)), 0)
        rows = [r for rows in self.site_json("data/index.json")["days"].values() for r in rows]
        self.assertTrue(rows)
        self.assertTrue(all(len(r) == 5 and r[4] in (0, 1) for r in rows))
        day = self.site_json("data/days/2026-10-14.json")
        stories = ([day["story"]] if day.get("story") else []) + day.get("more", [])
        grim = {st["id"] for st in stories if "murder" in st["headline"]}
        self.assertTrue(grim)
        self.assertEqual({r[0] for r in rows if r[4] == 0}, grim)
        flags = [st["id"] in grim for st in day.get("more", [])]
        self.assertEqual(flags, sorted(flags))  # grim runners-up last

    def test_blocked_with_empty_archive_exits_nonzero(self):
        code = self.run_build(AnyDayFetch(block_after=0))
        self.assertEqual(code, 1)
        self.assertEqual(self.archive_files(), {})
        self.assertFalse(os.path.exists(self.out))

    def test_decoder_trouble_stops_summaries_but_keeps_stories(self):
        decoder = FakeDecoder(error=gnews_decode.DecoderTrouble("sorry page"))
        self.assertEqual(self.run_build(FakeFetch(DAY_ROUTES), decoder=decoder), 0)
        self.assertEqual(len(decoder.calls), 1)  # backed off after the first
        rec = json.loads(self.archive_files()[os.path.join("2026", "10-14.json")])
        self.assertIsNotNone(rec["story"])
        self.assertTrue(any("pushed back" in l for l in self.log))

    def test_breaker_trips_only_on_blocks(self):
        calls = []

        def flaky(url, **kw):
            calls.append(url)
            raise source.UpstreamError("upstream unreachable: timed out")

        b = build.Breaker(flaky)
        for _ in range(2):
            with self.assertRaises(source.UpstreamError):
                b("https://news.google.com/x")
        self.assertIsNone(b.reason)
        self.assertEqual(len(calls), 2)
        b = build.Breaker(AnyDayFetch(block_after=0))
        for _ in range(2):
            with self.assertRaises(source.UpstreamError):
                b("https://news.google.com/rss/search?q=after:2026-01-01")
        self.assertIn("redirected", b.reason)
        self.assertEqual(len(b.fetch.queries), 1)


class IndexTests(BuildTestCase):
    def test_index_matches_day_files(self):
        self.run_build(AnyDayFetch(n=4), backfill=6)
        index = self.site_json("data/index.json")
        self.assertEqual(index["version"], build.INDEX_VERSION)
        self.assertEqual(index["latest"], "2026-10-14")
        day_files = sorted(os.listdir(os.path.join(self.out, "data", "days")))
        self.assertEqual(day_files, sorted(d + ".json" for d in index["days"]))
        seen = set()
        for d, entries in index["days"].items():
            payload = self.site_json("data/days/%s.json" % d)
            stories = {s["id"]: s for s in [payload["story"]] + payload["more"] if s}
            for sid, incident, town, score, _random_ok in entries:
                self.assertNotIn(sid, seen)
                seen.add(sid)
                self.assertEqual(stories[sid]["incidentDate"], incident)
                self.assertEqual(stories[sid]["town"], town)
                self.assertEqual(stories[sid]["score"], score)
        # Every story in every day file is findable through the index.
        for fn in day_files:
            payload = self.site_json("data/days/" + fn)
            for s in [payload["story"]] + payload["more"]:
                if s:
                    self.assertIn(s["id"], seen)

    def test_story_in_two_days_is_listed_once(self):
        self.run_build(FakeFetch(DAY_ROUTES))
        rec = json.loads(self.archive_files()[os.path.join("2026", "10-14.json")])
        twin = dict(rec, date="2026-10-13")
        path = build.archive_path(self.archive, date(2026, 10, 13))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(build.dump_archive(twin))
        self.run_build(FakeFetch(DAY_ROUTES))
        index = self.site_json("data/index.json")
        self.assertEqual(index["days"]["2026-10-13"], [])
        self.assertEqual(len(index["days"]["2026-10-14"]), 1 + len(rec["more"]))

    def test_empty_day_is_collected_and_shown(self):
        self.run_build(FakeFetch())  # nothing anywhere
        today = self.site_json("data/today.json")
        self.assertTrue(today["empty"])
        self.assertIsNone(today["story"])
        self.assertEqual(today["message"], "Florida Man was, surprisingly, well-behaved today.")
        self.assertEqual(self.site_json("data/index.json")["days"], {"2026-10-14": []})


class CliTests(unittest.TestCase):
    def test_backfill_bounds(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                build.main(["--out", "x", "--backfill", "-1"])
            with self.assertRaises(SystemExit):
                build.main(["--out", "x", "--backfill", str(build.MAX_BACKFILL + 1)])


if __name__ == "__main__":
    unittest.main()
