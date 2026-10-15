"""Fetch a publisher page's own share blurb (og/twitter/meta description).

The URL is attacker-chosen (it comes from Google, i.e. from whatever got
indexed), and this server sits on a tailnet next to other services, so every
hop is checked: https on 443, a real hostname, every resolved address
globally routable, and the connection is pinned to the address we checked
(no second DNS lookup, so no rebinding). Only cleaned text leaves here."""
import codecs
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
import urllib.parse
from html.parser import HTMLParser

from .sanitize import clean_text, safe_url
from .source import USER_AGENT

TIMEOUT = 8          # per socket operation
DEADLINE = 12        # whole fetch, redirects included
MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 3
HTML_TYPES = ("text/html", "application/xhtml+xml")
META_KEYS = ("og:description", "twitter:description", "description")  # precedence
IMAGE_KEYS = ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src")  # precedence
MAX_IMAGE_URL = 2048

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(r"^(?=.{1,253}$)%s(?:\.%s)+$" % (_LABEL, _LABEL))
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".ts.net")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


class Blocked(Exception):
    """Refused by policy: never fetched."""


class FetchError(Exception):
    """Fetched (or tried to) and it didn't work out."""


def check_url(url):
    """(host, path) for a URL we are willing to fetch, else Blocked."""
    url = safe_url(url)
    if not url:
        raise Blocked("not a plain URL")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() != "https":
        raise Blocked("not https")
    if "@" in parts.netloc:
        raise Blocked("userinfo")
    if parts.port not in (None, 443):
        raise Blocked("port")
    host = (parts.hostname or "").lower()
    try:
        ipaddress.ip_address(host)
        raise Blocked("IP literal")
    except ValueError:
        pass
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise Blocked("bad hostname")
    if not _HOSTNAME.match(host) or not re.search(r"[a-z]", host.rsplit(".", 1)[1]):
        raise Blocked("bad hostname")
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        raise Blocked("local name")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    # Percent-encode anything non-ASCII; leave existing escapes alone.
    return host, urllib.parse.quote(path, safe="/?&=%:@!$'()*+,;~-._")


def address_ok(addr):
    """True only for globally routable unicast addresses. ipaddress.is_global
    alone is not enough on 3.9 (multicast, NAT64, 6to4, ::a.b.c.d pass)."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False  # includes scoped v6 like fe80::1%en0 on 3.9
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return address_ok(str(ip.ipv4_mapped))
        if int(ip) >> 32 == 0:  # ::/96, IPv4-compatible and unspecified
            return False
        if any(ip in net for net in _NAT64):
            return address_ok(str(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)))
        if ip.sixtofour is not None:
            return address_ok(str(ip.sixtofour))
        if ip.teredo is not None:
            return all(address_ok(str(v4)) for v4 in ip.teredo)
    elif ip in _CGNAT:
        return False  # tailnet addresses live here
    return bool(ip.is_global and not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified))


def system_resolve(host):
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def pick_address(host, resolve):
    """Resolve once; refuse if any address is non-global; return one."""
    try:
        addrs = list(resolve(host))
    except (OSError, UnicodeError) as e:
        raise FetchError("dns: %s" % type(e).__name__)
    if not addrs:
        raise FetchError("dns: no addresses")
    bad = [a for a in addrs if not address_ok(a)]
    if bad:
        raise Blocked("resolves to a non-public address")
    return addrs[0]


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to a pre-validated IP while SNI and certificate verification
    use the hostname. Never looks the hostname up itself."""

    def __init__(self, host, ip, timeout=TIMEOUT, context=None):
        self._fm_context = context or ssl.create_default_context()
        super().__init__(host, 443, timeout=timeout, context=self._fm_context)
        self._fm_ip = ip

    def set_tunnel(self, *a, **k):
        raise Blocked("no proxies")

    def connect(self):
        sock = socket.create_connection((self._fm_ip, 443), self.timeout)
        self._fm_raw = sock
        try:
            self.sock = self._fm_context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise

    def abort(self):
        """Called from the deadline timer thread: shutting the raw socket down
        unblocks whatever recv the worker is stuck in (handshake, headers or
        body), which the per-recv socket timeout alone never does."""
        _shutdown(getattr(self, "_fm_raw", None))


def _shutdown(sock):
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = {}
        self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "body":
            self.done = True
            return
        if tag != "meta":
            return
        a = {k.lower(): (v or "") for k, v in attrs if k}
        key = (a.get("property") or a.get("name") or "").strip().lower()
        if key in META_KEYS + IMAGE_KEYS and key not in self.found and a.get("content", "").strip():
            self.found[key] = a["content"]

    def handle_endtag(self, tag):
        if tag == "head":
            self.done = True


def _meta(html):
    p = _MetaParser()
    try:
        p.feed(html)
        p.close()
    except Exception:  # HTMLParser is lenient, but stay defensive
        pass
    return p.found


def _blurb(found):
    for key in META_KEYS:
        text = clean_text(found.get(key, ""), max_len=2000)
        if text:
            return text
    return ""


def image_url(value, base):
    """An absolute https image URL from a meta tag, or None. Relative paths
    resolve against the page; anything else (http, data:, javascript:) is
    dropped. The browser loads it, so it only has to be a well-formed https
    URL on a public-looking hostname."""
    value = (value or "").strip()
    if not value or len(value) > MAX_IMAGE_URL or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    try:
        url = urllib.parse.urljoin(base, value)
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or not _HOSTNAME.match(host):
        return None
    if re.fullmatch(r"0x[0-9a-f]*|[0-9]+", host.rsplit(".", 1)[1]):  # IPv4 literals, hex too
        return None
    if host.endswith(_BLOCKED_SUFFIXES) or len(url) > MAX_IMAGE_URL:
        return None
    return url


def _image(found, base):
    for key in IMAGE_KEYS:
        url = image_url(found.get(key), base)
        if url:
            return url
    return None


def extract_blurb(html):
    """og:description > twitter:description > description, as clean text."""
    return _blurb(_meta(html))


def extract_page(html, base):
    """(blurb, image URL or None) from a page's head."""
    found = _meta(html)
    return _blurb(found), _image(found, base)


_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([A-Za-z0-9_.:-]{1,40})""", re.I)


def _charset(ctype_header, head):
    m = re.search(r"charset=[\"']?([A-Za-z0-9_.:-]{1,40})", ctype_header or "", re.I)
    name = m.group(1) if m else None
    if not name:
        m2 = _META_CHARSET.search(head[:4096])
        name = m2.group(1).decode("ascii") if m2 else None
    try:
        if name and getattr(codecs.lookup(name), "_is_text_encoding", True):
            return name
    except LookupError:
        pass
    return "utf-8"


def _decode(body, ctype):
    try:
        return body.decode(_charset(ctype, body), "replace")
    except Exception:  # odd codecs; we only want meta tags anyway
        return body.decode("utf-8", "replace")


class Fetcher:
    def __init__(self, resolve=system_resolve, connection=PinnedHTTPSConnection,
                 clock=time.monotonic, max_bytes=MAX_BYTES):
        self.resolve = resolve
        self.connection = connection
        self.clock = clock
        self.max_bytes = max_bytes

    def blurb(self, url):
        """Clean share-blurb text for url ('' if the page has none).
        Raises Blocked or FetchError."""
        return self.page(url)[0]

    def page(self, url):
        """(blurb, share image URL or None) for url. Raises Blocked or FetchError."""
        deadline = self.clock() + DEADLINE
        for _hop in range(MAX_REDIRECTS + 1):
            if self.clock() > deadline:
                raise FetchError("deadline")
            host, path = check_url(url)
            ip = pick_address(host, self.resolve)
            conn = self.connection(host, ip, timeout=TIMEOUT)
            # The clock checks below only run between reads; this timer is what
            # actually bounds a server that dribbles one byte per timeout.
            watchdog = threading.Timer(max(0.0, deadline - self.clock()),
                                       getattr(conn, "abort", lambda: None))
            watchdog.daemon = True
            watchdog.start()
            try:
                location, body, ctype = self._get(conn, host, path, deadline)
            except (OSError, http.client.HTTPException, ValueError) as e:
                if self.clock() > deadline:
                    raise FetchError("deadline")
                raise FetchError("network: %s" % type(e).__name__)
            finally:
                watchdog.cancel()
                conn.close()
            if location is None:
                return extract_page(_decode(body, ctype), url)
            url = urllib.parse.urljoin(url, location)
        raise FetchError("too many redirects")

    def _get(self, conn, host, path, deadline):
        # No cookies, no auth, no Accept-Encoding (http.client sends identity).
        conn.request("GET", path, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9",
            "Accept-Language": "en-US,en;q=0.8",
        })
        resp = conn.getresponse()
        if resp.status in (301, 302, 303, 307, 308):
            loc = (resp.getheader("Location") or "").strip()
            if not loc or len(loc) > 2048:
                raise FetchError("redirect without usable Location")
            return loc, b"", ""
        if resp.status != 200:
            raise FetchError("status %d" % resp.status)
        ctype = resp.getheader("Content-Type") or ""
        if ctype.split(";")[0].strip().lower() not in HTML_TYPES:
            raise FetchError("not html")
        if (resp.getheader("Content-Encoding") or "identity").strip().lower() != "identity":
            raise FetchError("encoded body")
        buf = bytearray()
        while len(buf) < self.max_bytes:
            if self.clock() > deadline:
                raise FetchError("deadline")
            chunk = resp.read(min(16384, self.max_bytes - len(buf)))
            if not chunk:
                break
            start = max(0, len(buf) - 8)
            buf += chunk
            if b"</head" in bytes(buf[start:]).lower():
                break
        return None, bytes(buf), ctype
