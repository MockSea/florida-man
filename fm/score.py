"""Florida Man-ness: a deterministic, explicitly subjective keyword score."""
import re

# (pattern, points, label). Patterns are matched case-insensitively on
# word boundaries; a trailing "s?"/"(?:es)?" etc. is written inline.
ANIMALS = [
    (r"alligators?|gators?", 30, "alligator"),
    (r"crocodiles?", 25, "crocodile"),
    (r"pythons?|boa constrictors?", 22, "big snake"),
    (r"snakes?", 15, "snake"),
    (r"iguanas?", 22, "iguana"),
    (r"manatees?", 22, "manatee"),
    (r"raccoons?", 18, "raccoon"),
    (r"o?possums?", 18, "possum"),
    (r"armadillos?", 22, "armadillo"),
    (r"kangaroos?|wallab(?:y|ies)", 25, "marsupial"),
    (r"emus?|ostrich(?:es)?", 25, "large bird"),
    (r"peacocks?|flamingos?|pelicans?|roosters?|ducks?|chickens?(?!\s+(?:nuggets?|wings?|tenders?|strips?|sandwich))", 15, "bird"),
    (r"monkeys?|chimps?|chimpanzees?", 22, "monkey"),
    (r"squirrels?", 18, "squirrel"),
    (r"sharks?", 18, "shark"),
    (r"goats?|pigs?|hogs?|cows?|horses?|donkeys?|llamas?|alpacas?", 12, "livestock"),
    (r"tortoises?|turtles?|frogs?|toads?|lizards?", 14, "reptile/amphibian"),
    (r"catfish|lobsters?|crabs?", 12, "seafood"),
    (r"bears?", 10, "bear"),
]

OBJECTS = [
    (r"chicken nuggets?|nuggets?", 28, "chicken nuggets"),
    (r"pool noodles?", 28, "pool noodle"),
    (r"porta[- ]?pott(?:y|ies)", 25, "porta-potty"),
    (r"toilets?|bathtubs?", 18, "bathroom fixture"),
    (r"lawn ?mowers?", 22, "lawnmower"),
    (r"golf carts?", 20, "golf cart"),
    (r"forklifts?|bulldozers?|tractors?|backhoes?", 18, "heavy machinery"),
    (r"mannequins?|blow[- ]up doll|inflatable", 20, "inflatable/mannequin"),
    (r"taco bell|mcdonald'?s|wendy'?s|burger king|chick-fil-a", 18, "fast food"),
    (r"burritos?|tacos?|cheeseburgers?|hot dogs?|pizza|sandwich(?:es)?|spaghetti|donuts?|doughnuts?", 14, "food"),
    (r"bananas?|coconuts?|watermelons?|pineapples?", 18, "produce"),
    (r"swords?|katanas?|machetes?|crossbows?|nunchucks?", 16, "medieval-adjacent weapon"),
    (r"fireworks?|flamethrowers?", 15, "pyrotechnics"),
    (r"shopping carts?|scooters?|jet ?skis?|kayaks?|unicycles?|hoverboards?", 12, "unusual vehicle"),
    (r"toothbrush(?:es)?|sex toys?|dildos?", 18, "personal item"),
]

BEHAVIOR = [
    (r"naked|nude|in his underwear", 20, "clothing optional"),
    (r"licked|licking|licks", 20, "licking"),
    (r"twerk\w*|karaoke|danc(?:ed|ing|es)", 18, "performance"),
    (r"dressed as|costume|clown|santa|elvis|superman|spider-?man|batman", 20, "costume"),
    (r"pooped|feces|defecat\w*|urinat\w*|peed", 18, "bodily functions"),
    (r"bit|bites|biting", 12, "biting"),
    (r"threw|throws|throwing|hurl(?:ed|s|ing)?|tossed", 10, "throwing things"),
    (r"blames?|blamed|claims?|claimed|says? (?:he|it) was", 12, "creative excuse"),
    (r"joyride|joy ride|stole|steals|stealing", 8, "theft"),
    (r"drunk|intoxicated|meth|bath salts", 8, "impaired"),
    (r"chase|fled|flee(?:s|ing)?", 8, "chase"),
    (r"impersonat\w*|pretend(?:ed|ing)? to be", 15, "impersonation"),
    (r"climbed|climbs|roof", 8, "climbing"),
]

LEGAL = [
    (r"called 911|calls 911|911", 14, "called 911"),
    (r"arrested|arrest", 6, "arrest"),
    (r"charged|charges", 5, "charges"),
    (r"deputies|sheriff'?s office|police say|cops", 5, "law enforcement"),
    (r"mugshot", 8, "mugshot"),
    (r"battery|assault", 6, "battery/assault"),
]

FLORIDA = [
    (r"waffle house", 18, "Waffle House"),
    (r"walmart", 12, "Walmart"),
    (r"publix", 15, "Publix"),
    (r"wawa|cracker barrel|7-eleven", 10, "Florida retail"),
    (r"airboats?|swamps?|everglades|bayou", 14, "swampland"),
    (r"sinkholes?|hurricanes?", 12, "Florida weather/geology"),
    (r"disney|theme park|spring break", 10, "tourism"),
    (r"flip[- ]flops?|trailer park|pontoon", 10, "Florida lifestyle"),
]

# Stories nobody should laugh at sink to the bottom.
GRIM = re.compile(
    r"\b(?:murder\w*|kill(?:s|ed|ing)?|homicide|manslaughter|dead|death|dies|died|"
    r"fatal\w*|shot|shooting|stabb\w*|rape\w*|sexual\w*|molest\w*|child(?:ren)?|"
    r"infant|bab(?:y|ies)|toddler|abuse\w*|suicide|overdose\w*|porn\w*|"
    r"massacre|terror\w*|hate crime|fentanyl|sex|human trafficking|"
    r"domestic violence|kidnapp\w*|tortur\w*|cruelty)\b",
    re.IGNORECASE,
)

REAL_WEAPONS = {
    "gun", "handgun", "pistol", "rifle", "shotgun", "firearm", "knife",
    "weapon", "revolver", "ar-15", "ak-47", "box", "cutter",
}
# "attacked ... with a <thing>": a weapon that isn't one.
NON_WEAPON = re.compile(
    r"\b(?:attack(?:ed|s|ing)?|hit(?:s|ting)?|beat(?:s|en|ing)?|struck|strikes?|"
    r"threaten(?:ed|s|ing)?|assault(?:ed|s|ing)?|armed|smack(?:ed|s)?|slapp(?:ed|ing)|"
    r"battered|whack(?:ed|s)?)\b(?:\s+[\w'-]+){0,4}?\s+with\s+(?:a|an|his|her|their|some)\s+"
    r"([\w'-]+(?:\s+[\w'-]+)?)",
    re.IGNORECASE,
)

CATEGORIES = (
    ("animal", ANIMALS, 35),
    ("object", OBJECTS, 30),
    ("behavior", BEHAVIOR, 30),
    ("legal", LEGAL, 15),
    ("florida", FLORIDA, 20),
)
_COMPILED = [
    (name, [(re.compile(r"\b(?:%s)\b" % p, re.IGNORECASE), pts, label) for p, pts, label in rules], cap)
    for name, rules, cap in CATEGORIES
]

BASE = 15


def is_grim(text):
    """True when a story is somebody's worst day: never served as a random pick."""
    return bool(GRIM.search(text or ""))


def score(text):
    """Return (score 1-100, reasons list) for headline (+summary) text."""
    text = text or ""
    total = BASE
    reasons = []
    for name, rules, cap in _COMPILED:
        got = 0
        for rx, pts, label in rules:
            if rx.search(text):
                got += pts
                reasons.append("%s: %s" % (name, label))
        total += min(got, cap)

    m = NON_WEAPON.search(text)
    if m:
        words = m.group(1).lower().split()
        if not any(w.strip("'") in REAL_WEAPONS for w in words):
            total += 25
            reasons.append("weapon that isn't: %s" % m.group(1).lower())

    if re.search(r"[\"“”!?]", text):
        total += 4
        reasons.append("entertainment: punctuation")
    if re.search(r"\b(?:allegedly|reportedly|bizarre|strange|weird|wild)\b", text, re.IGNORECASE):
        total += 4
        reasons.append("entertainment: self-aware headline")

    grim = len(GRIM.findall(text))
    if grim:
        total -= min(60, 30 * grim)
        reasons.append("penalty: grim story")

    return max(1, min(100, total)), reasons
