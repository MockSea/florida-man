"""Bundled list of Florida places used to validate the `town` param."""
import difflib
import os
import re

_DATA = os.path.join(os.path.dirname(__file__), "data")
_FILES = ("municipalities.txt", "unincorporated.txt")

# Aliases for names people commonly type that differ from the official one.
_ALIASES = {
    "lake worth": "Lake Worth Beach",
    "st pete": "St. Petersburg",
    "ft lauderdale": "Fort Lauderdale",
    "ft myers": "Fort Myers",
    "ft pierce": "Fort Pierce",
    "ft walton beach": "Fort Walton Beach",
    "pcb": "Panama City Beach",
    "jax": "Jacksonville",
}

# Real places whose names are also common words/surnames; skipped when
# guessing a town from free headline text (still valid for search).
_NOT_FROM_HEADLINES = {
    "golf", "sunrise", "jay", "bell", "lee", "mayo", "parker", "malone",
    "webster", "callahan", "hilliard", "baldwin", "archer", "alford",
    "oakland", "hollywood", "weston", "davie", "holiday", "hudson", "pace",
    "lutz", "odessa", "orchid", "noma", "esto", "layton", "midway",
    "springfield", "greenville", "madison", "hampton", "jasper", "vernon",
    "century", "freeport", "bristol", "trenton", "highland park", "seminole",
    "medley", "venice", "naples", "san antonio", "miramar", "plantation",
    "golden gate", "lake park", "mims", "kendall",
}


def key(name):
    """Normalize a place name for comparison: case, punctuation, St./Saint."""
    s = name.lower().replace("-", " ")
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\bsaint\b", "st", s)
    return re.sub(r"\s+", " ", s).strip()


def _load():
    names = []
    for fn in _FILES:
        path = os.path.join(_DATA, fn)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    names.append(line)
    return sorted(set(names), key=str.lower)


TOWNS = _load()
_BY_KEY = {key(n): n for n in TOWNS}
for _alias, _canonical in _ALIASES.items():
    if key(_canonical) in _BY_KEY:
        _BY_KEY.setdefault(key(_alias), _canonical)


def aliases():
    """{normalized alias: canonical name}, for clients that match towns themselves."""
    return {key(a): c for a, c in sorted(_ALIASES.items()) if key(c) in _BY_KEY}


def canonical(name):
    """Return the canonical town name, or None if it isn't in the list."""
    if not isinstance(name, str) or not name.strip() or len(name) > 60:
        return None
    k = key(name)
    if k.endswith(" fl"):
        k = k[:-3].strip()
    elif k.endswith(" florida"):
        k = k[:-8].strip()
    return _BY_KEY.get(k)


def suggestions(name, n=3):
    if not isinstance(name, str):
        return []
    keys = difflib.get_close_matches(key(name), list(_BY_KEY), n=n, cutoff=0.75)
    out = []
    for k in keys:
        if _BY_KEY[k] not in out:
            out.append(_BY_KEY[k])
    return out


# Longest names first so "Miami Beach" wins over "Miami".
_HEADLINE_TOWNS = sorted(
    (n for n in TOWNS if key(n) not in _NOT_FROM_HEADLINES), key=len, reverse=True
)
_HEADLINE_RE = re.compile(
    r"(?<![A-Za-z])("
    + "|".join(re.escape(n).replace(r"\ ", r"\s+") for n in _HEADLINE_TOWNS)
    + r")(?![A-Za-z])"
)


def find_in_text(text):
    """Best-effort: first known town mentioned (case-sensitive) in text."""
    m = _HEADLINE_RE.search(text or "")
    return canonical(m.group(1)) if m else None
