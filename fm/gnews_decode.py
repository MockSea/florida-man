"""Decode a Google News RSS article link to the publisher's URL.

Two requests, both to news.google.com through source.http_get (https only,
no redirects, size cap): the article page, which carries a signature and
timestamp, then a batchexecute POST that trades them for the real URL. Any
redirect (Google's /sorry page), non-200, or unparseable answer is treated as
upstream trouble and the whole decoder backs off."""
import json
import re
import threading
import time
import urllib.parse

from . import source
from .sanitize import safe_url

PAGE = "https://news.google.com/rss/articles/%s?hl=en-US&gl=US&ceid=US%%3Aen"
BATCH = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
# Google serves a /sorry redirect to the plain article path for non-browsers.
CHROME_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# Copied verbatim from an open-source decoder; Google checks its shape, not its values.
CTX = [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
       "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0]
BACKOFF = 15 * 60
MAX_URL = 2048

_ARTICLE_PATH = re.compile(r"^/(?:rss/)?articles/([A-Za-z0-9_-]{8,1024})$")
_SG = re.compile(r'data-n-a-sg="([A-Za-z0-9_-]{1,512})"')
_TS = re.compile(r'data-n-a-ts="([0-9]{1,12})"')


class DecodeError(Exception):
    """This link can't be decoded (not a Google News article link)."""


class DecoderTrouble(Exception):
    """Google refused or answered oddly; the decoder is now backing off."""


def article_id(url):
    """The <id> from https://news.google.com/rss/articles/<id>, else None."""
    try:
        parts = urllib.parse.urlsplit(url or "")
    except ValueError:
        return None
    if parts.scheme != "https" or parts.hostname != source.HOST or parts.port not in (None, 443):
        return None
    m = _ARTICLE_PATH.match(parts.path)
    return m.group(1) if m else None


def parse_page(html):
    """(signature, timestamp) from the article page."""
    sg, ts = _SG.search(html), _TS.search(html)
    if not sg or not ts:
        raise ValueError("article page lacks data-n-a-sg/ts")
    return sg.group(1), int(ts.group(1))


def build_body(art_id, ts, sg):
    inner = json.dumps(["garturlreq", CTX, art_id, ts, sg], separators=(",", ":"))
    f_req = json.dumps([[["Fbv4je", inner, None, "0"]]], separators=(",", ":"))
    return ("f.req=" + urllib.parse.quote(f_req)).encode("ascii")


def parse_batch(text):
    """The publisher URL from an XSSI-prefixed batchexecute response."""
    for line in text.splitlines():
        if not line.startswith("[["):
            continue
        for row in json.loads(line):
            if (isinstance(row, list) and len(row) > 2 and row[0] == "wrb.fr"
                    and row[1] == "Fbv4je" and isinstance(row[2], str)):
                payload = json.loads(row[2])
                if (isinstance(payload, list) and len(payload) > 1
                        and payload[0] == "garturlres" and isinstance(payload[1], str)):
                    return payload[1]
    raise ValueError("no garturlres in batchexecute response")


def valid_publisher_url(url):
    """The decoded URL is attacker-influenced: plain http(s), no userinfo,
    bounded. http is accepted here (it is still a usable link) but the
    publisher fetcher refuses to fetch it."""
    url = safe_url(url, max_len=MAX_URL)
    if not url:
        return None
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or "@" in parts.netloc:
        return None
    return url


class Decoder:
    def __init__(self, fetch=source.http_get, clock=time.monotonic, backoff=BACKOFF):
        self.fetch = fetch
        self.clock = clock
        self.backoff = backoff
        self._until = 0.0
        self._lock = threading.Lock()

    def backing_off(self):
        with self._lock:
            return self.clock() < self._until

    def _trouble(self, why):
        with self._lock:
            self._until = self.clock() + self.backoff
        raise DecoderTrouble(why)

    def decode(self, gn_url):
        """Publisher URL for a Google News link. Raises DecodeError for links
        that aren't Google News articles, DecoderTrouble for everything else
        (and starts the backoff)."""
        art = article_id(gn_url)
        if not art:
            raise DecodeError("not a Google News article link")
        if self.backing_off():
            raise DecoderTrouble("backing off")
        headers = {"User-Agent": CHROME_UA}
        try:
            html = self.fetch(PAGE % art, headers=headers).decode("utf-8", "replace")
            sg, ts = parse_page(html)
            body = build_body(art, ts, sg)
            raw = self.fetch(BATCH, data=body, headers=dict(
                headers, **{"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}))
            url = parse_batch(raw.decode("utf-8", "replace"))
        except source.UpstreamError as e:
            self._trouble(str(e))
        except (ValueError, TypeError, IndexError, KeyError) as e:
            self._trouble("unparseable answer: %s" % type(e).__name__)
        good = valid_publisher_url(url)
        if not good:
            # Google answered fine; this one URL is just unusable.
            raise DecodeError("decoded URL refused")
        return good
