# Florida Man of the Day: API contract for the frontend

This file is the whole contract. You do not need the backend source to build against it.

## The setup

- The backend is a small Python server. It serves **your static files** and a **JSON API** from the same origin. The browser only ever talks to that origin.
- Your deliverable is a set of static files placed in the repo's `static/` directory: `index.html` plus any `.js`, `.css`, `.svg`, `.png`, `.jpg`, `.webp`, `.ico`, `.woff2`, `.json`, `.webmanifest` or `.txt` files. There is no build step and no Node. Write plain HTML/CSS/vanilla JS.
- Static URL mapping: `GET /foo.js` serves `static/foo.js`, and subfolders work too (`/img/x.svg` serves `static/img/x.svg`). Other file types, dotfiles and directory URLs return 404, and there is no directory listing.
- `GET /` and `GET /search` (with any query string) both serve `static/index.html`. It is a single-page app: read `location.search` to decide what to show.
- The live server is reached over HTTPS at a private hostname. The [static build](#static-build-github-pages) is served from a subpath (`https://<user>.github.io/<repo>/`). Never hard-code a host, and never start a URL with `/`: use relative URLs (`app.js`, `api/today`, `?story=…`) and resolve against `location.href`.

## Hard rules (the browser will enforce some of these)

### Content-Security-Policy

Every response carries this header:

```
default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self';
img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none';
form-action 'self'; frame-ancestors 'none'
```

What this means for you:

- **No inline script.** `<script>…</script>` with a body does nothing, and neither do `onclick="…"` attributes or `javascript:` URLs. Load JS with `<script src="app.js" defer></script>` and attach handlers with `addEventListener`.
- **No inline styles.** `<style>` blocks and `style="…"` attributes are blocked. Put all CSS in `.css` files. Setting `el.style.foo = …` from JS *is* allowed, and so is toggling classes.
- **No CDNs** for JS, CSS or fonts. Fonts are self-hosted `.woff2` files in `static/fonts/`.
- Images may come from `static/` or be `data:` URIs. Remote images are blocked (see `imageUrl` below).
- `fetch()` may only call this same origin.
- The page cannot be framed.

Other headers: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cross-Origin-Opener-Policy: same-origin`.

### Remote text must be rendered with `textContent`

Every string in a story (`headline`, `summary`, `town`, `sourceName`, …) comes from the open web. A `"publisher"` summary is text the publisher's own page supplied. The backend strips HTML and validates URLs, but treat it as untrusted anyway:

- Build the DOM with `document.createElement` and set text with `node.textContent = value`.
- **Never** pass API data to `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, or template strings that are later parsed as HTML.
- `innerHTML` is acceptable only for static markup you wrote yourself that contains no API data.

### Source links

- Render `sourceUrl` as `<a href=… target="_blank" rel="noopener noreferrer">`, using `sourceName` as the link text, set via `textContent`. It may be a Google News link or the publisher's own article URL (see the Story object), and the same story can switch from one to the other between requests. Always take it from the latest response.
- The backend only emits `http:`/`https:` URLs. Re-check in JS anyway (`new URL(u).protocol` is `http:` or `https:`) and render plain text if the check fails.
- Always show the attribution, i.e. the source name plus the link. That is a product requirement.

## Endpoints

All API responses are JSON (`application/json; charset=utf-8`). All parameters are query-string parameters. Unknown parameters are ignored.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/today` | Today's Florida Man of the Day (homepage) |
| GET | `/api/day?date=YYYY-MM-DD` | The pick for a specific past day (permalink for a homepage) |
| GET | `/api/search?town=…&date=…` | Search by date, in one town or all of Florida |
| GET | `/api/random` | A random story from a random past day (the 🎲 button) |
| GET | `/api/story?id=…` | A single story by id (permalink for a card) |
| GET | `/api/towns` | List of valid town names (for autocomplete) |
| POST | `/api/track` | Record a share/copy interaction |
| GET | `/api/health` | `{"ok": true}` |

### The Story object

The homepage, search, and story endpoints all return stories in this shape:

| Field | Type | Notes |
|---|---|---|
| `id` | string | 16 lowercase hex characters. Stable for this article in this context. |
| `headline` | string | Plain text, at most 300 chars. "Florida man" is normalized to "Florida Man". Show it big. |
| `summary` | string | One sentence, plain text, at most about 240 chars (a longer sentence is cut at a word and ends in `…`). |
| `summarySource` | `"publisher"` \| `"feed"` \| `"generated"` | Where `summary` came from. `"publisher"`: the first sentence of the publisher's own link-preview blurb (its `og:description` or similar), fetched by the backend. `"feed"`: text from the news feed (rare). `"generated"`: nothing usable was found (yet), so it is a factual line like *"WFLA reported this one on October 14, 2025. Full story at the source."* You may style `"generated"` differently. Always show the source attribution next to a summary. A story can change from `"generated"` to `"publisher"` between requests (see [Summaries arrive later](#summaries-arrive-later)). |
| `summaryPending` | boolean | `true` means the backend is fetching a publisher summary for this story right now, and re-polling `/api/story?id=` in a few seconds may return a better `summary`. `false` means what you have is final for now; don't poll. |
| `imageUrl` | string \| null | **Always `null` today.** Licensing is unclear, so no images are shown. Keep a slot in the card: when `imageUrl` is a string, render an `<img alt="">`. When it is null, show a non-photo treatment (illustration, pattern, a big emoji, or nothing). If the backend ever starts sending images, the CSP `img-src` will be widened to match. |
| `town` | string \| null | Canonical Florida town name. Search results always carry the searched town. Homepage picks carry a town only if one was named in the headline, so handle `null` (e.g. show "Somewhere, Florida"). |
| `incidentDate` | string | `YYYY-MM-DD`. This is really the **publication date of the report** in Florida (Eastern) time, which is usually the incident day or the day after. It may differ from the requested date by ±1 day when nothing matched exactly. |
| `sourceName` | string | Publisher name, e.g. `"WFLA"`. Plain text. |
| `sourceSite` | string \| null | Publisher home page (http/https), if known. Optional to display. |
| `sourceUrl` | string | Link to the article. Once the backend has resolved it, this is the **publisher's article URL** (e.g. `https://www.wfla.com/news/…`). Until then it is a Google News URL (`https://news.google.com/rss/articles/…`) that redirects to the publisher. Either way it is http/https. It is usually https, but an old article can be plain `http:`. |
| `score` | integer | 1–100. **Not displayed** (owner's call, 2026-10-14). It is still returned and still used internally to pick the day's story. Don't render it. |
| `scoreMax` | integer | Always `100`. **Not displayed** (owner's call, 2026-10-14). It is still returned and still used internally to pick the day's story. |
| `scoreLabel` | string | Always `"subjective"`. **Not displayed** (owner's call, 2026-10-14). It is still returned and still used internally to pick the day's story. |
| `scoreReasons` | string[] | What moved the score, e.g. `"animal: alligator"`. **Not displayed** (owner's call, 2026-10-14). It is still returned and still used internally to pick the day's story. |
| `shareUrl` | string | Relative permalink to this story, e.g. `/?story=5c53a2e71bf937a4` (`?story=…` in the static build). Build the absolute URL with `new URL(shareUrl, location.href).href`. |

### Summaries arrive later

The news feed has headlines but almost never a summary. So the backend looks up each story's publisher page **in the background** and reads that page's own short share blurb. It never fetches during your request, and it never copies article text. It works through stories slowly (about 6 a minute by default), and does the homepage story and any story opened via `/api/story` first. Results are kept, so a story only needs this once.

As a result, the first response for a story usually has `summarySource: "generated"`, `summaryPending: true`, and a Google News `sourceUrl`. A later response for the same story has the publisher sentence (`summarySource: "publisher"`) and the publisher's URL. Some stories never get one, because the publisher has no usable blurb or the lookup failed. Those settle with `summaryPending: false` and keep the generated line.

**Re-poll guidance (main card only):**

- When the story in the main card (from `/api/today`, `/api/day`, `/api/random` or `/api/story`) has `summaryPending: true`, call `GET /api/story?id=<story.id>` once after about **4 s**. If that still says `summaryPending: true`, try once more at about **10 s** after the first render. Then stop.
- Stop early as soon as a response has `summaryPending: false`, and update the card's summary **and** source link from it.
- Cancel the pending re-polls if the user navigates or rolls a new story.
- Don't re-poll search result cards. Their summaries show up the next time the search is loaded.
- Any response that contains a story with `summaryPending: true` is sent with `Cache-Control: no-store`, so these re-polls are never answered from the browser cache.

---

### GET `/api/today`

No parameters. "Today" is the current calendar date in Florida (America/New_York). The backend searches the same month/day in the previous 4 years and picks the highest-scoring story. The pick is stored permanently, so every request on that date returns the same story, even across restarts.

**200 example**

```json
{
  "date": "2026-10-14",
  "cacheKey": "florida-man:2026-10-14",
  "story": {
    "id": "5c53a2e71bf937a4",
    "headline": "Florida Man arrested after hitting deputy with a chicken nugget at Waffle House",
    "summary": "A Florida Man was arrested after deputies said he hit one of them with a chicken nugget during an argument at a Waffle House.",
    "summarySource": "publisher",
    "summaryPending": false,
    "imageUrl": null,
    "town": null,
    "incidentDate": "2025-10-14",
    "sourceName": "WFLA",
    "sourceSite": "https://www.wfla.com",
    "sourceUrl": "https://www.wfla.com/news/florida/florida-man-chicken-nugget-deputy-waffle-house/",
    "score": 92,
    "scoreMax": 100,
    "scoreLabel": "subjective",
    "scoreReasons": [
      "object: chicken nuggets",
      "legal: arrest",
      "florida: Waffle House",
      "weapon that isn't: chicken nugget"
    ],
    "shareUrl": "/?story=5c53a2e71bf937a4"
  },
  "more": [ { "id": "…", "headline": "…", "summary": "…", "…": "same Story shape" } ],
  "empty": false,
  "message": null,
  "yearsSearched": [2025, 2024, 2023, 2022],
  "shareUrl": "/?date=2026-10-14"
}
```

`more` is the day's runners-up for a carousel: up to 9 more Story objects from the same searches, best first, never including `story`. It is always present (an empty array when there's nothing else, or on an empty day). `story` stays the Florida Man of the Day; put it first in the carousel. Each runner-up has its own `shareUrl` and works with `/api/story?id=`. Runners-up get summaries in the background like any story; only re-poll the one currently on screen, and only while it says `summaryPending: true`.

The top-level `shareUrl` (`/?date=…`) links to "the Florida Man of the Day for that date". The story's own `shareUrl` links to the single story. Either one is fine for the homepage share button.

**Empty state (still HTTP 200)**

```json
{
  "date": "2026-10-14",
  "cacheKey": "florida-man:2026-10-14",
  "story": null,
  "more": [],
  "empty": true,
  "message": "Florida Man was, surprisingly, well-behaved today.",
  "yearsSearched": [2025, 2024, 2023, 2022],
  "shareUrl": "/?date=2026-10-14"
}
```

When `empty` is `true`, show `message`. It is always exactly **"Florida Man was, surprisingly, well-behaved today."**, and you may hard-code that string as a fallback.

The first request of a new day may take a few seconds while the backend asks the news source. Cached days return in milliseconds. Show a loading state.

### GET `/api/day?date=YYYY-MM-DD`

The same response shape as `/api/today`, for another day. `date` must be a real date between `2004-01-01` and today. Use it to render share links of the form `/?date=YYYY-MM-DD`.

### GET `/api/search?town=…&date=…`

| Param | Required | Format |
|---|---|---|
| `town` | no | Leave it out (or blank) to search all of Florida; then `query.town` is `null`. Otherwise a Florida town from `/api/towns`. Matching ignores case and punctuation and treats "Saint" like "St.". A trailing ", FL" is allowed. A few aliases work (`St Pete`, `Jax`, `Ft Myers`). The response returns the canonical name in `query.town`. |
| `date` | yes | `MM-DD` (month/day in any year: searches the most recent 3 occurrences up to today) **or** `YYYY-MM-DD` (that exact day, between 2004-01-01 and today). ASCII digits, zero-padded. |

Results are sorted by `score` (highest first; the score itself is not displayed), up to 20. They are cached for 1 hour. `shareUrl` is the permalink for the search: `/?town=…&date=…`, or `/?scope=statewide&date=…` when no town was given.

A birthday search is just a full date, usually statewide: `/api/search?date=1990-05-17`. Full dates back to 1900-01-01 are accepted. The news index has nothing before 2004 and is thin until about 2012, so when the exact day is empty the backend **falls back** to that month/day in recent years:

- `query.fallback` is `"month-day"` (otherwise `null`); `query.date` stays the date that was asked for, and `query.datesSearched` lists the days actually searched.
- `message` is set even though `empty` is false, e.g. "Nothing on that exact day. Here's Florida Man on May 17 in other years." Show it above the results.
- `shareUrl` still points at the original date.

If the fallback is empty too, you get the normal empty shape.

**200 example**

```json
{
  "query": {
    "town": "Tampa",
    "date": "2024-10-14",
    "mode": "full",
    "datesSearched": ["2024-10-14"]
  },
  "results": [
    {
      "id": "e02c00a8468097ba",
      "headline": "Tampa man arrested for stealing a golf cart while dressed as a pirate",
      "summary": "FOX 13 Tampa Bay reported this one on October 14, 2024. Full story at the source.",
      "summarySource": "generated",
      "summaryPending": true,
      "imageUrl": null,
      "town": "Tampa",
      "incidentDate": "2024-10-14",
      "sourceName": "FOX 13 Tampa Bay",
      "sourceSite": "https://www.fox13news.com",
      "sourceUrl": "https://news.google.com/rss/articles/CBMiTTT001?oc=5",
      "score": 69,
      "scoreMax": 100,
      "scoreLabel": "subjective",
      "scoreReasons": ["object: golf cart", "behavior: costume", "behavior: theft", "legal: arrest"],
      "shareUrl": "/?story=e02c00a8468097ba"
    }
  ],
  "count": 1,
  "empty": false,
  "message": null,
  "stale": false,
  "partial": false,
  "shareUrl": "/?town=Tampa&date=2024-10-14"
}
```

- `query.mode` is `"full"` for `YYYY-MM-DD` or `"month-day"` for `MM-DD`. In `month-day` mode, `datesSearched` lists the actual days searched, e.g. `["2026-10-14","2025-10-14","2024-10-14"]`.
- `stale: true` means the news source was unavailable or busy, so you are seeing an older cached copy. You may show a small "might be a bit old" note.
- `partial: true` means some of the dates couldn't be fetched, so results may be incomplete.

**Empty state (HTTP 200)**

```json
{
  "query": {"town": "Yankeetown", "date": "01-02", "mode": "month-day",
            "datesSearched": ["2026-01-02", "2025-01-02", "2024-01-02"]},
  "results": [],
  "count": 0,
  "empty": true,
  "message": "Florida Man was, surprisingly, well-behaved today.",
  "stale": false,
  "partial": false,
  "shareUrl": "/?town=Yankeetown&date=01-02"
}
```

### GET `/api/story?id=…`

`id` is 16 lowercase hex characters (from a story's `id` or `shareUrl`).

**200:** `{"story": { …Story… }}`

This is also the endpoint to re-poll while `summaryPending` is `true` (see [Summaries arrive later](#summaries-arrive-later)). Asking for a story also moves its summary lookup to the front of the backend's queue. The response is `Cache-Control: no-store` while the summary is pending, and `public, max-age=3600` after that.

**404:** `story_not_found` means the story has dropped out of the cache. Offer the homepage or search instead.

### GET `/api/towns`

**200:** `{"towns": ["Alachua", "Alford", "Altamonte Springs", …]}`. This is about 475 canonical names, sorted. It covers Florida's incorporated municipalities plus well-known unincorporated communities (Brandon, Port Charlotte, The Villages, …). Use it for a `<datalist>` or autocomplete. It is cacheable for a day.

### GET `/api/random`

Picks a random day between 2013-01-01 and yesterday, runs a statewide search for it, and returns one of its stories at random, skipping grim ones. If the day has none left it tries another, up to 3 days, then returns the empty shape. Never cached: every call is a new roll.

The server keeps a few rolls ready in the background with their summaries already fetched, so most rolls come back instantly with `summarySource: "publisher"` and nothing to re-poll. When that stash is empty the roll happens live and behaves like any other story (it may say `summaryPending: true`).

**200**
```json
{"date": "2017-03-09", "story": { …Story… }, "empty": false, "message": null,
 "shareUrl": "/?story=1f0c9a4e2b7d6a35"}
```

**200, nothing found:** `{"date": null, "story": null, "empty": true, "message": "Florida Man was, surprisingly, well-behaved today.", "shareUrl": null}`

It shares the backend's news budget with search, so a burst of rolls can return `503 rate_limited`. Disable the button while a roll is in flight and show "rolling…".

### POST `/api/track`

Counts share and copy interactions locally. No third parties are involved, and no personal data is stored. Call it fire-and-forget, and ignore failures.

- Header `Content-Type: application/json` is **required** (otherwise 415).
- Body (max 1 KB): `{"event": "share" | "copy", "storyId": "<16-hex id>"}`. `storyId` is optional; leave it out when sharing a search.
- Use `"share"` after `navigator.share()` resolves successfully. Use `"copy"` after the copy-link fallback.
- **204** with an empty body on success. **400** with `invalid_event` / `invalid_body` / `invalid_story_id` on bad input.

```js
fetch("api/track", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ event: "copy", storyId: story.id }),
}).catch(() => {});
```

## Errors

Every error has a non-2xx status and this shape:

```json
{
  "error": {
    "code": "invalid_town",
    "message": "That isn't a Florida town we know. Try a city or town name, e.g. Tampa.",
    "field": "town",
    "suggestions": ["Tampa", "Tamarac"]
  }
}
```

- `message` is human-readable and safe to show to users (via `textContent`).
- `field` (optional) names the bad parameter, so you can highlight the input.
- `suggestions` appears only on `invalid_town`: up to 3 "did you mean" names, possibly an empty list.
- `retryAfter` (seconds) appears only on `rate_limited`. The same value is sent in a `Retry-After` header.

| Status | `code` | When |
|---|---|---|
| 400 | `invalid_town` | town given but not in the list (an absent town means statewide) |
| 400 | `missing_date`, `invalid_date`, `date_out_of_range` | bad/absent date, nonexistent day, future, or before 2004 (1900 for `/api/search`) |
| 400 | `invalid_story_id` | `id` isn't 16 hex chars |
| 400 | `invalid_body`, `invalid_event`, `invalid_query` | bad track body / too many query params |
| 404 | `story_not_found`, `not_found` | unknown story / unknown URL |
| 405 | `method_not_allowed` | wrong HTTP method |
| 413 / 415 | `body_too_large` / `unsupported_media_type` | track body problems |
| 502 | `upstream_error` | the news source failed and nothing is cached. Show the error state with a retry button. |
| 503 | `rate_limited` | the backend is protecting the news source and has nothing cached for this query. Retry after `retryAfter` seconds. |
| 500 | `internal_error` | bug; show a generic error |

Errors are **not** empty states. An empty result is a 200 with `empty: true`.

## Front-end routes (URL scheme for sharing)

Your `index.html` should handle these. They are the URLs the API puts in `shareUrl`:

| URL | Show | Data from |
|---|---|---|
| `/` | Today's Florida Man of the Day | `GET /api/today` |
| `/?date=YYYY-MM-DD` | That day's Florida Man of the Day | `GET /api/day?date=…` |
| `/?story=<id>` | A single story card | `GET /api/story?id=…` |
| `/?town=<Town>&date=<MM-DD or YYYY-MM-DD>` | Search results, form pre-filled | `GET /api/search?…` |
| `/?scope=statewide&date=<MM-DD or YYYY-MM-DD>` | Statewide search results, town box empty | `GET /api/search?date=…` |
| `/search` (optional) | Same page; you may use it for the search view | n/a |

## Sharing

1. Build the absolute URL: `new URL(shareUrl, location.href).href`. (Resolving against `location.href`, not `location.origin`, keeps the static site's subpath.)
2. If `navigator.share` exists, call `navigator.share({ title: headline, url })`. On success, track `"share"`. If it rejects with `AbortError`, the user cancelled, so do nothing.
3. Otherwise, call `navigator.clipboard.writeText(url)`. On success, show "Link copied" and track `"copy"`.
4. If the clipboard API is unavailable or throws, show the URL in a read-only, pre-selected `<input>` so the user can copy it by hand, and still track `"copy"`.

## UX requirements (from the PRD)

- Homepage: the title "Florida Man of the Day"; a giant headline; an image slot (null for now); town and incident date; one-line summary (updated in place when the publisher summary arrives); source attribution with a link that opens in a new tab; share button; link to search. **No score** (owner's call, 2026-10-14). The `score*` fields are still in the API, but don't display them.
- Search: town and date inputs (accept `MM-DD` or `YYYY-MM-DD`; a date picker that produces `YYYY-MM-DD` is fine). Include loading, empty and error states.
- The empty-state text is exactly: **Florida Man was, surprisingly, well-behaved today.**
- Responsive and usable at 390px wide.

## Static build (GitHub Pages)

`python3 -m fm.build --out site` writes a copy of the site that needs no server (see the README). `app.js` runs in **static mode** when `index.html` carries `<meta name="fm-mode" content="static">`, which only the build adds. In static mode it reads the JSON files below instead of `/api/*`, makes no `/api/track` call, and never re-polls (every summary is final).

Pages can't send headers, so the built `index.html` carries the CSP above as `<meta http-equiv="Content-Security-Policy">`, minus `frame-ancestors` (browsers ignore that directive in a meta tag), plus `<meta name="referrer" content="no-referrer">`. The other headers listed above aren't available on Pages.

All paths are relative to the page, and every link and `shareUrl` in the files is a query string (`?story=<id>`, `?date=YYYY-MM-DD`), so it resolves under the project subpath.

### Files

| Path | Content |
|---|---|
| `data/today.json` | The `/api/today` shape for the newest collected day. If today couldn't be collected, this is an earlier day, and its `date` says which. |
| `data/days/YYYY-MM-DD.json` | The `/api/day` shape for one collected day: `date`, `story`, `more`, `empty`, `message`, `yearsSearched`, `shareUrl`. No file means the day was never collected. |
| `data/index.json` | Everything search, random and `?story=` links need (below). |
| `data/towns.json` | `{"towns": [...], "aliases": {"st pete": "St. Petersburg", ...}}`: the `/api/towns` list, plus aliases keyed by normalized name (lowercase, punctuation dropped, "saint" → "st"). |

Stories have the Story shape, with `summaryPending` always `false`, the publisher summary and link already applied, and `shareUrl` `?story=<id>`.

### `data/index.json`

```json
{
  "version": 1,
  "latest": "2026-10-14",
  "days": {
    "2026-10-14": [["5c53a2e71bf937a4", "2025-10-14", null, 92, 1], ["e02c00a8468097ba", "2024-10-14", "Tampa", 69, 1]],
    "2026-10-13": []
  }
}
```

- `days` has one key per collected day (the day of the pick, i.e. the file in `data/days/`). Each entry is `[id, incidentDate, town, score, randomOk]` for a story in that day's file. `randomOk` is `0` for a grim story (see `score.is_grim`): it stays in its day, but the Random button skips it.
- A story found on several days is listed once, under the newest. An empty list means the day was collected and nothing was found (or every story was already listed).
- `latest` is the newest day with a story, or `null`.

How the frontend uses it:

- **`?story=<id>`**: find the id, then load that day's file.
- **Random**: a uniformly random entry with `randomOk` set, then its day file.
- **Search** (`?town=…&date=…`, `?scope=statewide&date=…`): filter entries by town (if given; the name must be in `towns.json`), then by **`incidentDate`**: the exact date; otherwise the same month/day in other years (with the server's fallback message); otherwise the nearest collected date ("Nothing collected for that day yet. The closest we have is …"). For `MM-DD`, the same month/day in any year, otherwise the nearest month/day. Hits are sorted by score, capped at 20, and loaded from their day files. Only collected days can be found. Towns come from headlines only, so a town filter finds fewer stories than the server's town search does.

Size: about 45 bytes per story. At 10 stories a day that is roughly 160 KB a year (about 60 KB gzipped, as Pages serves it) and 1.6 MB after 10 years (about 600 KB gzipped). Only search, random and story links load it; the front page doesn't. Day files are 3–8 KB each.

### Archive

The build also keeps `archive/YYYY/MM-DD.json` in the repo, one file per collected day: the stored day payload with raw stories (Google News links, generated summaries), plus `summaries`, the publisher summary results keyed by article (`status`, `publisherUrl`, `summary`, `fetched`, `retryAt`). Keys are sorted and indented, so a day's diff shows only what changed. The site's JSON is generated from the archive on every run. The archive is the source of truth, and `site/` is never committed.
