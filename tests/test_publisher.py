import ssl
import unittest
from unittest import mock

from fm import publisher as pub
from fm.source import USER_AGENT

PAGE = (b'<!doctype html><html><head><meta charset="utf-8"><title>x</title>'
        b'<meta name="description" content="Plain description of the raccoon boat lawyer.">'
        b'<meta name="twitter:description" content="Twitter text about the raccoon boat lawyer.">'
        b'<meta property="og:description" content="OG text about the raccoon boat lawyer.">'
        b'</head><body><p>Article body we must never read.</p></body></html>')


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.body = body
        self.pos = 0
        self.read_calls = 0

    def getheader(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def read(self, n):
        self.read_calls += 1
        chunk = self.body[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk


class FakeNet:
    """Resolver + connection factory. `sites` maps host -> (addrs, response)."""

    def __init__(self, sites):
        self.sites = sites
        self.resolved = []
        self.connected = []
        self.requests = []
        self.responses = []

    def resolve(self, host):
        self.resolved.append(host)
        if host not in self.sites:
            raise OSError("NXDOMAIN")
        return self.sites[host][0]

    def connection(self, host, ip, timeout):
        net = self

        class Conn:
            def request(self, method, path, headers):
                net.requests.append((host, ip, method, path, headers))

            def getresponse(self):
                r = net.sites[host][1]
                r = r(net.requests[-1][3]) if callable(r) else r
                net.responses.append(r)
                return r

            def close(self):
                pass

        self.connected.append((host, ip, timeout))
        return Conn()

    def fetcher(self, **kw):
        return pub.Fetcher(resolve=self.resolve, connection=self.connection, **kw)


def html(body=PAGE, ctype="text/html; charset=utf-8", **headers):
    return FakeResponse(200, dict({"Content-Type": ctype}, **headers), body)


def redirect(loc, code=302):
    return FakeResponse(code, {"Location": loc})


GOOD = ["93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"]


class UrlPolicyTests(unittest.TestCase):
    def test_refused_urls_never_resolve_or_connect(self):
        for url in ("http://example.com/a", "https://example.com:8080/a", "https://example.com:80/a",
                    "https://127.0.0.1/a", "https://[::1]/a", "https://2130706433/a",
                    "https://0x7f.1/a", "https://localhost/a", "https://intranet/a",
                    "https://foo.localhost/a", "https://printer.local/a", "https://box.tailnet.ts.net/a",
                    "https://user:pw@example.com/a", "ftp://example.com/a", "https://exa_mple.com/a",
                    "https://example.com./a", "javascript:alert(1)", ""):
            net = FakeNet({})
            with self.assertRaises(pub.Blocked, msg=url):
                net.fetcher().blurb(url)
            self.assertEqual((net.resolved, net.connected), ([], []), url)

    def test_https_443_hostname_ok(self):
        self.assertEqual(pub.check_url("https://Example.COM:443/a?b=1"), ("example.com", "/a?b=1"))
        self.assertEqual(pub.check_url("https://example.com"), ("example.com", "/"))
        self.assertEqual(pub.check_url("https://example.com/café?q=a%20b#frag"),
                         ("example.com", "/caf%C3%A9?q=a%20b"))
        self.assertEqual(pub.check_url("https://bücher.example/")[0], "xn--bcher-kva.example")


class AddressTests(unittest.TestCase):
    BAD = [
        "127.0.0.1", "127.8.9.10", "10.1.2.3", "172.16.0.1", "172.31.255.255", "192.168.1.1",
        "169.254.169.254", "100.64.0.1", "100.100.100.100", "100.127.255.254", "0.0.0.0",
        "0.1.2.3", "224.0.0.1", "239.255.255.250", "255.255.255.255", "240.0.0.1",
        "192.0.2.1", "198.18.0.1",
        "::1", "::", "fe80::1", "fc00::1", "fd7a:115c:a1e0::1", "ff02::1",
        "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:100.64.0.1", "::ffff:169.254.169.254",
        "::127.0.0.1", "::10.0.0.1",
        "64:ff9b::7f00:1", "64:ff9b::a00:1", "64:ff9b::6440:1", "64:ff9b:1::a00:1",
        "2002:7f00:1::", "2002:a00:1::1", "fe80::1%en0", "not-an-ip",
    ]

    def test_blocked_classes(self):
        for a in self.BAD:
            self.assertFalse(pub.address_ok(a), a)

    def test_public_addresses_ok(self):
        for a in GOOD + ["8.8.8.8", "1.1.1.1", "64:ff9b::808:808", "::ffff:8.8.8.8"]:
            self.assertTrue(pub.address_ok(a), a)

    def test_any_bad_address_refuses_the_host(self):
        for bad in ("127.0.0.1", "::ffff:192.168.0.1", "100.101.102.103"):
            net = FakeNet({"example.com": (GOOD + [bad], html())})
            with self.assertRaises(pub.Blocked, msg=bad):
                net.fetcher().blurb("https://example.com/a")
            self.assertEqual(net.connected, [])

    def test_no_addresses_is_a_failure(self):
        net = FakeNet({"example.com": ([], html())})
        with self.assertRaises(pub.FetchError):
            net.fetcher().blurb("https://example.com/a")
        with self.assertRaises(pub.FetchError):
            net.fetcher().blurb("https://nxdomain.example/a")


class FetchTests(unittest.TestCase):
    def test_happy_path_pins_resolved_ip_once(self):
        net = FakeNet({"example.com": (GOOD, html())})
        text = net.fetcher().blurb("https://example.com/story?id=1")
        self.assertEqual(text, "OG text about the raccoon boat lawyer.")
        self.assertEqual(net.resolved, ["example.com"])  # one lookup
        self.assertEqual(net.connected, [("example.com", GOOD[0], pub.TIMEOUT)])
        host, ip, method, path, headers = net.requests[0]
        self.assertEqual((method, path), ("GET", "/story?id=1"))
        self.assertEqual(headers["User-Agent"], USER_AGENT)
        lower = {k.lower() for k in headers}
        for h in ("cookie", "authorization", "accept-encoding", "proxy-authorization"):
            self.assertNotIn(h, lower)

    def test_meta_precedence(self):
        og = b'<meta property="og:description" content="OG text about the raccoon.">'
        tw = b'<meta name="twitter:description" content="Twitter text about the raccoon.">'
        de = b'<meta name="description" content="Description about the raccoon.">'
        cases = [(og + tw + de, "OG text"), (de + tw, "Twitter text"), (de, "Description"),
                 (b'<meta property="og:description" content="  ">' + de, "Description"),
                 (b"<title>none</title>", "")]
        for metas, want in cases:
            body = b"<html><head>" + metas + b"</head><body></body></html>"
            net = FakeNet({"example.com": (GOOD, html(body))})
            got = net.fetcher().blurb("https://example.com/")
            self.assertTrue(got.startswith(want), (metas, got))

    def test_meta_after_head_ignored(self):
        body = (b'<html><head><title>t</title></head><body>'
                b'<meta property="og:description" content="Smuggled in the body of the page."></body>')
        self.assertEqual(FakeNet({"example.com": (GOOD, html(body))}).fetcher().blurb("https://example.com/"), "")

    def test_blurb_html_and_entities_cleaned(self):
        body = (b'<html><head><meta property="og:description" content="'
                b'&lt;b&gt;Florida man&lt;/b&gt; &amp;amp; his &quot;emotional support&quot; '
                b'gator &lt;script&gt;alert(1)&lt;/script&gt;\xe2\x80\xae went to Publix."></head>')
        text = FakeNet({"example.com": (GOOD, html(body))}).fetcher().blurb("https://example.com/")
        self.assertEqual(text, 'Florida man & his "emotional support" gator went to Publix.')

    def test_non_html_refused(self):
        for ctype in ("application/json", "image/png", "text/plain", "", "text/htmlx"):
            net = FakeNet({"example.com": (GOOD, html(ctype=ctype))})
            with self.assertRaises(pub.FetchError, msg=ctype):
                net.fetcher().blurb("https://example.com/")
        ok = FakeNet({"example.com": (GOOD, html(ctype="application/xhtml+xml"))})
        self.assertTrue(ok.fetcher().blurb("https://example.com/"))

    def test_encoded_body_refused(self):
        net = FakeNet({"example.com": (GOOD, html(**{"Content-Encoding": "gzip"}))})
        with self.assertRaises(pub.FetchError):
            net.fetcher().blurb("https://example.com/")

    def test_non_200_is_failure(self):
        for status in (404, 429, 500, 204):
            net = FakeNet({"example.com": (GOOD, FakeResponse(status, {"Content-Type": "text/html"}, PAGE))})
            with self.assertRaises(pub.FetchError):
                net.fetcher().blurb("https://example.com/")

    def test_size_cap(self):
        big = b"<html><head>" + b"<!-- pad -->" * 100000 + PAGE  # meta past the cap
        resp = html(big)
        net = FakeNet({"example.com": (GOOD, resp)})
        self.assertEqual(net.fetcher().blurb("https://example.com/"), "")
        self.assertLessEqual(resp.pos, pub.MAX_BYTES)
        self.assertEqual(net.fetcher(max_bytes=1000).blurb("https://example.com/"), "")

    def test_stops_reading_at_end_of_head(self):
        body = PAGE + b"x" * 400000
        resp = html(body)
        FakeNet({"example.com": (GOOD, resp)}).fetcher().blurb("https://example.com/")
        self.assertLess(resp.pos, 20000)

    def test_deadline(self):
        t = [0.0]

        class Slow(FakeResponse):
            def read(self, n):
                t[0] += 5
                return b"<!-- drip -->" * 10

        net = FakeNet({"example.com": (GOOD, Slow(200, {"Content-Type": "text/html"}))})
        with self.assertRaises(pub.FetchError):
            net.fetcher(clock=lambda: t[0]).blurb("https://example.com/")

    def test_charset_lenient(self):
        body = b'<html><head><meta property="og:description" content="Caf\xe9 owner chases gator."></head>'
        text = FakeNet({"example.com": (GOOD, html(body, ctype="text/html; charset=latin-1"))}).fetcher().blurb(
            "https://example.com/")
        self.assertEqual(text, "Café owner chases gator.")
        for cs in ("rot13", "zlib", "nonsense", "utf-8"):
            FakeNet({"example.com": (GOOD, html(body, ctype="text/html; charset=" + cs))}).fetcher().blurb(
                "https://example.com/")


class RedirectTests(unittest.TestCase):
    def test_followed_and_revalidated(self):
        def a_example(path):
            return redirect("https://www.a.example/final" if path == "/moved?x=1" else "/moved?x=1")
        net = FakeNet({"a.example": (GOOD, a_example), "www.a.example": (["8.8.4.4"], html())})
        self.assertTrue(net.fetcher().blurb("https://a.example/start").startswith("OG text"))
        self.assertEqual([r[3] for r in net.requests], ["/start", "/moved?x=1", "/final"])
        self.assertEqual(net.resolved, ["a.example", "a.example", "www.a.example"])
        self.assertEqual(net.connected[-1][:2], ("www.a.example", "8.8.4.4"))

    def test_redirect_to_private_address_refused(self):
        net = FakeNet({"a.example": (GOOD, redirect("https://rebind.example/")),
                       "rebind.example": (["192.168.1.10"], html())})
        with self.assertRaises(pub.Blocked):
            net.fetcher().blurb("https://a.example/")
        self.assertEqual([c[0] for c in net.connected], ["a.example"])

    def test_redirect_to_refused_url_forms(self):
        for loc in ("http://a.example/", "https://a.example:8443/", "https://127.0.0.1/",
                    "https://[::ffff:7f00:1]/", "https://localhost/", "file:///etc/passwd"):
            net = FakeNet({"a.example": (GOOD, redirect(loc))})
            with self.assertRaises(pub.Blocked, msg=loc):
                net.fetcher().blurb("https://a.example/")

    def test_redirect_cap(self):
        hops = []

        def loop(path):
            hops.append(path)
            return redirect("/r%d" % len(hops))
        net = FakeNet({"a.example": (GOOD, loop)})
        with self.assertRaises(pub.FetchError):
            net.fetcher().blurb("https://a.example/")
        self.assertEqual(len(hops), pub.MAX_REDIRECTS + 1)

        hops.clear()
        net = FakeNet({"a.example": (GOOD, lambda p: loop(p) if len(hops) < 3 else html())})
        self.assertTrue(net.fetcher().blurb("https://a.example/"))  # exactly 3 redirects is fine


class PinnedConnectionTests(unittest.TestCase):
    def test_connects_to_pinned_ip_with_hostname_for_tls(self):
        ctx = mock.Mock()
        sock = object()
        with mock.patch.object(pub.socket, "create_connection", return_value=sock) as cc, \
                mock.patch.object(pub.socket, "getaddrinfo", side_effect=AssertionError("DNS!")):
            conn = pub.PinnedHTTPSConnection("example.com", "93.184.215.14", timeout=8, context=ctx)
            conn.connect()
        cc.assert_called_once_with(("93.184.215.14", 443), 8)
        ctx.wrap_socket.assert_called_once_with(sock, server_hostname="example.com")

    def test_default_context_verifies(self):
        conn = pub.PinnedHTTPSConnection("example.com", "93.184.215.14")
        self.assertEqual(conn._fm_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(conn._fm_context.check_hostname)
        with self.assertRaises(pub.Blocked):
            conn.set_tunnel("proxy.example", 8080)


if __name__ == "__main__":
    unittest.main()


class DribbleDeadlineTest(unittest.TestCase):
    """A real socket that sends one byte at a time, slower than nothing but
    faster than the per-recv timeout: only the watchdog can stop it."""

    def setUp(self):
        import socket
        import threading
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.stop = threading.Event()

        def serve():
            c, _ = self.srv.accept()
            c.recv(4096)
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                      b"Content-Length: 100000\r\n\r\n")
            while not self.stop.wait(0.2):
                try:
                    c.sendall(b"x")
                except OSError:
                    break
            c.close()
        threading.Thread(target=serve, daemon=True).start()

    def tearDown(self):
        self.stop.set()
        self.srv.close()

    def test_watchdog_stops_dribble(self):
        import http.client
        import socket
        import time
        port = self.port

        class Plain(http.client.HTTPConnection):
            def __init__(self, host, ip, timeout=None):
                super().__init__("127.0.0.1", port, timeout=timeout)

            def connect(self):
                super().connect()
                self._fm_raw = self.sock

            def abort(self):
                pub._shutdown(getattr(self, "_fm_raw", None))

        f = pub.Fetcher(resolve=lambda h: ["93.184.216.34"], connection=Plain)
        with mock.patch.object(pub, "DEADLINE", 1.0), \
                mock.patch.object(pub, "TIMEOUT", 0.5):
            t0 = time.monotonic()
            with self.assertRaises(pub.FetchError) as cm:
                f.blurb("https://example.com/story")
        self.assertLess(time.monotonic() - t0, 3.0)
        self.assertIn("deadline", str(cm.exception))
