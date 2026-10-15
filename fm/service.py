"""Business logic: daily pick, search, story permalinks, share tracking."""
import random
import threading
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import score as scoring
from . import source, summaries as enrich, towns, validate
from .sanitize import first_sentence, normalize_florida_man
from .validate import ApiError

EMPTY_MESSAGE = "Florida Man was, surprisingly, well-behaved today."
RANDOM_FROM = date(2013, 1, 1)  # roughly when the Florida Man meme took off
RANDOM_TRIES = 3
WARM_TOMORROW_FROM = 21  # local hour after which tomorrow's pick is warmed too
MORE_MAX = 9  # runners-up on a day payload, for the carousel
FALLBACK_MESSAGE = "Nothing on that exact day. Here's Florida Man on %s in other years."
LOCK_POOL = 64
MAX_RESULTS = 20
EMPTY_DAY_TTL = 3600  # an empty day may be an upstream hiccup; retry hourly
PARTIAL_SEARCH_TTL = 300


def _needs_more(payload):
    return bool(payload.get("story")) and "more" not in payload


def _with_more(payload, more):
    out = dict(payload)
    out["more"] = more
    return out


def _always_more(payload):
    """Old cached empty days predate `more`; the contract says it's always there."""
    return payload if "more" in payload else _with_more(payload, [])


def _fmt_day(d):
    return "%s %d, %d" % (d.strftime("%B"), d.day, d.year)



def random_grim(st):
    """Grim stories can sit in a day's list but never come up on the dice."""
    return scoring.is_grim("%s %s" % (st.get("headline") or "", st.get("summary") or ""))

class FloridaMan:
    def __init__(self, store, tz="America/New_York", fetch=source.http_get,
                 limiter=None, home_years=4, search_years=3, search_ttl=3600,
                 now=None, summaries=None):
        self.store = store
        self.summaries = summaries  # an enrich.Summaries, or None for none
        self.stash = None  # a warm.RandomStash, set by __main__
        self.tz = ZoneInfo(tz)
        self.fetch = fetch
        self.limiter = limiter or source.RateLimiter(10)
        self.home_years = home_years
        self.search_years = search_years
        self.search_ttl = search_ttl
        self._now = now or (lambda: datetime.now(self.tz))
        # Fixed pool: a dict keyed by cache key grew forever under a
        # tailnet client walking every town/date pair.
        self._locks = tuple(threading.Lock() for _ in range(LOCK_POOL))

    # -- helpers -----------------------------------------------------------
    def today(self):
        return self._now().astimezone(self.tz).date()

    def _lock_for(self, key):
        return self._locks[hash(key) % LOCK_POOL]

    def _fetch_day(self, day, town=None):
        url = source.build_url(source.build_query(day, town))
        return source.parse_rss(self.fetch(url))

    def _to_story(self, item, town=None):
        headline = normalize_florida_man(item["title"])
        hl = headline.lower()
        if "florida man" not in hl and not (town and ("%s man" % town.lower()) in hl):
            return None
        if item["published"] is None:
            return None
        day = item["published"].astimezone(self.tz).date()
        extra = normalize_florida_man(item["extra"]) if item["extra"] else ""
        if extra:
            summary, summary_source = first_sentence(extra), "feed"
        else:
            summary = "%s reported this one on %s. Full story at the source." % (
                item["sourceName"], _fmt_day(day))
            summary_source = "generated"
        pts, reasons = scoring.score(headline + " " + extra)
        sid = source.story_id(item["link"], town)
        return {
            "id": sid,
            "headline": headline,
            "summary": summary,
            "summarySource": summary_source,
            "imageUrl": None,
            "town": town or towns.find_in_text(headline + " " + extra),
            "incidentDate": day.isoformat(),
            "sourceName": item["sourceName"],
            "sourceSite": item["sourceSite"],
            "sourceUrl": item["link"],
            "score": pts,
            "scoreMax": 100,
            "scoreLabel": "subjective",
            "scoreReasons": reasons,
            "shareUrl": "/?" + urlencode({"story": sid}),
        }

    def _collect(self, days, town=None):
        """Fetch each day, return (stories, failures). Stories are matched to
        the exact requested day when possible, else within +/-1 day."""
        exact, near, seen, failures = [], [], set(), 0
        wanted = set(days)
        for d in days:
            try:
                items = self._fetch_day(d, town)
            except source.UpstreamError:
                failures += 1
                continue
            for it in items:
                st = self._to_story(it, town)
                if not st:
                    continue
                dedupe = st["headline"].lower()
                if dedupe in seen:
                    continue
                inc = date.fromisoformat(st["incidentDate"])
                if inc in wanted:
                    exact.append(st)
                elif any(abs((inc - w).days) <= 1 for w in wanted):
                    near.append(st)
                else:
                    continue
                seen.add(dedupe)
        stories = exact or near
        stories.sort(key=lambda s: (-s["score"], s["id"]))
        return stories, failures

    def _present(self, payload, priority=False):
        """Every payload leaves through here: overlay stored publisher
        summaries (as a copy) and queue the stories that lack one."""
        if self.summaries is None:
            return enrich.settled(payload)
        return self.summaries.present(payload, priority)

    def _acquire(self, n):
        try:
            self.limiter.acquire(n)
        except source.RateLimited as e:
            raise ApiError(503, "rate_limited",
                           "Too many fresh lookups right now. Try again shortly.",
                           retryAfter=e.retry_after)

    # -- daily pick --------------------------------------------------------
    def day(self, d):
        return self._present(_always_more(self._day(d)))

    def day_raw(self, d):
        """The stored day payload, picking it first if needed, without the
        summary overlay. The batch build archives this."""
        return _always_more(self._day(d))

    def _day(self, d):
        key = "florida-man:%s" % d.isoformat()
        cached, fresh = self.store.get(key)
        if cached is not None and fresh and not _needs_more(cached):
            return cached
        with self._lock_for(key):
            cached, fresh = self.store.get(key)
            if cached is not None and fresh:
                if not _needs_more(cached):
                    return cached
                return self._backfill_more(key, d, cached)
            days = self._home_days(d)
            try:
                self._acquire(len(days))
            except ApiError:
                if cached is not None:
                    return _with_more(cached, cached.get("more") or [])
                raise
            stories, failures = self._collect(days)
            pick = stories[0] if stories else None
            more = stories[1:1 + MORE_MAX]
            payload = {
                "date": d.isoformat(),
                "cacheKey": key,
                "story": pick,
                "more": more,
                "empty": pick is None,
                "message": None if pick else EMPTY_MESSAGE,
                "yearsSearched": [x.year for x in days],
                "shareUrl": "/?" + urlencode({"date": d.isoformat()}),
            }
            if pick:
                self.store.put(key, payload)  # permanent: same date, same pick
                self.store.put_stories([pick] + more)
            elif failures == 0:
                self.store.put(key, payload, ttl=EMPTY_DAY_TTL)
            elif cached is not None:
                return _with_more(cached, cached.get("more") or [])
            else:
                raise ApiError(502, "upstream_error",
                               "Couldn't reach the news source. Try again in a bit.")
            return payload

    def _home_days(self, d):
        days = []
        for y in range(d.year - 1, d.year - 1 - self.home_years, -1):
            try:
                days.append(date(y, d.month, d.day))
            except ValueError:  # Feb 29 in a non-leap year
                pass
        return days

    def _backfill_more(self, key, d, cached):
        """Days picked before the carousel existed have no runners-up. Fetch
        them once, keep the original pick, and store the result. Any trouble
        returns an empty list without storing, so a later visit tries again."""
        days = self._home_days(d)
        try:
            self._acquire(len(days))
        except ApiError:
            return _with_more(cached, [])
        stories, failures = self._collect(days)
        if failures:
            return _with_more(cached, [])
        pick_id = cached["story"]["id"]
        more = [s for s in stories if s["id"] != pick_id][:MORE_MAX]
        payload = _with_more(cached, more)
        self.store.put(key, payload)
        self.store.put_stories(more)
        return payload

    def today_pick(self):
        return self._present(_always_more(self._day(self.today())), priority=True)

    # -- search ------------------------------------------------------------
    def search(self, town_param, date_param):
        return self._present(self._search_any(town_param, date_param))

    def _search_any(self, town_param, date_param):
        today = self.today()
        town = validate.town(town_param) if str(town_param or "").strip() else None
        mode, val = validate.search_date(date_param, today, earliest=validate.BIRTHDAY_MIN)
        if mode == "month-day":
            return self._search(town, "%02d-%02d" % val, self._recent_days(val, today), mode)
        label = val.isoformat()
        # Before MIN_DATE the news index has nothing, so don't spend a fetch on it.
        exact = self._search(town, label, [val] if val >= validate.MIN_DATE else [], mode)
        if not exact["empty"] or exact["stale"]:
            return exact
        fb = self._search(town, "%02d-%02d" % (val.month, val.day),
                          self._recent_days((val.month, val.day), today), "month-day")
        if fb["empty"]:
            return exact
        return dict(fb, query=dict(fb["query"], date=label, mode="full", fallback="month-day"),
                    message=FALLBACK_MESSAGE % val.strftime("%B %-d"),
                    shareUrl=exact["shareUrl"])

    def _recent_days(self, month_day, today):
        mo, dy = month_day
        days, y = [], today.year
        while len(days) < self.search_years and y >= validate.MIN_DATE.year:
            try:
                cand = date(y, mo, dy)
                if cand <= today:
                    days.append(cand)
            except ValueError:
                pass
            y -= 1
        return days

    def _search(self, town, date_label, days, mode):
        key = "search:%s:%s" % (towns.key(town) if town else "*", date_label)
        cached, fresh = self.store.get(key)
        if cached is not None and fresh:
            return cached
        with self._lock_for(key):
            cached, fresh = self.store.get(key)
            if cached is not None and fresh:
                return cached
            stories, failures = [], 0
            if days:
                try:
                    self._acquire(len(days))
                except ApiError:
                    if cached is not None:
                        return dict(cached, stale=True)
                    raise
                stories, failures = self._collect(days, town)
                if failures == len(days):
                    if cached is not None:
                        return dict(cached, stale=True)
                    raise ApiError(502, "upstream_error",
                                   "Couldn't reach the news source. Try again in a bit.")
            results = stories[:MAX_RESULTS]
            payload = {
                "query": {"town": town, "date": date_label, "mode": mode,
                          "datesSearched": [x.isoformat() for x in days], "fallback": None},
                "results": results,
                "count": len(results),
                "empty": not results,
                "message": None if results else EMPTY_MESSAGE,
                "stale": False,
                "partial": failures > 0,
                "shareUrl": "/?" + urlencode({"town": town, "date": date_label} if town
                                              else {"scope": "statewide", "date": date_label}),
            }
            self.store.put(key, payload, ttl=PARTIAL_SEARCH_TTL if failures else self.search_ttl)
            self.store.put_stories(results)
            return payload

    # -- random ------------------------------------------------------------
    def random_pick(self, rng=random):
        """A random statewide story from a random past date. Served from the
        warm stash when one is ready (summary already fetched), else live."""
        if self.stash is not None:
            ready = self.stash.take()
            if ready is not None:
                return ready
        raw = self.random_raw(rng)
        if raw is None:
            return {"date": None, "story": None, "empty": True, "message": EMPTY_MESSAGE,
                    "shareUrl": None}
        return self._present(raw, priority=True)

    def random_raw(self, rng=random):
        """An un-overlaid random payload, or None. Tries a few dates, each one
        an ordinary cached full-date search, so it costs what a search costs
        and repeats are cheap."""
        today = self.today()
        span = (today - RANDOM_FROM).days
        for _ in range(RANDOM_TRIES):
            d = RANDOM_FROM + timedelta(days=rng.randrange(span))
            res = self._search(None, d.isoformat(), [d], "full")  # exact day, no fallback
            pool = [st for st in res["results"] if not random_grim(st)]
            if pool:
                st = rng.choice(pool)
                return {"date": d.isoformat(), "story": st, "empty": False, "message": None,
                        "shareUrl": st.get("shareUrl") or "/?" + urlencode({"story": st["id"]})}
        return None

    def warm_days(self, evening_hour=WARM_TOMORROW_FROM):
        """Pick (and queue summaries for) today, and tomorrow once it's late,
        so the first visitor of the day doesn't wait on either."""
        self.day(self.today())
        if self._now().astimezone(self.tz).hour >= evening_hour:
            self._present(self._day(self.today() + timedelta(days=1)))

    # -- permalinks & tracking ---------------------------------------------
    def story(self, sid):
        st = self.store.get_story(validate.story_id(sid))
        if st is None:
            raise ApiError(404, "story_not_found",
                           "That story isn't in our cache anymore. Try a search.", field="id")
        return self._present({"story": st}, priority=True)

    def track(self, payload):
        event, _sid = validate.track_event(payload)
        self.store.incr("%s:total" % event)
        self.store.incr("%s:%s" % (event, self.today().isoformat()))
