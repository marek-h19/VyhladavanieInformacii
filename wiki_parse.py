"""
wiki_parse.py - regex-only parsing of English Wikipedia dump records.

One dump record is the text of one <page>...</page> element. We do not use an XML
parser: title, namespace, redirect target and wikitext are found with regular
expressions, and the taxobox (Speciesbox / Automatic taxobox / Taxobox) is cut out
by counting {{ }} pairs, like extractor.extract_by_id does for HTML.

parse_record(record) returns None (not an article) or a dict:
  {"kind": "redirect", "title", "target"}
  {"kind": "article",  "title"}                              article without taxobox
  {"kind": "taxon",    "title", "wiki_id", "taxon", "genus", "authority", "status",
                       "image", "common_names", "lead", "categories", "sections",
                       "text_len"}

latin_key / binomial_key / genus_key normalise Latin names so that PFAF names and
Wikipedia names can be joined (case, hybrid sign, cultivar names, authors).
"""

import html
import re

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
NS_RE = re.compile(r"<ns>(\d+)</ns>")
ID_RE = re.compile(r"</title>\s*<ns>\d+</ns>\s*<id>(\d+)</id>")
REDIRECT_RE = re.compile(r'<redirect\s+title="([^"]*)"')
HEAD = 3000                                    # page header (title, ns, id, redirect) is this short
QUICK_BOX_RE = re.compile(r"peciesbox|axobox", re.I)    # Speciesbox / taxobox somewhere in the text

TAXOBOX_RE = re.compile(
    r"\{\{\s*(?:speciesbox|automatic taxobox|taxobox|subspeciesbox|infraspeciesbox)\b", re.I)
BRACES_RE = re.compile(r"\{\{|\}\}")
FIELD_SPLIT_RE = re.compile(r"\n\s*\|")
INLINE_SPLIT_RE = re.compile(r"\|(?=\s*[A-Za-z0-9_ ]+=)")
KEY_RE = re.compile(r"^[a-z0-9_ ]+$")

COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
REF_RE = re.compile(r"<ref\b[^>/]*>.*?</ref\s*>", re.S | re.I)
REF_SELF_RE = re.compile(r"<ref\b[^>]*/>", re.I)
GALLERY_RE = re.compile(r"<(gallery|math|timeline|imagemap)\b.*?</\1\s*>", re.S | re.I)
TABLE_RE = re.compile(r"\{\|.*?\|\}", re.S)
TEMPLATE_RE = re.compile(r"\{\{([^{}|]*)(?:\|([^{}]*))?\}\}")
FILE_LINK_RE = re.compile(
    r"\[\[(?:File|Image):(?:[^\[\]]|\[\[[^\[\]]*\]\])*\]\]", re.I)
CATEGORY_RE = re.compile(r"\[\[\s*Category\s*:\s*([^\]|]+?)\s*(?:\|[^\]]*)?\]\]", re.I)
LINK_RE = re.compile(r"\[\[(?:[^\[\]|]*\|)?([^\[\]]*)\]\]")
EXT_LINK_RE = re.compile(r"\[(?:https?:)?//[^\s\]]+(?:\s+([^\]]*))?\]")
HEADING_RE = re.compile(r"^={2,4}\s*([^=\n]+?)\s*={2,4}\s*$", re.M)
BOLD_RE = re.compile(r"(?<!')'''(?!')(.+?)(?<!')'''(?!')")
TAG_RE = re.compile(r"<[^>]+>")

KEEP_LAST_ARG = {"lang", "nowrap", "nobr", "small", "lang-la", "lang-fr", "lang-de",
                 "transl", "strong", "em", "linktext"}


# ------------------------------------------------------------ wikitext cleaning
RANGE_WORDS = {"-": "–", "–": "–", "—": "–", "to": " to ", "and": " and ",
               "or": " or ", "by": " by ", "x": " x ", "×": " x "}
SPACE_TEMPLATES = {"nbsp": " ", "snd": " – ", "spaced ndash": " – ",
                   "ndash": "–", "mdash": "—"}


def _convert(args):
    """{{convert|13|-|20|ft|m}} -> '13-20 ft';  {{convert|3|m|ft}} -> '3 m'."""
    values = [a.strip() for a in args if "=" not in a]
    if len(values) >= 4 and values[1].lower() in RANGE_WORDS:
        return f"{values[0]}{RANGE_WORDS[values[1].lower()]}{values[2]} {values[3]}"
    return f"{values[0]} {values[1]}" if len(values) >= 2 else ""


def _template(match):
    """Template -> text: keep the visible argument of a few wrappers, drop the rest."""
    name = match.group(1).strip().lower()
    args = (match.group(2) or "").split("|")
    if name in SPACE_TEMPLATES:
        return SPACE_TEMPLATES[name]
    if name in KEEP_LAST_ARG and args:
        return args[-1].split("=", 1)[-1]
    if name in ("convert", "cvt"):
        return _convert(args)
    return ""                                        # pronunciation, citations, hatnotes ...


# what is left of removed pronunciation templates: "( ; )", "(pronounced or )", "(; derived"
LEADING_PUNCT_PAREN_RE = re.compile(r"\(\s*[;,:]+\s*")
FILLER_PAREN_RE = re.compile(r"\(\s*(?:(?:pronounced|pronunciation|or|and|also)\b\s*|[;,/]\s*)*\)",
                             re.I)
SPACE_BEFORE_PUNCT_RE = re.compile(r"[ \t]+([,.;:])")


def wiki_to_text(text):
    """Wikitext -> plain text (links resolved, templates/refs/tables/tags removed)."""
    text = COMMENT_RE.sub("", text)
    text = REF_RE.sub("", text)
    text = REF_SELF_RE.sub("", text)
    text = GALLERY_RE.sub("", text)
    text = TABLE_RE.sub("", text)
    for _ in range(12):                              # innermost templates first
        replaced = TEMPLATE_RE.sub(_template, text)
        if replaced == text:
            break
        text = replaced
    text = FILE_LINK_RE.sub("", text)
    text = CATEGORY_RE.sub("", text)
    text = LINK_RE.sub(r"\1", text)
    text = EXT_LINK_RE.sub(lambda m: m.group(1) or "", text)
    text = re.sub(r"'{2,}", "", text)
    text = TAG_RE.sub("", text)
    text = html.unescape(text).replace("{{", "").replace("}}", "")
    text = LEADING_PUNCT_PAREN_RE.sub("(", text)
    text = FILLER_PAREN_RE.sub("", text)
    return SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)


def plain(text):
    """Like wiki_to_text, but a single clean line (TSV-safe)."""
    return re.sub(r"\s+", " ", wiki_to_text(text)).strip()


# ------------------------------------------------------------------ taxobox
def template_end(text, start):
    """text[start:] begins with '{{': index just after the matching '}}', or -1."""
    depth = 0
    for match in BRACES_RE.finditer(text, start):
        depth += 1 if match.group() == "{{" else -1
        if depth == 0:
            return match.end()
    return -1


def taxobox_fields(block):
    """'{{Speciesbox | genus = Abies ...}}' -> {'genus': 'Abies', ...} (first key wins)."""
    body = block[2:-2]
    chunks = FIELD_SPLIT_RE.split(body) if "\n" in body else INLINE_SPLIT_RE.split(body)
    fields = {}
    for index, chunk in enumerate(chunks):
        if "=" not in chunk:
            continue
        if index == 0:                               # "Speciesbox | genus = x" on one line
            chunk = chunk.partition("|")[2]
            if "=" not in chunk:
                continue
        key, _, value = chunk.partition("=")
        key = key.strip().lower()
        if KEY_RE.match(key) and key not in fields:
            fields[key] = value.strip()
    return fields


def taxon_name(fields):
    """Scientific name of the taxobox: taxon, binomial or genus + species."""
    for key in ("taxon", "binomial"):
        value = plain(fields.get(key, ""))
        if value:
            return value
    genus, species = plain(fields.get("genus", "")), plain(fields.get("species", ""))
    return f"{genus} {species}".strip()


def common_names(lead_raw, taxon, title):
    """Bold phrases of the first sentence (excluding the scientific name itself)."""
    seen, names = {taxon.lower(), title.lower()}, []
    for raw in BOLD_RE.findall(lead_raw.replace("'''''", "'''")[:700]):
        name = plain(raw)
        if 2 <= len(name) <= 60 and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names[:6]


def lead_paragraph(lead_clean, limit=1500):
    for paragraph in re.split(r"\n\s*\n", lead_clean):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if len(paragraph) >= 40 and not paragraph.startswith(":"):
            return paragraph[:limit]
    return ""


def wikitext(record):
    """Content of the <text ...>...</text> element (still XML-escaped), or None.
    Plain string search instead of a regex: it is much faster on 100 KB pages."""
    start = record.find("<text")
    if start < 0:
        return None
    end_tag = record.find(">", start)
    if end_tag < 0 or record[end_tag - 1] == "/":      # <text ... />  (deleted text)
        return None
    end = record.rfind("</text>")
    return record[end_tag + 1:end] if end > end_tag else None


def iter_pages(path, chunk_chars=8 * 1024 * 1024):
    """Yield the <page>...</page> records of a dump file (.xml or .xml.bz2) one by one.
    Streams the file in chunks, so a 100 GB dump never has to fit into memory."""
    import bz2
    opener = bz2.open if path.lower().endswith(".bz2") else open
    rest = ""
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        while True:
            data = f.read(chunk_chars)
            if not data:
                break
            *records, rest = (rest + data).split("</page>")
            for record in records:
                yield record + "</page>"
    if rest.strip():
        yield rest                                     # </mediawiki> or a truncated last page


def parse_record(record):
    """One <page> record -> dict (see module docstring) or None."""
    begin = max(record.find("<page>"), 0)             # the first record also holds <siteinfo>
    head_end = begin + HEAD                           # title, ns and redirect are at the page start
    title_match = TITLE_RE.search(record, begin, head_end)
    ns_match = NS_RE.search(record, begin, head_end)
    if not title_match or not ns_match or ns_match.group(1) != "0":
        return None
    title = html.unescape(title_match.group(1)).strip()
    redirect = REDIRECT_RE.search(record, begin, head_end)
    if redirect:
        target = html.unescape(redirect.group(1)).split("#")[0].strip()
        return {"kind": "redirect", "title": title, "target": target}
    raw = wikitext(record)
    if raw is None:
        return None
    if not QUICK_BOX_RE.search(raw, 0, 40000):         # cheap test before the expensive work
        return {"kind": "article", "title": title}
    text = html.unescape(raw)                          # dump text is XML-escaped
    box = TAXOBOX_RE.search(text, 0, 40000)
    if not box:
        return {"kind": "article", "title": title}
    end = template_end(text, box.start())
    if end < 0:
        return {"kind": "article", "title": title}
    fields = taxobox_fields(text[box.start():end])
    taxon = taxon_name(fields)
    if not taxon:
        return {"kind": "article", "title": title}
    body = text[end:]
    lead_raw = re.split(r"^==", body, maxsplit=1, flags=re.M)[0]
    id_match = ID_RE.search(record)
    return {
        "kind": "taxon",
        "title": title,
        "wiki_id": id_match.group(1) if id_match else "",
        "taxon": taxon,
        "genus": plain(fields.get("genus", "")) or taxon.split(" ")[0],
        "authority": plain(fields.get("authority", "")),
        "status": plain(fields.get("status", "")),
        "image": plain(fields.get("image", "")),
        "common_names": common_names(lead_raw, taxon, title),
        "lead": lead_paragraph(wiki_to_text(lead_raw)),
        "categories": [re.sub(r"\s+", " ", c) for c in CATEGORY_RE.findall(text)],
        "sections": [plain(h) for h in HEADING_RE.findall(body)],
        "text_len": len(text),
    }


# -------------------------------------------------------------- name matching
def latin_key(name):
    """Canonical join key: lower case, 'x'/'×' hybrid marker, no cultivar names.
    'Colutea × media' and 'Colutea x media' -> 'colutea x media'."""
    name = html.unescape(name)
    name = re.sub(r"(?:^|\s)['‘’\"][^'‘’\"]+['‘’\"]", " ", name)
    name = name.replace("×", " x ").lower()
    name = re.sub(r"[^\w.\- ]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return re.sub(r"^x ", "", name)


def binomial_key(name):
    """Genus + species (+ hybrid marker and epithet): 'abies alba pendula' -> 'abies alba'."""
    parts = latin_key(name).split(" ")
    if len(parts) >= 3 and parts[1] == "x":
        return " ".join(parts[:3])
    return " ".join(parts[:2])


def genus_key(name):
    return latin_key(name).split(" ")[0]