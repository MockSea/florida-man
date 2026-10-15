"""Turn untrusted feed text/URLs into plain, bounded, safe values."""
import html
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
_WS = re.compile(r"\s+")
_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-f]+|[a-z]+\d*);", re.IGNORECASE)
_TAG = re.compile(r"<[a-zA-Z/!?]")
_FLORIDA_MAN = re.compile(r"\b(?:florida|fla\.?)\s+man\b", re.IGNORECASE)


class _TextOnly(HTMLParser):
    """Collects text, dropping <script>/<style> content and all markup."""

    _SKIP = {"script", "style", "noscript", "template", "iframe", "object"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in ("br", "p", "li", "div"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def strip_html(s):
    p = _TextOnly()
    p.feed(s)
    p.close()
    return "".join(p.parts)


def clean_text(s, max_len=400):
    """Plain text: no tags, entities decoded (feeds often double-encode),
    no control/bidi characters, collapsed whitespace, bounded length."""
    if not s:
        return ""
    s = strip_html(str(s))
    # A second pass catches double-encoded input like &lt;b&gt; or &amp;#39;.
    if _ENTITY.search(s):
        s = html.unescape(s)
    if _TAG.search(s):
        s = strip_html(s)
    s = _CONTROL.sub("", s.replace(" ", " "))
    s = _WS.sub(" ", s).strip()
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def normalize_florida_man(s):
    """'FLORIDA MAN' / 'florida man' / 'Fla. man' -> 'Florida Man'."""
    return _FLORIDA_MAN.sub("Florida Man", s)


# A period after these is not a sentence end ("Fla.", "St.", "Dr.", initials).
_ABBREV = {
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "ft", "mt", "fla", "gov", "sen", "rep",
    "lt", "sgt", "capt", "det", "dept", "no", "vs", "inc", "co", "corp", "ave", "blvd",
    "rd", "hwy", "jan", "feb", "mar", "apr", "aug", "sept", "sep", "oct", "nov", "dec",
    "u.s", "a.m", "p.m", "approx", "est",
}
_SENTENCE_END = re.compile(r"[.!?][\"'”’)]*(?=\s+[\"'“‘(]?[A-Z0-9]|\s*$)")


def first_sentence(text):
    """The first sentence of plain text, skipping common abbreviations."""
    for m in _SENTENCE_END.finditer(text):
        if text[m.start()] == ".":
            word = re.search(r"([\w.]+)$", text[: m.start()])
            w = word.group(1).lower() if word else ""
            if w in _ABBREV or len(w) == 1 or re.fullmatch(r"(?:\w\.)+\w", w):
                continue
        return text[: m.end()].strip()
    return text.strip()


def trim_words(text, limit):
    """At most `limit` chars, cut on a word boundary with an ellipsis."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-–—") + "…"


def safe_url(url, max_len=2048):
    """Return url if it is a plain absolute http(s) URL, else None."""
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url or len(url) > max_len or _CONTROL.search(url) or re.search(r"\s", url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port  # raises on garbage ports
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    if not parts.hostname or parts.username or parts.password:
        return None
    if port is not None and not (0 < port < 65536):
        return None
    return url
