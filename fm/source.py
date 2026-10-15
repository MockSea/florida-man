"""Google News RSS: query building, a locked-down fetcher, and parsing."""
import hashlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from datetime import timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from .sanitize import clean_text, safe_url

HOST = "news.google.com"
BASE = "https://news.google.com/rss/search"
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 10
USER_AGENT = "florida-man-of-the-day/1.0 (+https://github.com/MockSea/florida-man)"


class UpstreamError(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, retry_after):
        super().__init__("upstream fetch budget exhausted")
        self.retry_after = retry_after


def build_query(day, town=None):
    """`"Florida man" ["Town"] after:D-1 before:D+1` for a datetime.date.

    Google treats `before:` as exclusive, so the window is padded a day each
    side and results are filtered to the exact date afterwards."""
    terms = ['"Florida man"']
    if town:
        terms.append('"%s"' % town.replace('"', ""))
    terms.append("after:%s" % (day - timedelta(days=1)).isoformat())
    terms.append("before:%s" % (day + timedelta(days=1)).isoformat())
    return " ".join(terms)


def build_url(query):
    return BASE + "?" + urllib.parse.urlencode(
        {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UpstreamError("upstream redirected (%s); not following" % code)


_opener = urllib.request.build_opener(_NoRedirect)


def http_get(url, timeout=TIMEOUT, max_bytes=MAX_BYTES, data=None, headers=None):
    """GET (or POST, with data) a news.google.com https URL. No redirects,
    timeout, size cap. `headers` may override the User-Agent."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname != HOST or parts.port not in (None, 443):
        raise UpstreamError("refusing to fetch non-Google-News URL")
    hdrs = {"User-Agent": USER_AGENT}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise UpstreamError("upstream status %s" % resp.status)
            body = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        raise UpstreamError("upstream status %s" % e.code)
    except (urllib.error.URLError, OSError) as e:
        raise UpstreamError("upstream unreachable: %s" % getattr(e, "reason", e))
    if len(body) > max_bytes:
        raise UpstreamError("upstream response too large")
    return body


class RateLimiter:
    """At most `per_minute` upstream fetches in any rolling 60s window."""

    def __init__(self, per_minute, clock=time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self.stamps = deque()
        self.lock = threading.Lock()

    def acquire(self, n=1):
        """Take n tokens all-or-nothing; raise RateLimited otherwise."""
        with self.lock:
            now = self.clock()
            while self.stamps and now - self.stamps[0] >= 60:
                self.stamps.popleft()
            if len(self.stamps) + n > self.per_minute:
                oldest = self.stamps[0] if self.stamps else now
                raise RateLimited(max(1, int(60 - (now - oldest)) + 1))
            self.stamps.extend([now] * n)


class _DescriptionText(HTMLParser):
    """Google's <description> is `<a>title</a> <font>source</font>` (or an
    <ol> of those for clusters). Keep only text outside <a>/<font>, which
    is the only part that could be a real summary."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("a", "font", "script", "style"):
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in ("a", "font", "script", "style") and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.parts.append(data)


def _extra_description(desc):
    p = _DescriptionText()
    p.feed(desc or "")
    p.close()
    return clean_text(" ".join(p.parts), max_len=300)


def story_id(url, town=None):
    """Stable id per (article, town context): a search result tagged with a
    town must not overwrite the same article stored without one."""
    key = url if not town else url + "|" + town
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


XML_DECL = re.compile(r"\A\s*<\?xml[^>]*\?>")


def parse_rss(body):
    """Parse feed bytes into a list of raw item dicts (sanitized text,
    validated URLs). Items without a safe http(s) link are dropped."""
    if isinstance(body, bytes):
        # Decode ourselves so the DTD check below sees the same text the
        # parser does: a UTF-16 or codec-declared body would hide "<!DOCTYPE"
        # from a byte scan, and the 3.9 system expat has no expansion limit.
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            raise UpstreamError("feed is not UTF-8")
    body = body.lstrip("\ufeff")
    if "\x00" in body:
        raise UpstreamError("feed contains NUL bytes")
    low = body.lower()
    # RSS never needs a DTD; refusing one rules out entity-expansion tricks.
    if "<!doctype" in low or "<!entity" in low:
        raise UpstreamError("refusing feed with DTD/entities")
    # Drop the XML declaration so an encoding="..." in it can't make the
    # parser reinterpret text we already checked.
    body = XML_DECL.sub("", body, count=1)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise UpstreamError("malformed feed: %s" % e)
    items = []
    for it in root.iter("item"):
        link = safe_url((it.findtext("link") or "").strip())
        if not link:
            continue
        src = it.find("source")
        source_name = clean_text(src.text if src is not None else "", max_len=80)
        source_site = safe_url(src.get("url")) if src is not None else None
        title = clean_text(it.findtext("title") or "", max_len=300)
        # Google appends " - Source Name" to every title.
        if source_name and title.lower().endswith(" - " + source_name.lower()):
            title = title[: -(len(source_name) + 3)].rstrip()
        elif not source_name and " - " in title:
            title, _, source_name = title.rpartition(" - ")
        if not title:
            continue
        published = None
        raw_date = it.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date.strip())
                if published.tzinfo is None:
                    published = None
            except (TypeError, ValueError, IndexError):
                published = None
        extra = _extra_description(it.findtext("description") or "")
        # Only keep description text that reads like an actual summary.
        if len(extra) < 40 or title.lower().startswith(extra.lower()):
            extra = ""
        items.append(
            {
                "title": title,
                "link": link,
                "sourceName": source_name or "Google News",
                "sourceSite": source_site,
                "published": published,
                "extra": extra,
            }
        )
    return items
