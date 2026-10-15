"""Background enrichment: real one-sentence summaries from publishers.

Stories leave the API immediately with whatever is stored; ids without a
result are queued, and one worker thread decodes the Google link, fetches
the publisher's share blurb and stores the outcome. Results are overlaid on
every payload at read time, so cached day/search payloads never need
invalidating and are never mutated."""
import re
import sys
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit

from . import source
from .gnews_decode import DecodeError, DecoderTrouble
from .publisher import Blocked, FetchError
from .sanitize import clean_text, first_sentence, normalize_florida_man, trim_words

QUEUE_MAX = 200
RETRY_FAILED = 86400
RETRY_REJECTED = 30 * 86400
MIN_BLURB = 40
MAX_SUMMARY = 240
IDLE_WAIT = 5

# Site-wide share text is written in the outlet's own voice ("your source for
# news and weather"); a story blurb almost never is. Matching on story words
# instead rejected good blurbs that paraphrase the headline.
_BOILERPLATE = re.compile(
    r"\b(?:your (?:local |trusted |#1 )?source|source for|breaking news|latest news"
    r"|news,? weather|weather,? (?:traffic|sports)|traffic and sports|subscribe"
    r"|unlimited access|sign up|stay (?:up to date|informed)|all rights reserved"
    r"|top stories|headlines)\b",
    re.IGNORECASE)


def make_summary(blurb, headline):
    """One clean sentence from a publisher blurb, or None if it looks like
    site-wide boilerplate rather than this story."""
    text = normalize_florida_man(clean_text(blurb, max_len=2000))
    if len(text) < MIN_BLURB:
        return None
    sentence = first_sentence(text)
    if len(sentence) < MIN_BLURB:
        sentence = text  # "Wow." is not a summary; keep going up to the cap
    sentence = trim_words(sentence, MAX_SUMMARY)
    if _BOILERPLATE.search(text):
        return None
    return sentence


def article_key(google_url):
    """Summaries are per article, not per (article, town) story id."""
    return source.story_id(google_url)


def _log(fmt, *args):
    sys.stderr.write("summaries: " + (fmt % args) + "\n")


class Summaries:
    def __init__(self, store, decoder, fetcher, per_minute=6, clock=time.time,
                 monotonic=time.monotonic, queue_max=QUEUE_MAX, log=_log):
        self.store = store
        self.decoder = decoder
        self.fetcher = fetcher
        self.enabled = per_minute > 0
        self.limiter = source.RateLimiter(max(per_minute, 1), clock=monotonic)
        self.monotonic = monotonic
        self.clock = clock
        self.queue_max = queue_max
        self.log = log
        self._queue = OrderedDict()  # article key -> (google url, headline)
        self._lock = threading.Lock()
        self._wake = threading.Event()

    # -- queue -------------------------------------------------------------
    def enqueue(self, google_url, headline, priority=False):
        """True if the article is (now) queued. Priority items go to the
        front, evicting the newest normal item when full."""
        if not self.enabled:
            return False
        key = article_key(google_url)
        with self._lock:
            if key in self._queue:
                if priority:
                    self._queue.move_to_end(key, last=False)
                return True
            if len(self._queue) >= self.queue_max:
                if not priority:
                    return False
                self._queue.popitem(last=True)
            self._queue[key] = (google_url, headline)
            if priority:
                self._queue.move_to_end(key, last=False)
        self._wake.set()
        return True

    def queued(self):
        with self._lock:
            return list(self._queue)

    # -- read-time overlay -------------------------------------------------
    def _due(self, row):
        return row is None or (row["retryAt"] is not None and row["retryAt"] <= self.clock())

    def settled(self, st):
        """True once a story has a stored result that isn't due for a retry.
        Unlike summaryPending, this stays False while the decoder backs off."""
        row = self.store.get_summaries([article_key(st["sourceUrl"])]).get(article_key(st["sourceUrl"]))
        return not self._due(row)

    def stalled(self):
        """No point queueing more work: upstream is pushing back or the queue is full."""
        with self._lock:
            full = len(self._queue) >= self.queue_max
        return full or self.decoder.backing_off()

    def present(self, payload, priority=False):
        """A copy of an API payload with stored summaries overlaid on its
        story/results, and summaryPending set on each story."""
        stories = payload_stories(payload)
        rows = self.store.get_summaries(article_key(s["sourceUrl"]) for s in stories)
        trouble = self.decoder.backing_off()

        main = payload.get("story")

        def one(st):
            row = rows.get(article_key(st["sourceUrl"]))
            pending = False
            if self._due(row):
                # Only the card on screen jumps the queue, not its runners-up.
                queued = self.enqueue(st["sourceUrl"], st["headline"], priority and st is main)
                pending = queued and not trouble
            return overlay(st, row, pending)

        return _rebuild(payload, one)

    # -- worker ------------------------------------------------------------
    def process_one(self):
        """Enrich the next queued article. False if there was nothing to do
        or no budget (the item stays queued)."""
        if not self.enabled or self.decoder.backing_off():
            return False
        while True:
            with self._lock:
                if not self._queue:
                    return False
                key, (url, headline) = next(iter(self._queue.items()))
            if self._due(self.store.get_summaries([key]).get(key)):
                break
            with self._lock:
                self._queue.pop(key, None)  # settled meanwhile; costs nothing
        try:
            self.limiter.acquire(1)
        except source.RateLimited:
            return False
        with self._lock:
            self._queue.pop(key, None)
        try:
            self._enrich(key, url, headline)
        except Exception as e:  # never let one story kill the worker
            self.log("- error %s", type(e).__name__)
            self.store.put_summary(key, "failed", retry_in=RETRY_FAILED)
        return True

    def _enrich(self, key, url, headline):
        try:
            pub = self.decoder.decode(url)
        except DecoderTrouble as e:
            self.log("news.google.com trouble, backing off: %s", str(e)[:120])
            self.store.put_summary(key, "failed", retry_in=self.decoder.backoff)
            return
        except DecodeError:
            self.store.put_summary(key, "failed", retry_in=RETRY_FAILED)
            return
        host = urlsplit(pub).hostname
        if urlsplit(pub).scheme.lower() != "https":
            # Still a usable link, but we only fetch https.
            self.log("%s failed NotHttps", host)
            self.store.put_summary(key, "failed", publisher_url=pub, retry_in=RETRY_REJECTED)
            return
        try:
            blurb, image = self.fetcher.page(pub)
        except Blocked as e:
            # Refused by policy: don't link it either.
            self.log("%s blocked (%s)", host, e)
            self.store.put_summary(key, "failed", retry_in=RETRY_REJECTED)
            return
        except FetchError as e:
            self.log("%s failed (%s)", host, e)
            self.store.put_summary(key, "failed", publisher_url=pub, retry_in=RETRY_FAILED)
            return
        summary = make_summary(blurb, headline)
        if summary is None:
            self.log("%s rejected", host)
            self.store.put_summary(key, "rejected", publisher_url=pub, retry_in=RETRY_REJECTED,
                                   image=image)
        else:
            self.log("%s ok", host)
            self.store.put_summary(key, "ok", publisher_url=pub, summary=summary, image=image)

    def drain(self, limit, deadline=None, sleep=time.sleep):
        """Enrich queued articles in the calling thread until the queue is
        empty, `limit` are done, the monotonic `deadline` passes or the
        decoder backs off. Waits out the per-minute budget instead of giving
        up. For the batch build; returns how many were processed."""
        done = 0
        while self.enabled and done < limit and not self.decoder.backing_off():
            if deadline is not None and self.monotonic() >= deadline:
                break
            with self._lock:
                if not self._queue:
                    break
            if self.process_one():
                done += 1
            elif self.queued():
                sleep(60.0 / self.limiter.per_minute)
        return done

    def run(self, stop=None):
        while not (stop and stop.is_set()):
            self._wake.clear()
            if not self.process_one():
                self._wake.wait(IDLE_WAIT)

    def start(self):
        if not self.enabled:
            return None
        t = threading.Thread(target=self.run, name="fm-summaries", daemon=True)
        t.start()
        return t


def overlay(st, row, pending):
    out = dict(st, summaryPending=bool(pending))
    if row:
        if row["status"] == "ok" and row["summary"]:
            out["summary"] = row["summary"]
            out["summarySource"] = "publisher"
        if row["publisherUrl"]:
            out["sourceUrl"] = row["publisherUrl"]
        if row.get("image"):
            out["imageUrl"] = row["image"]
    return out


# Payload keys that hold a list of stories (search results, the day's runners-up).
LIST_KEYS = ("results", "more")


def payload_stories(payload):
    """Every story in an API payload: the main one, then any lists."""
    out = [payload["story"]] if payload.get("story") else []
    for k in LIST_KEYS:
        out.extend(payload.get(k) or [])
    return out


def _rebuild(payload, fn):
    out = dict(payload)
    if payload.get("story"):
        out["story"] = fn(payload["story"])
    for k in LIST_KEYS:
        if k in payload:
            out[k] = [fn(s) for s in payload[k] or []]
    return out


def settled(payload):
    """For an app without enrichment: just mark every story not pending."""
    return _rebuild(payload, lambda st: overlay(st, None, False))
