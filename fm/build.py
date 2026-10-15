"""Static build: python3 -m fm.build --out site [--backfill N]

Runs the live pipeline once as a batch job and writes a site that needs no
server: the static/ files plus JSON. Every collected day is kept in a
persistent archive (archive/YYYY/MM-DD.json, committed to the repo), so the
published site covers every day collected so far.

A day already in the archive is put back into a throwaway Store before the
pick, so the service sees it as cached and never re-picks it: same date, same
story, as on the server. Its summary rows travel with it, so retry times
survive between runs. Nothing here deletes or empties an archive file."""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import date, timedelta

from . import config, server, source, towns
from .cache import Store
from .gnews_decode import Decoder
from .publisher import Blocked, FetchError, Fetcher, image_url
from .sanitize import safe_url
from .service import EMPTY_MESSAGE, FloridaMan, random_grim
from .summaries import Summaries, article_key, overlay
from .validate import ApiError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE_DIR = os.path.join(ROOT, "archive")
STATIC_DIR = os.path.join(ROOT, "static")
MAX_BACKFILL = 366
RETRY_WINDOW = 14  # days: failed summaries this recent are retried by later runs
SUMMARIES_BASE = 60  # per run, plus SUMMARIES_PER_DAY for each backfilled day
SUMMARIES_PER_DAY = 10
MINUTES_BASE = 30  # time for summaries, plus MINUTES_PER_DAY per backfilled day
MINUTES_PER_DAY = 2
INDEX_VERSION = 1
BLOCK_SIGNS = ("redirected", "status 403", "status 429", "status 503")

_STORY_ID = re.compile(r"^[0-9a-f]{16}$")
_ISO = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_DAY_FILE = re.compile(r"^([0-9]{2})-([0-9]{2})\.json$")
# A root-relative or protocol-relative URL in an attribute or CSS url().
_ABSOLUTE = re.compile(r"""(?:\b(?:href|src|action|content)\s*=\s*["']?|url\(\s*["']?)\s*(/[^"'\s)>]*)""",
                       re.IGNORECASE)


def _log(fmt, *args):
    sys.stderr.write("build: " + (fmt % args) + "\n")


class BuildError(Exception):
    pass


class Breaker:
    """Wraps the news.google.com fetch. The first sign that Google is
    blocking us (its sorry-page redirect, 403/429/503) trips it, and from
    then on every fetch fails without touching the network."""

    def __init__(self, fetch):
        self.fetch = fetch
        self.reason = None

    def __call__(self, url, **kw):
        if self.reason:
            raise source.UpstreamError("not asking again: " + self.reason)
        try:
            return self.fetch(url, **kw)
        except source.UpstreamError as e:
            if any(sign in str(e) for sign in BLOCK_SIGNS):
                self.reason = str(e)[:120]
            raise


# -- archive ---------------------------------------------------------------

def archive_path(root, d):
    return os.path.join(root, "%04d" % d.year, "%02d-%02d.json" % (d.month, d.day))


def dump_archive(record):
    """Sorted keys and one item per line, so a day's diff is just what changed."""
    return json.dumps(record, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def _check_story(st):
    if not isinstance(st, dict):
        raise ValueError("story is not an object")
    if not isinstance(st.get("id"), str) or not _STORY_ID.match(st["id"]):
        raise ValueError("bad story id")
    if not isinstance(st.get("headline"), str) or not st["headline"]:
        raise ValueError("bad headline")
    if not isinstance(st.get("sourceUrl"), str) or not safe_url(st["sourceUrl"]):
        raise ValueError("bad sourceUrl")
    if not isinstance(st.get("incidentDate"), str) or not _ISO.match(st["incidentDate"]):
        raise ValueError("bad incidentDate")
    date.fromisoformat(st["incidentDate"])
    if not isinstance(st.get("score", 0), int):
        raise ValueError("bad score")


def check_record(rec, d):
    """Raise ValueError unless rec is a well-formed archive record for day d."""
    if not isinstance(rec, dict) or rec.get("date") != d.isoformat():
        raise ValueError("not a record for %s" % d.isoformat())
    if rec.get("story") is not None:
        _check_story(rec["story"])
    more = rec.get("more", [])
    if not isinstance(more, list):
        raise ValueError("more is not a list")
    for st in more:
        _check_story(st)
    rows = rec.get("summaries", {})
    if not isinstance(rows, dict):
        raise ValueError("summaries is not an object")
    for row in rows.values():
        if not isinstance(row, dict) or row.get("status") not in ("ok", "rejected", "failed"):
            raise ValueError("bad summary row")
        if row.get("publisherUrl") is not None and not safe_url(row["publisherUrl"]):
            raise ValueError("bad publisherUrl")
        if row.get("image") is not None and image_url(row["image"], "") != row["image"]:
            raise ValueError("bad image")


def load_archive(root, log=_log):
    """{date: record} for every readable day file. Broken files are logged and
    skipped, never rewritten unless this run collects that day again."""
    records = {}
    if not os.path.isdir(root):
        return records
    for year in sorted(os.listdir(root)):
        ydir = os.path.join(root, year)
        if not re.fullmatch(r"[0-9]{4}", year) or not os.path.isdir(ydir):
            continue
        for fn in sorted(os.listdir(ydir)):
            m = _DAY_FILE.match(fn)
            if not m:
                continue
            try:
                d = date(int(year), int(m.group(1)), int(m.group(2)))
                with open(os.path.join(ydir, fn), encoding="utf-8") as f:
                    rec = json.load(f)
                check_record(rec, d)
            except (OSError, ValueError) as e:
                log("skipping archive/%s/%s: %s", year, fn, str(e)[:80])
                continue
            records[d] = rec
    return records


def _write_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def save_record(root, d, rec):
    """Write a day file if its content changed. True if it was written."""
    path = archive_path(root, d)
    text = dump_archive(rec)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except OSError:
        pass
    _write_atomic(path, text)
    return True


def _stories(rec):
    out = [rec["story"]] if rec.get("story") else []
    return out + list(rec.get("more") or [])


def _row_out(row):
    retry = row.get("retryAt")
    return {"status": row["status"], "fetched": int(row["fetched"]),
            "publisherUrl": row.get("publisherUrl"), "summary": row.get("summary"),
            "image": row.get("image"),
            "retryAt": None if retry is None else int(retry)}


# -- collecting ------------------------------------------------------------

def _day_key(d):
    return "florida-man:%s" % d.isoformat()


def _payload(rec):
    return {k: v for k, v in rec.items() if k != "summaries"}


def collect(app, breaker, days, records, sleep=time.sleep, log=_log):
    """Pick each day through the service. Returns {date: raw payload} for the
    days that worked. Stops at the first sign of a block."""
    got = {}
    for d in days:
        if breaker.reason:
            break
        rec = records.get(d)
        if rec and rec.get("story"):
            # Already picked on an earlier run: the service serves it as cached.
            app.store.put(_day_key(d), _payload(rec))
        while True:
            try:
                payload = app.day_raw(d)
            except ApiError as e:
                if e.code == "rate_limited":
                    sleep(e.extra.get("retryAfter") or 5)
                    continue
                payload = None
                if not breaker.reason:
                    log("%s not collected: %s (%s)", d.isoformat(), e.message, e.code)
            break
        if breaker.reason and not (rec and rec.get("story")):
            # Blocked partway through this day: a pick from fewer years than
            # usual would be stored forever, so leave the day for next time.
            break
        if payload is not None:
            got[d] = payload
    return got


def _wants_summary(row, d, today, now):
    if row is None:
        return True  # never tried
    retry = row.get("retryAt")
    return retry is not None and retry <= now and (today - d).days <= RETRY_WINDOW


def enrich(summaries, store, collected, records, today, limit, deadline, sleep, log=_log):
    """Fill in publisher summaries: the days collected now first (today first),
    then older archived days with stories never tried or due a retry.
    Returns the set of days whose summary rows may have changed."""
    if not summaries.enabled or limit <= 0:
        return set(collected)
    known = {}
    for rec in records.values():
        for key, row in (rec.get("summaries") or {}).items():
            if key not in known or row["fetched"] > known[key]["fetched"]:
                known[key] = row
    now = summaries.clock()
    order = sorted(collected, reverse=True)
    order += sorted((d for d in records if d not in collected), reverse=True)
    touched, queued = set(collected), 0
    for d in order:
        rec = collected.get(d) or records[d]
        if queued >= limit and d not in collected:
            break
        for st in _stories(rec):
            key = article_key(st["sourceUrl"])
            row = known.get(key)
            if d not in collected and not _wants_summary(row, d, today, now):
                continue
            if row is not None and key not in store.get_summaries([key]):
                store.restore_summary(key, row)
            if queued >= limit or summaries.settled(st):
                continue
            if summaries.enqueue(st["sourceUrl"], st["headline"]):
                queued += 1
                touched.add(d)
    done = summaries.drain(limit, deadline=deadline, sleep=sleep)
    left = len(summaries.queued())
    if summaries.decoder.backing_off():
        log("news.google.com pushed back on link decoding; %d summaries done, %d left "
            "for the next run", done, left)
    elif left:
        log("summary cap or time limit reached; %d done, %d left for the next run", done, left)
    else:
        log("%d summaries done", done)
    return touched


# -- the site --------------------------------------------------------------

META_CSP = "; ".join(d for d in server.CSP.split("; ")
                     # Ignored (with a console warning) when delivered by <meta>.
                     if not d.startswith("frame-ancestors"))
META_ANCHOR = '<meta charset="utf-8">'


def absolute_urls(text):
    """Root-relative URLs in HTML or CSS. A Pages project site lives under a
    subpath, so the site must have none."""
    return [m.group(1) for m in _ABSOLUTE.finditer(text)]


def static_index(html):
    """index.html for the static site: CSP and referrer policy as meta tags
    (Pages can't send headers), plus the switch that puts app.js in static mode."""
    if META_ANCHOR not in html:
        raise BuildError("index.html lacks %s" % META_ANCHOR)
    inject = ('\n  <meta http-equiv="Content-Security-Policy" content="%s">'
              '\n  <meta name="referrer" content="no-referrer">'
              '\n  <meta name="fm-mode" content="static">' % META_CSP)
    return html.replace(META_ANCHOR, META_ANCHOR + inject, 1)


def cache_bust(html, static_dir=STATIC_DIR):
    """Stamp style.css and app.js with a hash of their contents, so a phone
    that cached the last build picks up the new one on the next load."""
    for name, attr in (("style.css", "href"), ("app.js", "src")):
        with open(os.path.join(static_dir, name), "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:10]
        ref = '%s="%s"' % (attr, name)
        if ref not in html:
            raise BuildError("index.html lacks %s" % ref)
        html = html.replace(ref, '%s="%s?v=%s"' % (attr, name, digest), 1)
    return html


def site_story(st, rows):
    out = overlay(st, rows.get(article_key(st["sourceUrl"])), False)
    out["shareUrl"] = "?story=" + st["id"]
    return out


def site_day(rec):
    """A day record in the /api/day shape, summaries overlaid, links relative."""
    rows = rec.get("summaries") or {}
    st = rec.get("story")
    out = {k: v for k, v in rec.items() if k not in ("summaries", "cacheKey")}
    out["story"] = site_story(st, rows) if st else None
    # Grim runners-up go to the back; scores archived before a GRIM term was
    # added don't carry its penalty.
    out["more"] = sorted((site_story(s, rows) for s in rec.get("more") or []), key=random_grim)
    out["empty"] = st is None
    out["message"] = None if st else EMPTY_MESSAGE
    out["shareUrl"] = "?date=" + rec["date"]
    return out


def build_index(records):
    """Every story once, under the newest day that has it:
    {"days": {"YYYY-MM-DD": [[id, incidentDate, town, score, randomOk], ...]}}.
    randomOk is 0 for grim stories, which the dice skip."""
    days, seen = {}, set()
    for d in sorted(records, reverse=True):
        entries = []
        for st in _stories(records[d]):
            if st["id"] in seen:
                continue
            seen.add(st["id"])
            entries.append([st["id"], st["incidentDate"], st.get("town"), st.get("score", 0),
                            0 if random_grim(st) else 1])
        days[d.isoformat()] = entries
    picked = [d for d in records if records[d].get("story")]
    return {"version": INDEX_VERSION,
            "latest": max(picked).isoformat() if picked else None,
            "days": days}


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _prepare_out(out, keep):
    real = os.path.realpath(out)
    for path in keep:
        p = os.path.realpath(path)
        if real == p or p.startswith(real + os.sep):
            raise BuildError("--out must not contain %s" % path)
    if os.path.exists(real):
        if not os.path.isdir(real):
            raise BuildError("%s is not a directory" % out)
        looks_built = (os.path.isfile(os.path.join(real, "index.html"))
                       and os.path.isfile(os.path.join(real, "data", "index.json")))
        if os.listdir(real) and not looks_built:
            raise BuildError("refusing to clear %s: not a previous build" % out)
        shutil.rmtree(real)
    os.makedirs(real)
    return real


def write_site(out, records, today, static_dir=STATIC_DIR, archive_dir=ARCHIVE_DIR):
    """Replace `out` with the site. Returns the date today.json shows."""
    with open(os.path.join(static_dir, "index.html"), encoding="utf-8") as f:
        index_html = cache_bust(static_index(f.read()), static_dir)
    bad = absolute_urls(index_html)
    if bad:
        raise BuildError("index.html has root-relative URLs: %s" % ", ".join(bad[:5]))
    out = _prepare_out(out, (static_dir, archive_dir, ROOT))

    for dirpath, dirnames, filenames in os.walk(static_dir):
        dirnames[:] = sorted(n for n in dirnames if not n.startswith("."))
        rel_dir = os.path.relpath(dirpath, static_dir)
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if fn.startswith(".") or ext not in server.STATIC_TYPES or (rel_dir, fn) == (".", "index.html"):
                continue
            dest = os.path.normpath(os.path.join(out, rel_dir, fn))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(os.path.join(dirpath, fn), dest)
    _write_atomic(os.path.join(out, "index.html"), index_html)

    data = os.path.join(out, "data")
    for d, rec in records.items():
        _write_atomic(os.path.join(data, "days", d.isoformat() + ".json"), _dump(site_day(rec)))
    index = build_index(records)
    _write_atomic(os.path.join(data, "index.json"), _dump(index))
    _write_atomic(os.path.join(data, "towns.json"),
                  _dump({"towns": towns.TOWNS, "aliases": towns.aliases()}))
    shown = today if today in records else date.fromisoformat(index["latest"] or max(records).isoformat())
    _write_atomic(os.path.join(data, "today.json"), _dump(site_day(records[shown])))
    return shown


# -- the whole run ---------------------------------------------------------

def run(out, archive_dir=ARCHIVE_DIR, static_dir=STATIC_DIR, backfill=0,
        max_summaries=None, summary_minutes=None, fetch=source.http_get, decoder=None,
        fetcher=None, tz="America/New_York", home_years=4, fetches_per_minute=30,
        summaries_per_minute=6, now=None, clock=time.time, monotonic=time.monotonic,
        sleep=time.sleep, log=_log):
    """Collect, enrich, archive, publish. Returns the process exit code: 0 if
    a site was written, 1 if there was nothing at all to publish."""
    if max_summaries is None:
        max_summaries = SUMMARIES_BASE + SUMMARIES_PER_DAY * backfill
    if summary_minutes is None:
        summary_minutes = MINUTES_BASE + MINUTES_PER_DAY * backfill
    records = load_archive(archive_dir, log)
    breaker = Breaker(fetch)
    tmp = tempfile.mkdtemp(prefix="fm-build-")
    store = Store(tmp, clock=clock)
    try:
        summaries = Summaries(store, decoder or Decoder(fetch=breaker), fetcher or Fetcher(),
                              per_minute=summaries_per_minute, clock=clock, monotonic=monotonic,
                              queue_max=max(max_summaries, 1))
        app = FloridaMan(store, tz=tz, fetch=breaker,
                         limiter=source.RateLimiter(fetches_per_minute, clock=monotonic),
                         home_years=home_years, now=now, summaries=summaries)
        today = app.today()
        days = [today - timedelta(days=i) for i in range(backfill + 1)]
        collected = collect(app, breaker, days, records, sleep=sleep, log=log)
        touched = set(collected)
        if breaker.reason:
            log("news.google.com is blocking this runner (%s); keeping the archive as is "
                "and publishing what we have", breaker.reason)
        else:
            deadline = monotonic() + summary_minutes * 60
            touched |= enrich(summaries, store, collected, records, today,
                              max_summaries, deadline, sleep, log)
        written = 0
        for d in sorted(touched):
            rec = dict(collected.get(d) or records[d])
            keys = [article_key(st["sourceUrl"]) for st in _stories(rec)]
            # Rows this run didn't load stay as they were.
            rows = {k: v for k, v in ((records.get(d) or {}).get("summaries") or {}).items()
                    if k in keys}
            rows.update((k, _row_out(v)) for k, v in store.get_summaries(keys).items())
            rec["summaries"] = {k: rows[k] for k in sorted(rows)}
            records[d] = rec
            written += save_record(archive_dir, d, rec)
    finally:
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)
    log("collected %d of %d day(s); %d archive file(s) changed; %d day(s) in the archive",
        len(collected), len(days), written, len(records))
    if not records:
        log("nothing to publish: no days collected and the archive is empty")
        return 1
    shown = write_site(out, records, today, static_dir, archive_dir)
    if shown != today:
        log("today (%s) isn't collected; the front page shows %s", today, shown)
    return 0


def fill_images(archive_dir, fetcher, sleep=time.sleep, log=_log, pause=1.0):
    """Fetch share photos for archived rows that have a publisher page but no
    image yet. Touches only publisher sites, one page per `pause` seconds.
    Returns how many photos were found."""
    records = load_archive(archive_dir, log)
    found = 0
    for d, rec in sorted(records.items()):
        changed = False
        for row in (rec.get("summaries") or {}).values():
            if not row.get("publisherUrl") or row.get("image"):
                continue
            try:
                image = fetcher.page(row["publisherUrl"])[1]
            except (Blocked, FetchError) as e:
                log("%s: %s", d, str(e)[:80])
                image = None
            sleep(pause)
            if image:
                row["image"] = image
                found += 1
                changed = True
        if changed:
            save_record(archive_dir, d, rec)
    log("share photos: %d found", found)
    return found


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m fm.build", description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True, help="directory to write the site to (replaced)")
    p.add_argument("--archive", default=ARCHIVE_DIR, help="archive directory (default: repo archive/)")
    p.add_argument("--backfill", type=int, default=0, help="also collect the previous N days")
    p.add_argument("--max-summaries", type=int, default=None,
                   help="stories to enrich this run (default %d + %d per backfilled day)"
                   % (SUMMARIES_BASE, SUMMARIES_PER_DAY))
    p.add_argument("--summary-minutes", type=float, default=None,
                   help="time limit for enrichment (default %d + %d per backfilled day)"
                   % (MINUTES_BASE, MINUTES_PER_DAY))
    p.add_argument("--fill-images", action="store_true",
                   help="only fetch missing share photos for archived stories, then exit")
    args = p.parse_args(argv)
    if args.fill_images:
        fill_images(os.path.abspath(args.archive), Fetcher())
        return 0
    if not 0 <= args.backfill <= MAX_BACKFILL:
        p.error("--backfill must be between 0 and %d" % MAX_BACKFILL)
    cfg = config.Config.from_env()
    try:
        return run(args.out, archive_dir=os.path.abspath(args.archive), backfill=args.backfill,
                   max_summaries=args.max_summaries, summary_minutes=args.summary_minutes,
                   tz=cfg.tz, home_years=cfg.home_years,
                   fetches_per_minute=cfg.fetches_per_minute,
                   summaries_per_minute=cfg.summaries_per_minute)
    except BuildError as e:
        _log("error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
