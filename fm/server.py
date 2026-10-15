"""HTTP layer: JSON API under /api/, static files from static/, nothing else."""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import summaries as enrich, towns, validate
from .validate import ApiError

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "font-src 'self'; "
    "img-src 'self' data: https:; "  # story photos hotlink the publisher's share image
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".json": "application/json",
    ".webmanifest": "application/manifest+json",
    ".txt": "text/plain; charset=utf-8",
}
# Client-side routes that all serve the single page.
PAGE_ROUTES = ("/", "/search")
MAX_BODY = 1024
MAX_STATIC = 5 * 1024 * 1024


def _cache_for(payload, cache):
    """A payload with a summary still on its way must not be cached by the
    browser, or the frontend's re-poll would just get its own copy back."""
    if any(s.get("summaryPending") for s in enrich.payload_stories(payload)):
        return "no-store"
    return cache


def make_handler(app, static_dir):
    static_root = os.path.realpath(static_dir)

    class Handler(BaseHTTPRequestHandler):
        server_version = "florida-man"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = 30  # drop idle/slow clients instead of holding a thread forever

        # -- plumbing ------------------------------------------------------
        def log_message(self, fmt, *args):
            # Request lines are attacker-controlled; keep terminal escapes out of logs.
            line = (fmt % args).encode("unicode_escape").decode("ascii")[:500]
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), line))

        def _send(self, status, body=b"", ctype=None, headers=None):
            self.send_response(status)
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            if ctype:
                self.send_header("Content-Type", ctype)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, obj, cache="no-store", headers=None):
            if isinstance(obj, dict):
                cache = _cache_for(obj, cache)
            body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            h = {"Cache-Control": cache}
            h.update(headers or {})
            self._send(status, body, "application/json; charset=utf-8", h)

        def _error(self, err):
            headers = {}
            if "retryAfter" in err.extra:
                headers["Retry-After"] = str(err.extra["retryAfter"])
            self._json(err.status, err.body(), headers=headers)

        def _not_found(self):
            self._error(ApiError(404, "not_found", "Nothing here."))

        def send_error(self, code, message=None, explain=None):
            # Replaces the stdlib HTML error page (which echoes the request).
            self.close_connection = True
            try:
                self._json(code, {"error": {"code": "http_%d" % code, "message": "Bad request."}})
            except OSError:
                pass

        # -- methods -------------------------------------------------------
        def do_GET(self):
            try:
                self._route_get()
            except ApiError as e:
                self._error(e)
            except Exception as e:  # never leak a traceback to the client
                self.log_message("internal error: %r", e)
                self._error(ApiError(500, "internal_error", "Something went sideways."))

        do_HEAD = do_GET

        def do_POST(self):
            try:
                path = urlsplit(self.path).path
                if path != "/api/track":
                    raise ApiError(405, "method_not_allowed", "POST is only for /api/track.")
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    raise ApiError(415, "unsupported_media_type", "Send application/json.")
                try:
                    length = int(self.headers.get("Content-Length") or "0")
                except ValueError:
                    length = -1
                if length < 0 or length > MAX_BODY:
                    raise ApiError(413, "body_too_large", "Body must be under 1 KB.")
                raw = self.rfile.read(length)
                try:
                    payload = json.loads(raw.decode("utf-8") or "null")
                except (UnicodeDecodeError, ValueError):
                    raise ApiError(400, "invalid_body", "Body must be valid JSON.")
                app.track(payload)
                self._send(204)
            except ApiError as e:
                self._error(e)
            except Exception as e:
                self.log_message("internal error: %r", e)
                self._error(ApiError(500, "internal_error", "Something went sideways."))

        def _method_not_allowed(self):
            self._error(ApiError(405, "method_not_allowed", "Method not allowed."))

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed

        # -- routing -------------------------------------------------------
        def _route_get(self):
            if len(self.path) > 2048:
                raise ApiError(414, "uri_too_long", "URL too long.")
            parts = urlsplit(self.path)
            path = parts.path
            try:
                q = parse_qs(parts.query, max_num_fields=10, strict_parsing=False)
            except ValueError:
                raise ApiError(400, "invalid_query", "Too many query parameters.")

            def param(name):
                vals = q.get(name)
                return vals[0] if vals else None

            if path.startswith("/api/"):
                if path == "/api/today":
                    return self._json(200, app.today_pick(), cache="public, max-age=300")
                if path == "/api/day":
                    d = validate.full_date(param("date"), app.today())
                    return self._json(200, app.day(d), cache="public, max-age=300")
                if path == "/api/search":
                    return self._json(200, app.search(param("town"), param("date")),
                                      cache="public, max-age=300")
                if path == "/api/random":
                    return self._json(200, app.random_pick(), cache="no-store")
                if path == "/api/story":
                    return self._json(200, app.story(param("id")), cache="public, max-age=3600")
                if path == "/api/towns":
                    return self._json(200, {"towns": towns.TOWNS}, cache="public, max-age=86400")
                if path == "/api/health":
                    return self._json(200, {"ok": True})
                if path == "/api/track":
                    raise ApiError(405, "method_not_allowed", "Use POST.")
                return self._not_found()
            if path in PAGE_ROUTES:
                return self._static("index.html")
            return self._static(path.lstrip("/"))

        def _static(self, rel):
            # No directories, no dotfiles, no traversal, known types only.
            if not rel or rel.endswith("/") or "\\" in rel or "\x00" in rel:
                return self._not_found()
            if any(seg.startswith(".") or seg == "" for seg in rel.split("/")):
                return self._not_found()
            ext = os.path.splitext(rel)[1].lower()
            ctype = STATIC_TYPES.get(ext)
            if not ctype:
                return self._not_found()
            full = os.path.realpath(os.path.join(static_root, rel))
            if os.path.commonpath([full, static_root]) != static_root or not os.path.isfile(full):
                return self._not_found()
            if os.path.getsize(full) > MAX_STATIC:
                return self._not_found()
            with open(full, "rb") as f:
                body = f.read()
            self._send(200, body, ctype, {"Cache-Control": "no-cache"})

    return Handler


def serve(app, host, port, static_dir):
    httpd = ThreadingHTTPServer((host, port), make_handler(app, static_dir))
    httpd.daemon_threads = True
    return httpd
