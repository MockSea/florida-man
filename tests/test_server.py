"""HTTP layer tests. Driven over a socketpair so no port is bound."""
import json
import os
import socket
import threading

from fm import server
from tests.helpers import DAY_ROUTES, AppTestCase, FakeFetch

STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


class HttpTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app = self.make_app(FakeFetch(DAY_ROUTES))
        self.handler = server.make_handler(self.app, STATIC)
        self.handler.log_message = lambda *a: None

    def serve_one(self, sock):
        def run():
            try:
                self.handler(sock, ("127.0.0.1", 0), None)
            finally:
                sock.close()  # what socketserver's shutdown_request does
        t = threading.Thread(target=run)
        t.start()
        return t

    def request(self, method, path, body=b"", headers=None):
        a, b = socket.socketpair()
        hdrs = {"Host": "localhost", "Connection": "close", "Content-Length": str(len(body))}
        hdrs.update(headers or {})
        raw = "%s %s HTTP/1.1\r\n" % (method, path)
        raw += "".join("%s: %s\r\n" % kv for kv in hdrs.items()) + "\r\n"
        a.sendall(raw.encode("latin-1") + body)
        t = self.serve_one(b)
        chunks = []
        while True:
            c = a.recv(65536)
            if not c:
                break
            chunks.append(c)
        t.join()
        a.close()
        head, _, payload = b"".join(chunks).partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        status = int(lines[0].split()[1])
        h = {}
        for line in lines[1:]:
            k, _, v = line.partition(":")
            h[k.strip().lower()] = v.strip()
        return status, h, payload

    def get_json(self, path):
        status, h, body = self.request("GET", path)
        self.assertTrue(h["content-type"].startswith("application/json"), path)
        return status, h, json.loads(body)

    def test_security_headers_everywhere(self):
        for path in ("/", "/app.js", "/api/today", "/api/nope", "/nope.js"):
            _, h, _ = self.request("GET", path)
            self.assertIn("script-src 'self'", h["content-security-policy"], path)
            self.assertNotIn("unsafe-inline", h["content-security-policy"])
            self.assertEqual(h["x-content-type-options"], "nosniff")
            self.assertEqual(h["referrer-policy"], "no-referrer")
            self.assertNotIn("python", h.get("server", "").lower())

    def test_index_and_static(self):
        status, h, body = self.request("GET", "/?story=abc")
        self.assertEqual(status, 200)
        self.assertTrue(h["content-type"].startswith("text/html"))
        self.assertIn(b'<script src="app.js"', body)
        self.assertNotIn(b"<script>", body)  # no inline script
        status, h, _ = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertTrue(h["content-type"].startswith("text/javascript"))

    def test_no_traversal_or_listing(self):
        for path in ("/../fm/server.py", "/..%2ffm/server.py", "/static/", "/fm/", "/.git",
                     "/../README.md", "/%2e%2e/README.md", "//etc/passwd", "/index.html/",
                     "/data/florida-man.sqlite3", "/tests/fixtures/empty.xml", "/.hidden.js"):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 404, path)

    def test_today(self):
        status, h, data = self.get_json("/api/today")
        self.assertEqual(status, 200)
        self.assertEqual(data["cacheKey"], "florida-man:2026-10-14")
        self.assertIsNotNone(data["story"])

    def test_statewide_search_and_random(self):
        status, _, data = self.get_json("/api/search?date=2025-10-14")
        self.assertEqual(status, 200)
        self.assertIsNone(data["query"]["town"])
        self.assertGreater(data["count"], 0)
        status, h, data = self.get_json("/api/random")
        self.assertEqual(status, 200)
        self.assertIn("story", data)
        self.assertIn("empty", data)

    def test_bad_params(self):
        status, _, data = self.get_json("/api/search?town=Atlanta&date=10-14")
        self.assertEqual(status, 400)
        self.assertEqual(data["error"]["code"], "invalid_town")
        self.assertEqual(data["error"]["field"], "town")
        status, _, data = self.get_json("/api/search?town=Tampa&date=2025-13-01")
        self.assertEqual((status, data["error"]["code"]), (400, "invalid_date"))
        status, _, data = self.get_json("/api/day?date=tomorrow")
        self.assertEqual(status, 400)
        status, _, data = self.get_json("/api/story?id=nope")
        self.assertEqual(status, 400)

    def test_track(self):
        hj = {"Content-Type": "application/json"}
        status, _, _ = self.request("POST", "/api/track", b'{"event":"copy"}', hj)
        self.assertEqual(status, 204)
        self.assertEqual(self.stores[0].counters()["copy:total"], 1)
        status, _, _ = self.request("POST", "/api/track", b'{"event":"copy"}',
                                    {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        status, _, _ = self.request("POST", "/api/track", b"x" * 5000, hj)
        self.assertEqual(status, 413)
        status, _, _ = self.request("POST", "/api/track", b"{nope", hj)
        self.assertEqual(status, 400)
        status, _, _ = self.request("GET", "/api/track")
        self.assertEqual(status, 405)
        status, _, _ = self.request("POST", "/api/today", b"{}", hj)
        self.assertEqual(status, 405)
        status, _, _ = self.request("DELETE", "/api/today")
        self.assertEqual(status, 405)

    def test_malformed_request_line(self):
        a, b = socket.socketpair()
        a.sendall(b"GARBAGE <script>\r\n\r\n")
        t = self.serve_one(b)
        out = a.recv(65536)
        t.join()
        a.close()
        self.assertNotIn(b"<script>", out)

    def test_bind_is_localhost_only(self):
        from fm import config
        self.assertEqual(config.HOST, "127.0.0.1")
