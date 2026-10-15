import unittest
from datetime import date, datetime

from fm import source
from fm.service import EMPTY_MESSAGE
from fm.validate import ApiError
from tests.helpers import DAY_ROUTES, ET, TAMPA_ROUTES, AppTestCase, FakeFetch

NUGGET = source.story_id("https://news.google.com/rss/articles/CBMiAAA111?oc=5")


class DailyPickTests(AppTestCase):
    def test_pick_and_shape(self):
        fetch = FakeFetch(DAY_ROUTES)
        out = self.make_app(fetch).today_pick()
        self.assertEqual(out["date"], "2026-10-14")
        self.assertEqual(out["cacheKey"], "florida-man:2026-10-14")
        self.assertFalse(out["empty"])
        st = out["story"]
        self.assertEqual(st["id"], NUGGET)
        self.assertEqual(st["headline"],
                         "Florida Man arrested after hitting deputy with a chicken nugget at Waffle House")
        self.assertEqual(st["incidentDate"], "2025-10-14")
        self.assertIsNone(st["imageUrl"])
        self.assertEqual(st["scoreLabel"], "subjective")
        self.assertEqual(st["summarySource"], "generated")
        self.assertEqual(st["shareUrl"], "/?story=" + NUGGET)
        self.assertTrue(st["sourceUrl"].startswith("https://"))
        # Four prior years searched, newest first, one query each.
        self.assertEqual(out["yearsSearched"], [2025, 2024, 2023, 2022])
        self.assertEqual(fetch.queries[0], '"Florida man" after:2025-10-13 before:2025-10-15')

    def test_same_date_same_pick(self):
        a = self.make_app(FakeFetch(DAY_ROUTES)).today_pick()
        # Fresh store dir each time would refetch; same feed must give same pick.
        self.setUp()
        b = self.make_app(FakeFetch(DAY_ROUTES)).today_pick()
        self.assertEqual(a["story"]["id"], b["story"]["id"])
        self.assertEqual(a["story"]["score"], b["story"]["score"])

    def test_cache_hit_does_not_refetch(self):
        fetch = FakeFetch(DAY_ROUTES)
        app = self.make_app(fetch)
        first = app.today_pick()
        n = len(fetch.queries)
        for _ in range(5):
            self.assertEqual(app.today_pick(), first)
        self.assertEqual(len(fetch.queries), n)

    def test_cache_survives_restart(self):
        first = self.make_app(FakeFetch(DAY_ROUTES)).today_pick()
        self.stores[0].close()
        fetch2 = FakeFetch(fail=True)  # would error if it tried the network
        second = self.make_app(fetch2).today_pick()
        self.assertEqual(second, first)
        self.assertEqual(fetch2.queries, [])

    def test_timezone_day_boundary(self):
        # 02:00 UTC on Oct 15 is still Oct 14 in Florida.
        out = self.make_app(FakeFetch(DAY_ROUTES), home_years=1).today_pick()
        self.assertEqual(out["story"]["incidentDate"], "2025-10-14")

    def test_grim_not_picked(self):
        out = self.make_app(FakeFetch(DAY_ROUTES)).today_pick()
        self.assertNotIn("murder", out["story"]["headline"])

    def test_empty_state(self):
        out = self.make_app(FakeFetch()).today_pick()
        self.assertTrue(out["empty"])
        self.assertIsNone(out["story"])
        self.assertEqual(out["message"], "Florida Man was, surprisingly, well-behaved today.")
        self.assertEqual(EMPTY_MESSAGE, out["message"])

    def test_upstream_down_errors_and_is_not_cached(self):
        app = self.make_app(FakeFetch(fail=True))
        with self.assertRaises(ApiError) as cm:
            app.today_pick()
        self.assertEqual(cm.exception.code, "upstream_error")
        self.assertEqual(self.stores[0].get("florida-man:2026-10-14"), (None, False))

    def test_rate_limited(self):
        app = self.make_app(FakeFetch(DAY_ROUTES), per_minute=2)
        with self.assertRaises(ApiError) as cm:
            app.today_pick()  # needs 4 fetches
        self.assertEqual(cm.exception.status, 503)
        self.assertIn("retryAfter", cm.exception.extra)

    def test_feb29_skips_non_leap_years(self):
        fetch = FakeFetch()
        now = datetime(2028, 2, 29, 12, tzinfo=ET)
        out = self.make_app(fetch, now=now).today_pick()
        self.assertEqual(out["yearsSearched"], [2024])  # 2027-2024: only 2024 is leap
        self.assertTrue(out["empty"])

    def test_past_day_permalink(self):
        app = self.make_app(FakeFetch({"after:2024-10-13 before:2024-10-15": "day_2025-10-14.xml"}))
        out = app.day(date(2025, 10, 14))
        self.assertEqual(out["cacheKey"], "florida-man:2025-10-14")
        self.assertEqual(out["shareUrl"], "/?date=2025-10-14")


class SearchTests(AppTestCase):
    def test_month_day_search(self):
        fetch = FakeFetch(TAMPA_ROUTES)
        out = self.make_app(fetch).search("tampa", "10-14")
        self.assertEqual(out["query"]["town"], "Tampa")
        self.assertEqual(out["query"]["mode"], "month-day")
        self.assertEqual(out["query"]["datesSearched"], ["2026-10-14", "2025-10-14", "2024-10-14"])
        self.assertEqual(out["count"], 2)
        self.assertEqual({r["town"] for r in out["results"]}, {"Tampa"})
        scores = [r["score"] for r in out["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(out["shareUrl"], "/?town=Tampa&date=10-14")
        self.assertTrue(all('"Tampa"' in q for q in fetch.queries))

    def test_full_date_search_and_cache(self):
        fetch = FakeFetch(TAMPA_ROUTES)
        app = self.make_app(fetch)
        out = app.search("Tampa", "2024-10-14")
        self.assertEqual(out["query"]["mode"], "full")
        self.assertEqual(len(fetch.queries), 1)
        self.assertEqual(out["count"], 2)
        app.search("TAMPA", "2024-10-14")
        self.assertEqual(len(fetch.queries), 1)

    def test_empty_search(self):
        out = self.make_app(FakeFetch()).search("Yankeetown", "2025-01-02")
        self.assertTrue(out["empty"])
        self.assertEqual(out["results"], [])
        self.assertEqual(out["message"], EMPTY_MESSAGE)

    def test_bad_town(self):
        app = self.make_app(FakeFetch())
        for bad, code in (("Atlanta", "invalid_town"), ("<script>", "invalid_town")):
            with self.assertRaises(ApiError) as cm:
                app.search(bad, "10-14")
            self.assertEqual(cm.exception.code, code)
            self.assertEqual(cm.exception.status, 400)
        with self.assertRaises(ApiError) as cm:
            app.search("Tampaa", "10-14")
        self.assertIn("Tampa", cm.exception.extra["suggestions"])

    def test_statewide_search_without_town(self):
        for blank in (None, "", "   "):
            self.setUp()
            fetch = FakeFetch(DAY_ROUTES)
            out = self.make_app(fetch).search(blank, "2025-10-14")
            self.assertIsNone(out["query"]["town"])
            self.assertEqual(out["query"]["mode"], "full")
            self.assertFalse(out["empty"])
            self.assertEqual(out["shareUrl"], "/?scope=statewide&date=2025-10-14")
            self.assertEqual(fetch.queries, ['"Florida man" after:2025-10-13 before:2025-10-15'])

    def test_statewide_and_town_caches_are_separate(self):
        fetch = FakeFetch(TAMPA_ROUTES)
        app = self.make_app(fetch)
        app.search("Tampa", "10-14")
        n = len(fetch.queries)
        app.search(None, "10-14")
        self.assertEqual(len(fetch.queries), 2 * n)
        app.search("", "10-14")
        app.search("tampa", "10-14")
        self.assertEqual(len(fetch.queries), 2 * n)

    def test_bad_dates(self):
        app = self.make_app(FakeFetch())
        cases = {"13-01": "invalid_date", "02-30": "invalid_date", "2025-02-29": "invalid_date",
                 "10/14": "invalid_date", "2030-01-01": "date_out_of_range",
                 "1899-12-31": "date_out_of_range", "": "missing_date", "x" * 50: "invalid_date",
                 "2025-1-14": "invalid_date", "１０-１４": "invalid_date"}
        for val, code in cases.items():
            with self.assertRaises(ApiError, msg=val) as cm:
                app.search("Tampa", val)
            self.assertEqual(cm.exception.code, code, val)
        self.assertEqual(app.search("Tampa", "02-29")["query"]["mode"], "month-day")

    def test_stale_served_when_rate_limited(self):
        t = [1000.0]
        app = self.make_app(FakeFetch(TAMPA_ROUTES), per_minute=1)
        self.stores[0].clock = lambda: t[0]
        app.search("Tampa", "2024-10-14")
        t[0] += 7200  # cache expired
        out = app.search("Tampa", "2024-10-14")  # limiter has no tokens left
        self.assertTrue(out["stale"])
        self.assertEqual(out["count"], 2)


class CarouselTests(AppTestCase):
    def test_more_holds_runners_up_in_score_order(self):
        app = self.make_app(FakeFetch(DAY_ROUTES))
        out = app.today_pick()
        more = out["more"]
        self.assertTrue(more)
        self.assertLessEqual(len(more), 9)
        self.assertNotIn(out["story"]["id"], [m["id"] for m in more])
        scores = [out["story"]["score"]] + [m["score"] for m in more]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for m in more:  # each runner-up has a working permalink
            self.assertEqual(app.story(m["id"])["story"]["id"], m["id"])

    def test_empty_day_has_empty_more(self):
        self.assertEqual(self.make_app(FakeFetch()).today_pick()["more"], [])

    def old_cached_day(self, app):
        """Store a day the way it was cached before `more` existed."""
        out = app.today_pick()
        old = {k: v for k, v in out.items() if k != "more"}
        old["story"] = {k: v for k, v in out["story"].items() if k != "summaryPending"}
        self.stores[0].put(out["cacheKey"], old)
        return out

    def test_backfill_keeps_cached_pick(self):
        app = self.make_app(FakeFetch(DAY_ROUTES))
        self.old_cached_day(app)
        key = "florida-man:2026-10-14"
        stored, _ = self.stores[0].get(key)
        stored["story"] = dict(stored["story"], id="kept-pick", headline="An older pick")
        self.stores[0].put(key, stored)
        out = app.today_pick()
        self.assertEqual(out["story"]["id"], "kept-pick")
        self.assertTrue(out["more"])
        self.assertIn("more", self.stores[0].get(key)[0])  # stored, not refetched next time

    def test_backfill_failure_returns_empty_and_retries_later(self):
        app = self.make_app(FakeFetch(DAY_ROUTES))
        self.old_cached_day(app)
        self.stores[0].close()
        app2 = self.make_app(FakeFetch(fail=True))
        out = app2.today_pick()
        self.assertEqual(out["more"], [])
        self.assertNotIn("more", self.stores[1].get("florida-man:2026-10-14")[0])


class StoryAndTrackTests(AppTestCase):
    def test_search_does_not_clobber_homepage_story(self):
        app = self.make_app(FakeFetch({**DAY_ROUTES, '"Tampa" after:2025-10-13': "day_2025-10-14.xml"}))
        pick = app.today_pick()["story"]
        res = app.search("Tampa", "2025-10-14")["results"]
        same = [r for r in res if r["sourceUrl"] == pick["sourceUrl"]][0]
        self.assertNotEqual(same["id"], pick["id"])
        self.assertIsNone(app.story(pick["id"])["story"]["town"])
        self.assertEqual(app.story(same["id"])["story"]["town"], "Tampa")

    def test_story_permalink(self):
        app = self.make_app(FakeFetch(DAY_ROUTES))
        pick = app.today_pick()["story"]
        self.assertEqual(app.story(pick["id"])["story"], pick)
        with self.assertRaises(ApiError) as cm:
            app.story("0" * 16)
        self.assertEqual(cm.exception.status, 404)
        with self.assertRaises(ApiError) as cm:
            app.story("../../etc")
        self.assertEqual(cm.exception.code, "invalid_story_id")

    def test_track(self):
        app = self.make_app(FakeFetch())
        app.track({"event": "share", "storyId": NUGGET})
        app.track({"event": "copy"})
        app.track({"event": "copy"})
        self.assertEqual(self.stores[0].counters(),
                         {"copy:2026-10-14": 2, "copy:total": 2,
                          "share:2026-10-14": 1, "share:total": 1})
        for bad in ({"event": "hack"}, {}, [], {"event": "copy", "storyId": "x"}):
            with self.assertRaises(ApiError):
                app.track(bad)


if __name__ == "__main__":
    unittest.main()


class BirthdayFallbackTests(AppTestCase):
    def test_empty_exact_date_falls_back_to_other_years(self):
        fetch = FakeFetch(DAY_ROUTES)
        out = self.make_app(fetch).search(None, "2015-10-14")
        self.assertFalse(out["empty"])
        self.assertEqual(out["query"]["fallback"], "month-day")
        self.assertEqual(out["query"]["date"], "2015-10-14")
        self.assertIn("October 14", out["message"])
        self.assertEqual(out["shareUrl"], "/?scope=statewide&date=2015-10-14")
        self.assertTrue(all(r["incidentDate"].endswith("-10-14") for r in out["results"]))

    def test_pre_2004_birthday_skips_the_exact_fetch(self):
        fetch = FakeFetch(DAY_ROUTES)
        out = self.make_app(fetch).search(None, "1990-10-14")
        self.assertFalse(out["empty"])
        self.assertEqual(out["query"]["fallback"], "month-day")
        self.assertFalse(any("1990" in q for q in fetch.queries))

    def test_exact_hit_has_no_fallback(self):
        out = self.make_app(FakeFetch(DAY_ROUTES)).search(None, "2025-10-14")
        self.assertIsNone(out["query"]["fallback"])
        self.assertIsNone(out["message"])

    def test_both_empty_returns_plain_empty(self):
        out = self.make_app(FakeFetch()).search(None, "1990-01-02")
        self.assertTrue(out["empty"])
        self.assertEqual(out["message"], EMPTY_MESSAGE)


class RandomTests(AppTestCase):
    class Rng:
        """Deterministic stand-in: first date offset, then first story."""
        def __init__(self, offsets):
            self.offsets = list(offsets)
        def randrange(self, n):
            return self.offsets.pop(0) % n
        def choice(self, seq):
            return seq[0]

    def test_random_returns_a_story_from_a_past_date(self):
        from fm.service import RANDOM_FROM
        target = date(2025, 10, 14)
        app = self.make_app(FakeFetch(DAY_ROUTES))
        out = app.random_pick(self.Rng([(target - RANDOM_FROM).days]))
        self.assertEqual(out["date"], "2025-10-14")
        self.assertFalse(out["empty"])
        self.assertEqual(out["story"]["id"], out["shareUrl"].split("=", 1)[1])

    def test_random_never_rolls_a_grim_story(self):
        from fm.service import RANDOM_FROM
        target = date(2025, 10, 14)
        seen = []
        class Rng(self.Rng):
            def choice(inner, seq):
                seen.extend(st["headline"] for st in seq)
                return seq[-1]
        app = self.make_app(FakeFetch(DAY_ROUTES))
        out = app.random_pick(Rng([(target - RANDOM_FROM).days]))
        self.assertFalse(out["empty"])
        self.assertTrue(seen)
        self.assertFalse([h for h in seen if "murder" in h])

    def test_random_gives_up_after_a_few_empty_dates(self):
        from fm.service import RANDOM_TRIES
        fetch = FakeFetch()
        out = self.make_app(fetch).random_pick(self.Rng(range(1, 100)))
        self.assertTrue(out["empty"])
        self.assertIsNone(out["story"])
        self.assertEqual(out["message"], EMPTY_MESSAGE)
        self.assertEqual(len(fetch.queries), RANDOM_TRIES)
