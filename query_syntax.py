"""
query_syntax.py - the small query language around the Lucene query parser (no Lucene needed).

Lucene's classic query parser already understands boolean operators (AND, OR, NOT, +, -),
phrases ("nitrogen fixing", "edible seed"~3), fuzzy terms (abies~1), prefix/wildcard terms
(abies*, field:val*) and field names (common_name:okra). It does NOT parse numeric ranges
of IntPoint fields and exact-value fields in a friendly way, so this module cuts a few
filter expressions out of the query text before the rest goes to the parser:

    family:Fabaceae           exact family (case-insensitive)
    category:"medicinal plants"   Wikipedia category
    status:LC                 IUCN status from the Wikipedia taxobox
    care:"full sun"           PFAF care icon (moist soil, full sun, semi-shade, ...)
    match:taxobox             how the plant was joined with Wikipedia
    wiki:yes | wiki:no        plant has / has no Wikipedia article
    edibility>=4              numeric rating 0-5 (also medicinal, otheruses)
    hardiness:7               USDA zone 7 lies inside the plant's hardiness range
    wikilen>=20000            length of the Wikipedia article in characters

Every filter is a non-scoring clause: it narrows the result set and does not change ranking.
"""

import re

MAX_INT = 2 ** 31 - 1

TERM_KEYS = {          # query key -> (index field, how a value is normalised)
    "family": "family_key",
    "category": "category",
    "status": "status",
    "care": "care_key",
    "match": "wiki_match",
    "wiki": "wiki_present",
}
RANGE_KEYS = {
    "edibility": "edibility_rating",
    "medicinal": "medicinal_rating",
    "otheruses": "other_uses_rating",
    "wikilen": "wiki_text_len",
}

TERM_RE = re.compile(r'(?<!\w)(' + "|".join(TERM_KEYS) + r'):(?:"([^"]+)"|(\S+))', re.IGNORECASE)
RANGE_RE = re.compile(r'(?<!\w)(' + "|".join(RANGE_KEYS) + r')\s*(>=|<=|>|<|=)\s*(\d+)',
                      re.IGNORECASE)
HARDINESS_RE = re.compile(r'(?<!\w)hardiness\s*[:=]\s*(\d{1,2})\b', re.IGNORECASE)
HARDINESS_VALUE_RE = re.compile(r"^\s*(\d{1,2})\s*(?:-|–|to)?\s*(\d{1,2})?\s*$")


def parse_filters(text):
    """(remaining query text, filters). A filter is ("term", field, value) or
    ("range", field, low, high) with inclusive integer bounds."""
    filters = []

    def term(match):
        key = match.group(1).lower()
        value = (match.group(2) or match.group(3)).strip().lower()
        if key == "wiki":
            value = "1" if value in ("yes", "true", "1", "y") else "0"
        filters.append(("term", TERM_KEYS[key], value))
        return " "

    def numeric(match):
        field = RANGE_KEYS[match.group(1).lower()]
        op, number = match.group(2), int(match.group(3))
        low, high = {">=": (number, MAX_INT), ">": (number + 1, MAX_INT),
                     "<=": (0, number), "<": (0, number - 1), "=": (number, number)}[op]
        filters.append(("range", field, low, high))
        return " "

    def hardiness(match):
        zone = int(match.group(1))
        filters.append(("range", "hardiness_min", 0, zone))      # hardy from zone <= z ...
        filters.append(("range", "hardiness_max", zone, 99))     # ... up to zone >= z
        return " "

    text = TERM_RE.sub(term, text)
    text = RANGE_RE.sub(numeric, text)
    text = HARDINESS_RE.sub(hardiness, text)
    return re.sub(r"\s+", " ", text).strip(), filters


def fuzzify(text):
    """Add ~1 (one edit) to every plain word of 4+ letters: typos still match.
    Phrases, operators, fields, wildcards and words that already have ~ are untouched."""
    out = []
    for token in re.findall(r'"[^"]*"(?:~\d+)?|\S+', text):
        plain = re.fullmatch(r"[^\W\d_]{4,}", token) and token.upper() not in ("AND", "NOT")
        out.append(token + "~1" if plain else token)
    return " ".join(out)


def parse_hardiness(value):
    """USDA hardiness text -> (min zone, max zone) or None ("5-9" -> (5, 9), "7" -> (7, 7),
    "Coming soon" -> None)."""
    match = HARDINESS_VALUE_RE.match(value or "")
    if not match:
        return None
    low = int(match.group(1))
    high = int(match.group(2)) if match.group(2) else low
    return (low, high) if low <= high and high <= 13 else None
