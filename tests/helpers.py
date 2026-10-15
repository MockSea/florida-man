"""Shared test fixtures: fake upstream, fixed clock, temp data dir."""
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from fm import source
from fm.cache import Store
from fm.service import FloridaMan

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=ET)


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return f.read()


class FakeFetch:
    """Maps a substring of the decoded `q` param to a fixture file.
    Unmatched queries get an empty feed. Records every query."""

    def __init__(self, routes=None, fail=False):
        self.routes = routes or {}
        self.fail = fail
        self.queries = []

    def __call__(self, url):
        q = parse_qs(urlsplit(url).query)["q"][0]
        self.queries.append(q)
        if self.fail:
            raise source.UpstreamError("boom")
        for needle, name in self.routes.items():
            if needle in q:
                return fixture(name)
        return fixture("empty.xml")


DAY_ROUTES = {"after:2025-10-13 before:2025-10-15": "day_2025-10-14.xml"}
TAMPA_ROUTES = {'"Tampa" after:2024-10-13 before:2024-10-15': "tampa_2024-10-14.xml"}


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fm-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stores = []

    def make_app(self, fetch, per_minute=60, now=NOW, **kw):
        store = Store(self.tmp)
        self.stores.append(store)
        self.addCleanup(store.close)
        return FloridaMan(store, fetch=fetch, limiter=source.RateLimiter(per_minute),
                          now=lambda: now, **kw)
