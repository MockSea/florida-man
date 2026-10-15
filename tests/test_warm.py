from datetime import datetime

from fm.validate import ApiError
from fm.warm import RandomStash, Warmer
from tests.helpers import DAY_ROUTES, ET, AppTestCase, FakeFetch
from tests.test_summaries import EnrichTestCase, FakeFetcher, Mono


class StashTests(EnrichTestCase):
    def setUp(self):
        super().setUp()

    def stash_with_day_stories(self, app, **kw):
        """A stash whose 'random' picks are the fixture day's stories, in order."""
        day = app._day(app.today())
        stories = iter([day["story"]] + day["more"])
        app.random_raw = lambda rng=None: {"date": "2025-10-14", "story": next(stories),
                                           "empty": False, "message": None, "shareUrl": "/"}
        app.stash = RandomStash(app, **kw)
        return app.stash

    def drain(self):
        while self.sm.queued():
            self.mono.t += 60
            self.sm.process_one()

    def test_serves_only_once_summarized(self):
        app = self.make()
        stash = self.stash_with_day_stories(app)
        self.assertTrue(stash.refill_one())
        self.assertIsNone(stash.take())  # summary still pending
        self.drain()
        out = stash.take()
        self.assertEqual(out["story"]["summarySource"], "publisher")
        self.assertFalse(out["story"]["summaryPending"])
        self.assertIsNone(stash.take())  # served once, then gone

    def test_filler_is_dropped_not_served(self):
        app = self.make(fetcher=FakeFetcher(blurb="Breaking news, weather and traffic."))
        stash = self.stash_with_day_stories(app)
        stash.refill_one()
        self.drain()
        self.assertIsNone(stash.take())
        self.assertEqual(len(stash._items), 0)

    def test_decoder_backoff_pauses_refill_and_keeps_picks(self):
        app = self.make()
        stash = self.stash_with_day_stories(app)
        stash.refill_one()
        self.decoder.trouble = True
        self.assertFalse(stash.needs_more())
        self.assertIsNone(stash.take())
        self.assertEqual(len(stash._items), 1)  # not mistaken for settled filler
        self.decoder.trouble = False
        self.drain()
        self.assertEqual(stash.take()["story"]["summarySource"], "publisher")

    def test_refills_capped_per_hour(self):
        app = self.make()
        now = [0.0]
        stash = RandomStash(app, per_hour=2, clock=lambda: now[0])
        stash._sort = lambda: ([], [])  # every roll ends as filler
        for _ in range(2):
            self.assertTrue(stash.needs_more())
            stash._rolls.append(now[0])
        self.assertFalse(stash.needs_more())
        now[0] = 3601.0
        self.assertTrue(stash.needs_more())

    def test_stops_adding_at_size_and_hold(self):
        app = self.make()
        stash = self.stash_with_day_stories(app, size=2, hold=3)
        for _ in range(3):
            self.assertTrue(stash.needs_more())
            stash.refill_one()
        self.assertFalse(stash.needs_more())  # three pending = hold

    def test_random_pick_prefers_ready_stash(self):
        app = self.make()
        stash = self.stash_with_day_stories(app)
        stash.refill_one()
        self.drain()
        first_id = stash._items[0]["story"]["id"]
        self.assertEqual(app.random_pick()["story"]["id"], first_id)
        # Stash empty: falls through to a live pick (here, the next story).
        self.assertNotEqual(app.random_pick()["story"]["id"], first_id)


class WarmerTests(AppTestCase):
    def test_warms_today_then_waits_for_period(self):
        fetch = FakeFetch(DAY_ROUTES)
        app = self.make_app(fetch)
        calls = []
        app.warm_days = lambda: calls.append(1)
        t = Mono()
        stash = RandomStash(app)
        stash.needs_more = lambda: False
        purges = []
        app.store.purge_expired = lambda: purges.append(1)
        w = Warmer(app, stash, every=900, clock=t)
        w.tick_once()
        t.t += 899
        w.tick_once()
        t.t += 2
        w.tick_once()
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(purges), 2)

    def test_errors_do_not_escape(self):
        app = self.make_app(FakeFetch())

        def boom():
            raise ApiError(503, "rate_limited", "busy")
        app.warm_days = boom
        stash = RandomStash(app)
        stash.needs_more = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        Warmer(app, stash, clock=Mono()).tick_once()  # no raise

    def test_tomorrow_warmed_only_in_the_evening(self):
        morning = self.make_app(FakeFetch(DAY_ROUTES))
        morning.warm_days()
        self.assertIsNone(self.stores[0].get("florida-man:2026-10-15")[0])
        evening = self.make_app(FakeFetch(DAY_ROUTES), now=datetime(2026, 10, 14, 22, 0, tzinfo=ET))
        evening.warm_days()
        self.assertIsNotNone(self.stores[1].get("florida-man:2026-10-15")[0])
        self.assertIsNotNone(self.stores[1].get("florida-man:2026-10-14")[0])
