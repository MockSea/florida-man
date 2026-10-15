"""Background warming, so summaries are already there when someone looks.

The summary worker only learns about a story when a payload leaves the API, so
the first visitor always saw the generated filler. The Warmer asks for today's
pick (plus its runners-up) on a timer, and keeps a small stash of random picks
whose summaries have already been fetched for the Random button."""
import sys
import threading
import time
from collections import deque

from .validate import ApiError
from . import source

STASH_SIZE = 5
STASH_HOLD = 10  # stories waiting on a summary, at most, before we stop adding
REFILLS_PER_HOUR = 30  # if most rolls end as filler, this is what bounds the searches
WARM_DAYS_EVERY = 900
TICK = 20


def _log(fmt, *args):
    sys.stderr.write("warm: " + (fmt % args) + "\n")


class RandomStash:
    """Random payloads, served only once their story has a real summary.
    One that ends up with the generated filler is dropped, not served."""

    def __init__(self, app, size=STASH_SIZE, hold=STASH_HOLD,
                 per_hour=REFILLS_PER_HOUR, clock=time.monotonic):
        self.app = app
        self.size = size
        self.hold = hold
        self.per_hour = per_hour
        self.clock = clock
        self._rolls = deque()
        self._items = deque()
        self._lock = threading.Lock()

    def _sort(self):
        """Drop the ones that settled on filler; return (ready, pending) as
        (raw, shown) pairs. Caller holds the lock."""
        ready, pending = [], []
        for raw in list(self._items):
            shown = self.app._present(raw)
            st = shown["story"]
            if not self.app.summaries.settled(raw["story"]):
                pending.append((raw, shown))
            elif st["summarySource"] == "generated":
                self._items.remove(raw)
            else:
                ready.append((raw, shown))
        return ready, pending

    def take(self):
        with self._lock:
            ready, _ = self._sort()
            if not ready:
                return None
            raw, shown = ready[0]
            self._items.remove(raw)
            return shown

    def needs_more(self):
        if self.app.summaries.stalled():  # rolls added now would only be dropped
            return False
        with self._lock:
            while self._rolls and self.clock() - self._rolls[0] > 3600:
                self._rolls.popleft()
            if len(self._rolls) >= self.per_hour:
                return False
            ready, pending = self._sort()
        return len(ready) < self.size and len(ready) + len(pending) < self.hold

    def refill_one(self):
        with self._lock:
            self._rolls.append(self.clock())
        raw = self.app.random_raw()
        if raw is None:
            return False
        with self._lock:
            if any(r["story"]["id"] == raw["story"]["id"] for r in self._items):
                return False
            self._items.append(raw)
        self.app._present(raw)  # queues its summary
        return True


class Warmer:
    def __init__(self, app, stash, every=WARM_DAYS_EVERY, tick=TICK, clock=time.monotonic):
        self.app = app
        self.stash = stash
        self.every = every
        self.tick = tick
        self.clock = clock
        self._next_days = 0.0

    def tick_once(self):
        """One round: today's pick when due, then at most one stash refill.
        Upstream trouble or a busy rate limit just waits for the next tick."""
        try:
            if self.clock() >= self._next_days:
                self._next_days = self.clock() + self.every
                self.app.store.purge_expired()  # search rows otherwise pile up until restart
                self.app.warm_days()
            if self.stash.needs_more():
                self.stash.refill_one()
        except (ApiError, source.UpstreamError) as e:
            _log("%s", type(e).__name__)
        except Exception as e:  # never let the warmer thread die
            _log("unexpected %s", type(e).__name__)

    def run(self, stop=None):
        while not (stop and stop.is_set()):
            self.tick_once()
            (stop or threading.Event()).wait(self.tick)

    def start(self):
        t = threading.Thread(target=self.run, name="fm-warm", daemon=True)
        t.start()
        return t
