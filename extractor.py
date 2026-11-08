import html
import re


# (output field, element id without the "ContentPlaceHolder1_" prefix)
TEXT_FIELDS = (
    ("latin_display", "lbldisplatinname"),
    ("common_name", "lblCommanName"),          # the site has this typo
    ("family", "lblFamily"),
    ("usda_hardiness", "lblUSDAhardiness"),
    ("known_hazards", "lblKnownHazards"),
    ("habitats", "txtHabitats"),
    ("range", "lblRange"),
    ("weed_potential", "lblWeedPotential"),
    ("conservation_status", "lblConservationStatus"),
    ("synonyms", "lblSynonyms"),
    ("other_names", "lblOtherNameText"),
    ("found_in", "lblFoundInText"),
    ("plant_habitats", "lblhabitats"),
    ("summary", "txtSummary"),
    ("physical_characteristics", "lblPhystatment"),
    ("edible_uses", "txtEdibleUses"),
    ("medicinal_uses", "txtMediUses"),
    ("other_uses", "txtOtherUses"),
    ("special_uses", "txtSpecialUses"),
    ("cultivation", "txtCultivationDetails"),
    ("propagation", "txtPropagation"),
    ("weed_potential_text", "lblWeedPotentialText"),
    ("carbon_farming", "lblCarbFarm"),
)
RATING_FIELDS = (
    ("edibility_rating", "txtEdrating"),        # "(3 of 5)" -> "3"
    ("medicinal_rating", "txtMedRating"),
    ("other_uses_rating", "txtOtherUseRating"),
)
FIELDNAMES = (["latin_name"] + [name for name, _ in TEXT_FIELDS]
              + [name for name, _ in RATING_FIELDS] + ["care"])

BLOCK_END_RE = re.compile(r"<br\s*/?>|</(?:p|li|div|tr|td|h\d)>", re.IGNORECASE)


def clean(value):
    """HTML fragment -> plain text: block tags become spaces, tags are removed,
    entities (&nbsp; ...) are decoded and whitespace is collapsed (TSV-safe)."""
    value = BLOCK_END_RE.sub(" ", value)
    value = html.unescape(re.sub(r"<[^>]+>", "", value))
    return re.sub(r"\s+", " ", value).strip()


def extract_by_id(element_id, text):
    """Plain text of the element id="ContentPlaceHolder1_<element_id>", or ''.
    Handles nested tags of the same name by counting open/close pairs."""
    opening = re.search(
        r'<([A-Za-z][A-Za-z0-9]*)\b[^>]*\bid="ContentPlaceHolder1_'
        + re.escape(element_id) + r'"[^>]*>', text)
    if not opening or opening.group(0).endswith("/>"):
        return ""
    tag_re = re.compile(r"<(/?)" + opening.group(1) + r"\b[^>]*>", re.IGNORECASE)
    depth = 1
    for tag in tag_re.finditer(text, opening.end()):
        if tag.group(0).endswith("/>"):
            continue                           # self-closing, no matching end tag
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return clean(text[opening.end():tag.start()])
    return ""                                  # unbalanced markup


def extract_rating(element_id, text):
    """'(3 of 5)' -> '3', otherwise ''."""
    match = re.search(r"(\d)\s+of\s+5", extract_by_id(element_id, text))
    return match.group(1) if match else ""


def extract_care(text):
    """Care icons (Moist Soil, Semi-shade, Full sun ...) from the icon table."""
    match = re.search(r'<table id="ContentPlaceHolder1_tblIcons">(.*?)</table>',
                      text, re.DOTALL)
    return re.findall(r"alt='([^']*)'", match.group(1)) if match else []


def extract_row(latin_name, text):
    """All fields of one plant page as a dict keyed by FIELDNAMES."""
    row = {"latin_name": latin_name}
    for name, element_id in TEXT_FIELDS:
        row[name] = extract_by_id(element_id, text)
    for name, element_id in RATING_FIELDS:
        row[name] = extract_rating(element_id, text)
    row["care"] = "; ".join(extract_care(text))
    return row


def strip_references(text):
    """Remove citation markers like [43] or [12, 40] (useful before indexing)."""
    return re.sub(r"\[\d+(?:\s*,\s*\d+)*\]", " ", text)