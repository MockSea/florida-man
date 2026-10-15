# How it works

Florida Man of the Day picks one deterministic "Florida Man" story per date, with search by date and town. The backend uses only the Python 3.9 standard library: no pip, no Node, and no AI/LLM anywhere.

- `fm/`: the backend
  - `source.py`: Google News RSS fetch and parse
  - `gnews_decode.py`, `publisher.py`, `summaries.py`: background publisher summaries (link decode, guarded page fetch, queue + worker)
  - `score.py`: heuristic scoring
  - `cache.py`: SQLite storage
  - `service.py`: business logic
  - `server.py`: HTTP layer
  - `validate.py`, `towns.py` + `data/*.txt`: input validation and the town list
- `static/`: the frontend, served as-is. It talks to the API in [`API.md`](../API.md), or to the static build's JSON files.
- `tests/`: stdlib `unittest`, offline, using fixture RSS.

## Run

```sh
python3 -m fm
# Florida Man of the Day on http://127.0.0.1:7510 (data: /path/to/repo/data)
```

The server always binds to **127.0.0.1**, and that is not configurable. Put a reverse proxy in front if you want it reachable from elsewhere.

| Env var | Default | Meaning |
|---|---|---|
| `FM_PORT` | `7510` | Port to listen on |
| `FM_DATA_DIR` | `<repo>/data` | SQLite file: day picks, search cache, story permalinks, share counters |
| `FM_TZ` | `America/New_York` | Timezone that defines "today" |
| `FM_HOME_YEARS` | `4` | Past years searched for the daily pick |
| `FM_SEARCH_YEARS` | `3` | Occurrences of the month/day searched for `MM-DD` searches |
| `FM_SEARCH_TTL` | `3600` | Search cache lifetime in seconds |
| `FM_FETCHES_PER_MINUTE` | `30` | Ceiling on RSS search requests to news.google.com. Over budget, stale cache is served, or a 503 if there is none. |
| `FM_SUMMARIES_PER_MINUTE` | `6` | Stories per minute the background summary worker may enrich (0–30). Each costs 2 requests to news.google.com and 1 to the publisher. `0` disables enrichment entirely, and with it the warmer. |

With summaries on, a warmer thread also asks for today's pick every 15 minutes (and tomorrow's after 9pm ET), so the day's story and its carousel runners-up are summarized before the first visitor, and keeps up to 5 summarized random picks ready for the Random button. It uses the same news and summary budgets as everything else.

The data dir defaults to the repo's `data/` folder, not the current working directory, so a launchd job with `cwd=/` still finds it. It is created with mode 0700 and is gitignored.

Share/copy counters:

```sh
sqlite3 data/florida-man.sqlite3 'select * from counters order by name'
```

## Running it as a static site (GitHub Pages)

The same pipeline also runs as a batch job that writes a site needing no server:

```sh
python3 -m fm.build --out site               # today only
python3 -m fm.build --out site --backfill 30 # plus the previous 30 days
```

It picks today's story (and any backfilled days), fetches publisher summaries synchronously within the usual rate limits (`--max-summaries`, `--summary-minutes` cap a run), adds each day to `archive/YYYY/MM-DD.json` (committed), and writes `site/`: the `static/` files plus the JSON described in [API.md](../API.md#static-build-github-pages). Days already in the archive keep their pick. Rerunning only updates summaries. If news.google.com blocks the runner, the archive is left as is and the site is built from it. The command exits nonzero only when there is nothing at all to publish. `FM_TZ`, `FM_HOME_YEARS`, `FM_FETCHES_PER_MINUTE` and `FM_SUMMARIES_PER_MINUTE` apply as for the server. Preview with `python3 -m http.server -d site`.

`.github/workflows/daily.yml` runs this every day at 14:00 UTC, and on demand (Actions → Daily → Run workflow, with an optional backfill). It tests, builds, commits `archive/` changes to `main` as `github-actions[bot]`, and deploys `site/` to Pages. It needs no secrets. In the repo settings, set Pages → Source to "GitHub Actions".

## Test

```sh
python3 -m unittest discover -s tests -t .
```

## How it works

- **Summaries:** Google News RSS has no descriptions, so a background thread (one, started by `python3 -m fm`) fills them in. For each story that leaves the API without a stored result, it makes two requests to `news.google.com` to turn the Google link into the publisher's article URL (a GET of the article page with a browser User-Agent, which Google requires, and a POST to its `batchexecute` endpoint). It then makes one GET to that **publisher URL** and reads only the page's `<head>` share blurb (`og:description`, then `twitter:description`, then `description`). It keeps the blurb's first sentence when it is actually about the story. No article body and no images are used. Results (including failures) live in the `summaries` table and are overlaid on every response at read time. `summaryPending` tells the frontend when to re-poll. When Google redirects (its "sorry" page), rate-limits or answers oddly, the decoder backs off for 15 minutes.

- **Daily pick:** for today's date D (in Florida time), query Google News RSS for `"Florida man" after:<D-1> before:<D+1>` in each of the previous 4 years. Normalize and score the results, keep the ones published on D (falling back to ±1 day), and pick the highest score. Ties go to the lowest stable hash. The pick is stored under `florida-man:{YYYY-MM-DD}` permanently, so the same date always returns the same story and restarts don't refetch. An empty day is stored for 1 hour, and upstream failures are not stored.
- **Search:** `"Florida man" "<Town>" after:… before:…`, either for one exact date or for the last 3 occurrences of the month/day. Headlines must contain "Florida Man" or "<Town> man". Results are cached for 1 hour, and stale results are served when the upstream is down or rate-limited.
- **Score:** keyword categories (animals, absurd objects, odd behavior, legal novelty, Florida flavor), a "weapon that isn't" detector ("attacked … with a pool noodle"), and a heavy penalty for grim stories (deaths, children, sexual crimes), so tragedies don't become the punchline. The score isn't shown on the page; it only decides the pick.
- **Security:**
  - RSS and link-decode fetches go only to `https://news.google.com`, with no redirects, a 10s timeout, a 2 MB cap and a per-minute budget.
  - Publisher fetches go to attacker-influenceable URLs, so they are https on port 443 only, to hostnames only (no IP literals or local names). Every address the name resolves to must be globally routable, which rules out loopback, RFC 1918, link-local, CGNAT/tailnet `100.64/10`, ULA, multicast, and mapped/NAT64/6to4 forms of those. The connection is pinned to the checked address, with SNI and certificate verification on the hostname, so there is no second DNS lookup and no rebinding. They follow at most 3 redirects, each re-checked the same way, and use no proxies, cookies or auth. They need `text/html`, read at most 512 KB, stop at `</head>`, and have an 8s socket timeout. Logs carry only host, outcome and error class.
  - Feeds that contain a DTD are refused.
  - All feed text is stripped of HTML and control/bidi characters, and only `http(s)` links survive.
  - Every query parameter is validated, and the town must be on the bundled list.
  - Responses carry a strict CSP (self only, no inline script).
  - Static serving allows only listed file types from `static/`, with no dotfiles or listings. Error responses are JSON, without tracebacks.
