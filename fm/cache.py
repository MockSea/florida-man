"""SQLite-backed persistence: day picks, search cache, stories, summaries, counters."""
import json
import os
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    created REAL NOT NULL,
    expires REAL            -- NULL = never
);
CREATE TABLE IF NOT EXISTS stories (
    id TEXT PRIMARY KEY,
    json TEXT NOT NULL,
    seen REAL NOT NULL
);
-- Publisher blurbs, keyed by article (hash of the Google News link), so the
-- same article seen statewide and in a town search is fetched once.
CREATE TABLE IF NOT EXISTS summaries (
    article TEXT PRIMARY KEY,
    publisher_url TEXT,     -- decoded link, NULL if unknown/refused
    summary TEXT,           -- NULL unless status = 'ok'
    status TEXT NOT NULL,   -- ok | rejected | failed
    fetched REAL NOT NULL,
    retry_at REAL           -- NULL = never retry
);
CREATE TABLE IF NOT EXISTS counters (
    name TEXT PRIMARY KEY,
    n INTEGER NOT NULL
);
"""

MAX_STORIES = 5000
MAX_SUMMARIES = 20000


class Store:
    def __init__(self, data_dir, clock=time.time):
        os.makedirs(data_dir, mode=0o700, exist_ok=True)
        self.path = os.path.join(data_dir, "florida-man.sqlite3")
        self.clock = clock
        self.lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(_SCHEMA)

    def close(self):
        with self.lock:
            self.db.close()

    # -- key/value -------------------------------------------------------
    def get(self, key):
        """Return (value, fresh) or (None, False). Expired rows are returned
        with fresh=False so callers can serve stale data when needed."""
        with self.lock:
            row = self.db.execute(
                "SELECT value, expires FROM kv WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None, False
        value, expires = row
        return json.loads(value), expires is None or expires > self.clock()

    def put(self, key, value, ttl=None):
        now = self.clock()
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO kv (key, value, created, expires) VALUES (?, ?, ?, ?)",
                (key, json.dumps(value), now, None if ttl is None else now + ttl),
            )

    def purge_expired(self, older_than=86400):
        with self.lock:
            self.db.execute(
                "DELETE FROM kv WHERE expires IS NOT NULL AND expires < ?",
                (self.clock() - older_than,),
            )

    # -- stories (for share permalinks) ---------------------------------
    def put_stories(self, stories):
        if not stories:
            return
        now = self.clock()
        with self.lock:
            self.db.execute("BEGIN")
            for s in stories:
                self.db.execute(
                    "INSERT OR REPLACE INTO stories (id, json, seen) VALUES (?, ?, ?)",
                    (s["id"], json.dumps(s), now),
                )
            self.db.execute(
                "DELETE FROM stories WHERE id NOT IN "
                "(SELECT id FROM stories ORDER BY seen DESC LIMIT ?)",
                (MAX_STORIES,),
            )
            self.db.execute("COMMIT")

    def get_story(self, story_id):
        with self.lock:
            row = self.db.execute(
                "SELECT json FROM stories WHERE id = ?", (story_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    # -- summaries -------------------------------------------------------
    def get_summaries(self, articles):
        """{article: row dict} for the given article keys that have a row."""
        articles = list(dict.fromkeys(articles))[:500]
        if not articles:
            return {}
        marks = ",".join("?" * len(articles))
        with self.lock:
            rows = self.db.execute(
                "SELECT article, publisher_url, summary, status, fetched, retry_at "
                "FROM summaries WHERE article IN (%s)" % marks, articles
            ).fetchall()
        keys = ("article", "publisherUrl", "summary", "status", "fetched", "retryAt")
        return {r[0]: dict(zip(keys, r)) for r in rows}

    def put_summary(self, article, status, publisher_url=None, summary=None, retry_in=None):
        now = self.clock()
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO summaries "
                "(article, publisher_url, summary, status, fetched, retry_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (article, publisher_url, summary, status, now,
                 None if retry_in is None else now + retry_in),
            )
            self.db.execute(
                "DELETE FROM summaries WHERE article NOT IN "
                "(SELECT article FROM summaries ORDER BY fetched DESC LIMIT ?)",
                (MAX_SUMMARIES,),
            )

    def restore_summary(self, article, row):
        """Put back a row exported by get_summaries, keeping its own fetched
        and retryAt times (the batch build carries them in the archive)."""
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO summaries "
                "(article, publisher_url, summary, status, fetched, retry_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (article, row.get("publisherUrl"), row.get("summary"), row["status"],
                 row["fetched"], row.get("retryAt")),
            )

    # -- counters --------------------------------------------------------
    def incr(self, name, by=1):
        with self.lock:
            self.db.execute(
                "INSERT INTO counters (name, n) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET n = n + excluded.n",
                (name, by),
            )

    def counters(self):
        with self.lock:
            return dict(self.db.execute("SELECT name, n FROM counters ORDER BY name"))
