(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const carousel = $("carousel");
  const status = $("status");
  const message = $("message");
  const position = $("position");
  const heading = $("results-title");
  const randomButton = $("random-button");
  const shareButton = $("share");
  const shareLabel = $("share-label");
  const previous = $("previous");
  const next = $("next");
  const townInput = $("town");
  const dateInput = $("date");
  const townToggle = $("town-toggle");
  const townRow = $("town-row");

  // Our own suggestion list, not a <datalist>: iOS turns a datalist into a
  // dropdown chevron that reads as a select box.
  const townList = $("town-list");
  const MAX_SUGGESTIONS = 6;
  let towns = [];
  let townsLoaded = false;
  let activeSuggestion = -1;

  async function loadTowns() {
    if (townsLoaded) return;
    townsLoaded = true;
    try {
      const data = await api.towns();
      towns = (Array.isArray(data.towns) ? data.towns : []).filter((name) => typeof name === "string");
      if (document.activeElement === townInput) suggestTowns();
    } catch {
      townsLoaded = false;
    }
  }

  function matchTowns(query) {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    const starts = towns.filter((name) => name.toLowerCase().startsWith(q));
    const contains = towns.filter((name) => !name.toLowerCase().startsWith(q) && name.toLowerCase().includes(q));
    return starts.concat(contains).slice(0, MAX_SUGGESTIONS);
  }

  function closeSuggestions() {
    townList.hidden = true;
    townList.replaceChildren();
    townInput.setAttribute("aria-expanded", "false");
    townInput.removeAttribute("aria-activedescendant");
    activeSuggestion = -1;
  }

  function suggestTowns() {
    const matches = matchTowns(townInput.value);
    const exact = matches.length === 1 && matches[0].toLowerCase() === townInput.value.trim().toLowerCase();
    if (!matches.length || exact) {
      closeSuggestions();
      return;
    }
    townList.replaceChildren(...matches.map((name, i) => {
      const item = document.createElement("li");
      item.id = `town-option-${i}`;
      item.setAttribute("role", "option");
      item.textContent = name;
      // pointerdown beats the input's blur, which would close the list first.
      item.addEventListener("pointerdown", (event) => {
        event.preventDefault();
        pickTown(name);
      });
      return item;
    }));
    activeSuggestion = -1;
    townList.hidden = false;
    townInput.setAttribute("aria-expanded", "true");
    townInput.removeAttribute("aria-activedescendant");
  }

  function pickTown(name) {
    townInput.value = name;
    closeSuggestions();
  }

  function highlightSuggestion(index) {
    const items = [...townList.children];
    if (!items.length) return;
    activeSuggestion = (index + items.length) % items.length;
    items.forEach((item, i) => item.setAttribute("aria-selected", String(i === activeSuggestion)));
    townInput.setAttribute("aria-activedescendant", items[activeSuggestion].id);
  }

  townInput.addEventListener("input", suggestTowns);
  townInput.addEventListener("blur", closeSuggestions);
  townInput.addEventListener("keydown", (event) => {
    if (townList.hidden) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      highlightSuggestion(activeSuggestion + (event.key === "ArrowDown" ? 1 : -1));
    } else if (event.key === "Enter" && activeSuggestion >= 0) {
      event.preventDefault();
      pickTown(townList.children[activeSuggestion].textContent);
    } else if (event.key === "Escape") {
      closeSuggestions();
    }
  });

  // The town box floats over the page, so opening it never pushes the stories
  // down. Closed, the button names the town in use.
  const townLabel = townToggle.querySelector("span");

  function showTownFilter(open) {
    townRow.hidden = !open;
    townToggle.setAttribute("aria-expanded", String(open));
    townLabel.textContent = townInput.value.trim() || "Town";
    townToggle.classList.toggle("is-set", Boolean(townInput.value.trim()));
    if (open) loadTowns();
    else closeSuggestions();
  }

  document.addEventListener("pointerdown", (event) => {
    if (!townRow.hidden && !townRow.contains(event.target) && !townToggle.contains(event.target)) {
      showTownFilter(false);
    }
  });
  townRow.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && townList.hidden) {
      showTownFilter(false);
      townToggle.focus();
    }
  });

  const EMPTY_TEXT = "Florida Man was, surprisingly, well-behaved today.";
  const POLL_DELAYS = [4000, 10000];
  const HEADINGS = {
    day: "Today’s Florida Man",
    date: "Florida Man, that day",
    search: "Search results",
    random: "A random Florida Man",
    story: "Florida Man",
  };

  let stories = [];
  let current = 0;
  let payloadShareUrl = null;
  let pollTimers = [];
  let loadToken = 0;

  function setStatus(text) {
    status.textContent = text || "";
  }

  function validUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "http:" || url.protocol === "https:";
    } catch {
      return false;
    }
  }

  async function getJson(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const info = data && typeof data.error === "object" && data.error ? data.error : {};
      const error = new Error(typeof info.message === "string" ? info.message : "Request failed.");
      error.code = info.code || response.status;
      throw error;
    }
    return data;
  }

  // -- data: the live server's API, or the JSON files of the static build ----

  const STATIC = (document.querySelector('meta[name="fm-mode"]') || {}).content === "static";
  const STORY_ID = /^[0-9a-f]{16}$/;
  const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
  const MONTH_DAY = /^\d{2}-\d{2}$/;
  const MAX_RESULTS = 20;

  const serverApi = {
    today: () => getJson("api/today"),
    day: (date) => getJson(`api/day?date=${encodeURIComponent(date)}`),
    story: (id) => getJson(`api/story?id=${encodeURIComponent(id)}`),
    random: () => getJson("api/random"),
    towns: () => getJson("api/towns"),
    search(town, date) {
      const query = new URLSearchParams({ date });
      if (town) query.set("town", town);
      return getJson(`api/search?${query}`);
    },
  };

  function notFound(text) {
    const error = new Error(text);
    error.code = 404;
    return error;
  }

  function text(value) {
    return typeof value === "string" ? value : "";
  }

  // Everything read from the JSON files goes through here: ids and dates must
  // look right, text must be strings, links must be http(s).
  function cleanStory(raw) {
    if (!raw || typeof raw !== "object" || !STORY_ID.test(raw.id)) return null;
    return {
      id: raw.id,
      headline: text(raw.headline),
      summary: text(raw.summary),
      summarySource: ["publisher", "feed", "generated"].includes(raw.summarySource)
        ? raw.summarySource : "generated",
      summaryPending: false,
      imageUrl: null,
      town: text(raw.town) || null,
      incidentDate: ISO_DATE.test(raw.incidentDate) ? raw.incidentDate : "",
      sourceName: text(raw.sourceName),
      sourceUrl: validUrl(raw.sourceUrl) ? raw.sourceUrl : "",
      shareUrl: `?story=${raw.id}`,
    };
  }

  function cleanDay(raw, date) {
    const data = raw && typeof raw === "object" ? raw : {};
    const day = ISO_DATE.test(data.date) ? data.date : date;
    const story = cleanStory(data.story);
    const more = (Array.isArray(data.more) ? data.more : []).map(cleanStory).filter(Boolean);
    return {
      date: day,
      story,
      more: story ? more : [],
      empty: !story,
      message: story ? null : EMPTY_TEXT,
      shareUrl: day ? `?date=${day}` : null,
    };
  }

  function townKey(name) {
    return String(name).toLowerCase().replace(/-/g, " ").replace(/[^a-z0-9 ]/g, "")
      .replace(/\bsaint\b/g, "st").replace(/\s+/g, " ").trim();
  }

  function daysBetween(a, b) {
    return Math.round((Date.parse(`${a}T00:00:00Z`) - Date.parse(`${b}T00:00:00Z`)) / 86400000);
  }

  function monthDayLabel(monthDay) {
    const [m, d] = monthDay.split("-").map(Number);
    return new Date(Date.UTC(2000, m - 1, d)).toLocaleDateString(undefined, {
      month: "long", day: "numeric", timeZone: "UTC",
    });
  }

  const staticApi = (() => {
    const files = new Map();

    function file(path) {
      if (!files.has(path)) {
        const request = getJson(path).catch((error) => {
          files.delete(path);
          throw error;
        });
        files.set(path, request);
      }
      return files.get(path);
    }

    // [{id, date, incidentDate, town, score}], one per story, newest day first.
    async function index() {
      const data = await file("data/index.json");
      const days = data && typeof data.days === "object" && data.days ? data.days : {};
      const entries = [];
      Object.keys(days).filter((day) => ISO_DATE.test(day)).sort().reverse().forEach((day) => {
        (Array.isArray(days[day]) ? days[day] : []).forEach((row) => {
          if (!Array.isArray(row) || !STORY_ID.test(row[0]) || !ISO_DATE.test(row[1])) return;
          entries.push({
            id: row[0],
            date: day,
            incidentDate: row[1],
            town: text(row[2]) || null,
            score: Number.isFinite(row[3]) ? row[3] : 0,
            randomOk: row[4] !== 0,
          });
        });
      });
      return entries;
    }

    async function day(date) {
      if (!ISO_DATE.test(date || "")) throw notFound("Use a date like 2024-10-14.");
      try {
        return cleanDay(await file(`data/days/${date}.json`), date);
      } catch (error) {
        if (error.code !== 404) throw error;
        return {
          date, story: null, more: [], empty: true, shareUrl: `?date=${date}`,
          message: "That day isn’t in the archive yet. It grows by one day every morning.",
        };
      }
    }

    async function storyFrom(entry) {
      const payload = await day(entry.date);
      return [payload.story].concat(payload.more).find((s) => s && s.id === entry.id) || null;
    }

    async function towns() {
      const data = await file("data/towns.json");
      return {
        towns: Array.isArray(data.towns) ? data.towns.filter((t) => typeof t === "string") : [],
        aliases: data.aliases && typeof data.aliases === "object" ? data.aliases : {},
      };
    }

    async function canonicalTown(name) {
      let key = townKey(name);
      if (key.endsWith(" fl")) key = key.slice(0, -3).trim();
      else if (key.endsWith(" florida")) key = key.slice(0, -8).trim();
      const list = await towns();
      const hit = list.towns.find((t) => townKey(t) === key);
      if (hit) return hit;
      const alias = list.aliases[key];
      if (typeof alias === "string" && list.towns.includes(alias)) return alias;
      const error = new Error("That isn't a Florida town we know. Try a city or town name, e.g. Tampa.");
      error.code = "invalid_town";
      throw error;
    }

    // The year doesn't matter: a date search means that month and day in every
    // year we have. Only collected days exist, so failing that, the nearest one.
    function pick(pool, date, town) {
      const monthDay = MONTH_DAY.test(date) ? date : date.slice(5);
      const sameDay = pool.filter((e) => e.incidentDate.slice(5) === monthDay);
      if (sameDay.length) return { hits: sameDay, message: null };
      if (!pool.length) return { hits: [], message: null };
      // Nearest collected month/day, in a leap year, wrapping round New Year.
      const distance = (e) => {
        const d = Math.abs(daysBetween(`2000-${e.incidentDate.slice(5)}`, `2000-${monthDay}`));
        return Math.min(d, 366 - d);
      };
      const best = Math.min(...pool.map(distance));
      const hits = pool.filter((e) => distance(e) === best);
      const nearest = hits.map((e) => e.incidentDate).sort().reverse()[0];
      return {
        hits,
        message: town
          ? `No ${town} stories for that day yet. The closest we have is ${formatDate(nearest)}.`
          : `Nothing collected for that day yet. The closest we have is ${formatDate(nearest)}.`,
      };
    }

    return {
      today: async () => cleanDay(await file("data/today.json"), null),
      day,
      towns,

      async story(id) {
        if (!STORY_ID.test(id || "")) throw notFound("That story link doesn’t look right.");
        const entry = (await index()).find((e) => e.id === id);
        const story = entry ? await storyFrom(entry) : null;
        if (!story) throw notFound("That story isn’t in the archive. Try a search.");
        return { story };
      },

      async random() {
        const entries = (await index()).filter((e) => e.randomOk);
        if (!entries.length) {
          return { date: null, story: null, empty: true, message: EMPTY_TEXT, shareUrl: null };
        }
        const entry = entries[Math.floor(Math.random() * entries.length)];
        const story = await storyFrom(entry);
        if (!story) throw notFound("The dice fell under the couch.");
        return { date: entry.date, story, empty: false, message: null, shareUrl: story.shareUrl };
      },

      async search(town, date) {
        if (!ISO_DATE.test(date || "") && !MONTH_DAY.test(date || "")) {
          throw notFound("Use MM-DD or YYYY-MM-DD.");
        }
        const name = town ? await canonicalTown(town) : null;
        const entries = await index();
        const pool = name ? entries.filter((e) => e.town && townKey(e.town) === townKey(name)) : entries;
        const { hits, message } = pick(pool, date, name);
        const top = hits.sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : 1)).slice(0, MAX_RESULTS);
        const results = (await Promise.all(top.map(storyFrom))).filter(Boolean);
        const shareQuery = new URLSearchParams(name ? { town: name, date } : { scope: "statewide", date });
        if (!results.length) {
          const where = name ? `${name} stories` : "stories";
          return {
            results: [], empty: true, shareUrl: `?${shareQuery}`,
            message: entries.length
              ? `No ${where} in the archive yet. It grows by one day every morning.`
              : EMPTY_TEXT,
          };
        }
        return { results, empty: false, message, shareUrl: `?${shareQuery}` };
      },
    };
  })();

  const api = STATIC ? staticApi : serverApi;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function formatDate(iso) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(iso || "")) return "";
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(undefined, {
      year: "numeric", month: "long", day: "numeric", timeZone: "UTC",
    });
  }

  function summaryNode(story) {
    if (story.summaryPending) {
      const loading = el("div", "summary-loading");
      loading.setAttribute("role", "img");
      loading.setAttribute("aria-label", "Summary loading");
      return loading;
    }
    const kind = story.summarySource === "generated" ? " is-generated" : "";
    return el("p", "story-summary" + kind, story.summary || "");
  }

  function storyCard(story, index, total) {
    const card = el("article", "story-card");
    card.setAttribute("aria-roledescription", "slide");
    card.setAttribute("aria-label", `${index + 1} of ${total}`);

    card.append(el("p", "story-date", formatDate(story.incidentDate)));
    card.append(el("p", "story-town", story.town ? `${story.town}, Florida` : "Somewhere, Florida"));
    card.append(el("h3", "", story.headline || "Florida Man remains Florida Man."));
    card.append(summaryNode(story));

    if (story.sourceName && validUrl(story.sourceUrl)) {
      const source = el("p", "story-source", "Source: ");
      const link = el("a", "", story.sourceName);
      link.href = story.sourceUrl;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      source.append(link);
      card.append(source);
    }
    return card;
  }

  function updateControls() {
    const many = stories.length > 1;
    previous.hidden = !many;
    next.hidden = !many;
    previous.disabled = current <= 0;
    next.disabled = current >= stories.length - 1;
    position.textContent = many ? `${current + 1} of ${stories.length}` : "";
    shareButton.hidden = !stories.length && !payloadShareUrl;
  }

  function render() {
    carousel.replaceChildren(...stories.map((s, i) => storyCard(s, i, stories.length)));
    carousel.scrollLeft = 0;
    current = 0;
    updateControls();
  }

  function cardLeft(card) {
    return card.offsetLeft - carousel.offsetLeft;
  }

  function goTo(index) {
    if (!stories.length) return;
    current = Math.max(0, Math.min(stories.length - 1, index));
    const card = carousel.children[current];
    if (card) carousel.scrollTo({ left: cardLeft(card), behavior: "smooth" });
    updateControls();
    pollCurrent();
  }

  function replaceStory(index, story) {
    stories[index] = story;
    const old = carousel.children[index];
    if (old) old.replaceWith(storyCard(story, index, stories.length));
  }

  function clearPolls() {
    pollTimers.forEach(clearTimeout);
    pollTimers = [];
  }

  // Re-poll the slide on screen while its summary is still being fetched.
  function pollCurrent() {
    clearPolls();
    const index = current;
    const story = stories[index];
    if (STATIC || !story || !story.summaryPending || !story.id) return;
    const token = loadToken;
    POLL_DELAYS.forEach((delay) => {
      pollTimers.push(setTimeout(async () => {
        if (token !== loadToken || !stories[index] || !stories[index].summaryPending) return;
        try {
          const fresh = await api.story(story.id);
          if (token !== loadToken || !fresh.story) return;
          replaceStory(index, fresh.story);
        } catch {
          // Keep the card as it is; the next poll or a reload may do better.
        }
      }, delay));
    });
  }

  function showMessage(text) {
    message.hidden = !text;
    message.textContent = text || "";
  }

  function showPayload(payload, mode) {
    const stale = mode === "day" && STATIC && payload.date && payload.date !== dateInput.max;
    heading.textContent = HEADINGS[stale ? "date" : mode];
    payloadShareUrl = payload.shareUrl || null;
    setStatus("");

    if (payload.empty) {
      stories = [];
      showMessage(payload.message || EMPTY_TEXT);
      render();
      return;
    }
    showMessage([payload.message, payload.stale ? "This might be a bit old." : ""]
      .filter(Boolean).join(" "));

    if (mode === "search") {
      stories = Array.isArray(payload.results) ? payload.results : [];
    } else {
      const lead = payload.story ? [payload.story] : [];
      const more = mode === "day" || mode === "date" ? payload.more : [];
      stories = lead.concat(Array.isArray(more) ? more : []);
    }
    render();
    pollCurrent();
  }

  function routeFromUrl() {
    const params = new URLSearchParams(location.search);
    const storyId = params.get("story");
    const date = params.get("date");
    const town = params.get("town");
    if (storyId) return { mode: "story", fetch: () => api.story(storyId) };
    if (date && (town || params.get("scope") === "statewide")) {
      return { mode: "search", fetch: () => api.search(town || "", date), town: town || "", date };
    }
    if (date) return { mode: "date", fetch: () => api.day(date), date };
    return { mode: "day", fetch: () => api.today() };
  }

  dateInput.max = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });

  function prefill(route) {
    townInput.value = route.town || "";
    showTownFilter(false);
    // An empty date box read as broken, so it shows today until someone picks a day.
    dateInput.value = /^\d{4}-\d{2}-\d{2}$/.test(route.date || "") ? route.date : dateInput.max;
  }

  function showError(error, fallback) {
    clearPolls();
    stories = [];
    payloadShareUrl = null;
    render();
    showMessage("");
    const fromApi = error.message && error.message !== "Request failed." ? error.message : "";
    setStatus(error.code === "rate_limited" ? "Too many requests. Try again in a moment." : fromApi || fallback);
  }

  async function load() {
    const token = ++loadToken;
    clearPolls();
    const route = routeFromUrl();
    if (route.mode !== "story") prefill(route);
    if (!dateInput.value) dateInput.value = dateInput.max;
    setStatus("Loading…");
    try {
      const payload = await route.fetch();
      if (token === loadToken) showPayload(payload, route.mode);
    } catch (error) {
      if (token === loadToken) showError(error, "Couldn’t load the files.");
    }
  }

  async function rollRandom() {
    const token = ++loadToken;
    clearPolls();
    randomButton.disabled = true;
    setStatus("Rolling…");
    try {
      const payload = await api.random();
      if (token !== loadToken) return;
      if (payload.story) history.pushState({}, "", payload.story.shareUrl || "./");
      showPayload(payload, "random");
    } catch (error) {
      if (token === loadToken) showError(error, "The dice fell under the couch.");
    } finally {
      randomButton.disabled = false;
    }
  }

  function track(event, story) {
    if (STATIC) return; // no analytics on the public site
    const body = { event };
    if (story && story.id) body.storyId = story.id;
    fetch("api/track", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).catch(() => {});
  }

  function showCopyField(url) {
    let field = $("copy-field");
    if (!field) {
      field = el("input", "copy-field");
      field.id = "copy-field";
      field.readOnly = true;
      field.setAttribute("aria-label", "Link to copy");
      shareButton.after(field);
    }
    field.value = url;
    field.focus();
    field.select();
  }

  async function share() {
    const story = stories[current];
    const relative = (story && story.shareUrl) || payloadShareUrl || location.href;
    const url = new URL(relative, location.href).href;
    if (navigator.share) {
      try {
        await navigator.share({ title: story ? story.headline : "Florida Man of the Day", url });
        track("share", story);
      } catch (error) {
        if (error && error.name !== "AbortError") showCopyField(url);
      }
      return;
    }
    try {
      await navigator.clipboard.writeText(url);
      shareLabel.textContent = "Link copied";
      setTimeout(() => { shareLabel.textContent = "Share"; }, 1800);
    } catch {
      showCopyField(url);
    }
    track("copy", story);
  }

  function nearestCard() {
    const cards = [...carousel.children];
    const left = carousel.scrollLeft;
    let best = 0;
    cards.forEach((card, i) => {
      if (Math.abs(cardLeft(card) - left) < Math.abs(cardLeft(cards[best]) - left)) best = i;
    });
    return best;
  }

  $("search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const date = dateInput.value;
    const town = townInput.value.trim();
    if (!date) {
      setStatus("Pick a date first.");
      dateInput.focus();
      return;
    }
    const params = new URLSearchParams(town ? { town, date } : { scope: "statewide", date });
    history.pushState({}, "", `?${params}`);
    load();
  });

  townToggle.addEventListener("click", () => {
    const open = townRow.hidden;
    showTownFilter(open);
    if (open) townInput.focus();
  });

  randomButton.addEventListener("click", rollRandom);
  shareButton.addEventListener("click", share);
  previous.addEventListener("click", () => goTo(current - 1));
  next.addEventListener("click", () => goTo(current + 1));

  carousel.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") { event.preventDefault(); goTo(current - 1); }
    if (event.key === "ArrowRight") { event.preventDefault(); goTo(current + 1); }
  });

  // Swipes settle on a card; follow them without scrolling again ourselves.
  let scrollTimer = null;
  carousel.addEventListener("scroll", () => {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      if (!stories.length) return;
      const index = nearestCard();
      if (index !== current) {
        current = index;
        updateControls();
        pollCurrent();
      }
    }, 120);
  });

  $("dateline").textContent = new Date().toLocaleDateString(undefined, {
    weekday: "long", month: "long", day: "numeric", year: "numeric", timeZone: "America/New_York",
  });

  window.addEventListener("popstate", load);
  load();
})();
