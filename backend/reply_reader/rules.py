"""Keyword + number reply reader: the fallback when OCI GenAI is off, slow or weak on Swahili
(EXECUTION_PLAN risks). Produces the same JSON as the GenAI path (reply_reader/schema.py).

Confidence is deliberately modest: a clear pattern with a count scores 0.8, so only clean replies
are auto-accepted and everything else lands in the officers' review queue.
"""
import re

SW_UNITS = {"moja": 1, "mbili": 2, "tatu": 3, "nne": 4, "tano": 5, "sita": 6, "saba": 7, "nane": 8, "tisa": 9,
            # noun-class forms: "ng'ombe watatu" (wa-), "magunia matano" (ma-)
            "mmoja": 1, "wawili": 2, "watatu": 3, "wanne": 4, "watano": 5, "wanane": 8,
            "mawili": 2, "matatu": 3, "manne": 4, "matano": 5, "manane": 8}
SW_TENS = {"kumi": 10, "ishirini": 20, "thelathini": 30, "arobaini": 40, "hamsini": 50,
           "sitini": 60, "sabini": 70, "themanini": 80, "tisini": 90}
EN_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
              "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100}

SPECIES_WORDS = [
    ("cattle", r"ng['’`]?ombe|cattle|cows?|bulls?|ndama"),
    ("goats", r"mbuzi|goats?"),
    ("sheep", r"kondoo|sheep"),
    ("camels", r"ngamia|camels?"),
]

# (report_type, pattern) checked in order; the first match wins.
TYPE_PATTERNS = [
    ("bags_dried", r"\bdried\b|nimekausha|tumekausha|zimekaushwa|yamekaushwa|imekaushwa|\bkaushwa\b"),
    ("dead_animals", r"\bdead\b|\bdied\b|wamekufa|imekufa|amekufa|wamekufa|\bkufa\b|mizoga|mzoga"),
    ("rvf_signs", r"\brvf\b|homa ya bonde|kutupa mimba|mimba (?:zime|ime)toka|abort"),
    ("sick_animals", r"\bsick\b|\bill\b|wagonjwa|mgonjwa|wanaumwa|anaumwa|ugonjwa"),
    ("water_point_dry", r"(kisima|bwawa|borehole|water ?point|\bwell\b|\bdam\b|maji)\W+(?:\w+\W+){0,3}?"
                        r"(kimekauka|imekauka|yamekauka|limekauka|\bdry\b|dried up|yamekwisha|hakuna)"),
    ("flooding", r"mafuriko|\bflood|imefurika|yamefurika|maji yamejaa|\bfurika"),
]
BAG_WORDS = r"magunia|gunia|\bbags?\b|\bsacks?\b|\bmifuko\b"
WET_WORDS = r"mabichi|\bbichi\b|unyevu|\bwet\b|\bdamp\b|\bmoist|hayajakauka|hazijakauka|haijakauka|maji"
HARVEST_WORDS = r"nimevuna|tumevuna|amevuna|\bvuna\b|harvested|\bharvest\b"
RAIN_WORDS = r"\bmvua\b|imenyesha|inanyesha|\brain(?:s|ed|ing)?\b"
SW_MARKERS = r"\b(na|ya|wa|za|kwa|mvua|magunia|nime\w+|ime\w+|mahindi|ng['’]?ombe|mbuzi|asante|ndiyo|hapana|leo|kata)\b"
EN_MARKERS = r"\b(the|and|my|are|is|have|bags|rain|dried|sick|dead|maize|today|cows?|goats?)\b"


def _rx(pattern):
    return re.compile(pattern, re.IGNORECASE)


def parse_number_words(text):
    """'kumi na tano' -> 15, 'ishirini' -> 20, 'mia mbili' -> 200, 'twenty' -> 20."""
    tokens = re.findall(r"[a-z]+", text.lower())
    best = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        value = None
        if tok == "mia":
            mult = SW_UNITS.get(tokens[i + 1], 1) if i + 1 < len(tokens) else 1
            value = 100 * mult
            i += 2 if i + 1 < len(tokens) and tokens[i + 1] in SW_UNITS else 1
            if i + 1 < len(tokens) and tokens[i] == "na" and (tokens[i + 1] in SW_TENS or tokens[i + 1] in SW_UNITS):
                value += SW_TENS.get(tokens[i + 1], 0) + SW_UNITS.get(tokens[i + 1], 0)
                i += 2
        elif tok in SW_TENS:
            value = SW_TENS[tok]
            i += 1
            if i + 1 < len(tokens) and tokens[i] == "na" and tokens[i + 1] in SW_UNITS:
                value += SW_UNITS[tokens[i + 1]]
                i += 2
        elif tok in SW_UNITS or tok in EN_NUMBERS:
            value = SW_UNITS.get(tok) or EN_NUMBERS[tok]
            i += 1
        else:
            i += 1
        if value is not None and best is None:
            best = value
    return best


def _count(text):
    """First count in the text, ignoring moisture percentages."""
    without_pct = re.sub(r"\d+(?:[.,]\d+)?\s*%", " ", text)
    digits = re.search(r"(?<![\d.])(\d{1,6})(?![\d.])", without_pct)
    if digits:
        return int(digits.group(1))
    return parse_number_words(without_pct)


def _moisture(text):
    match = re.search(r"(\d{1,2}(?:[.,]\d)?)\s*%", text)
    return float(match.group(1).replace(",", ".")) if match else None


def _language(text):
    sw = len(_rx(SW_MARKERS).findall(text))
    en = len(_rx(EN_MARKERS).findall(text))
    return "en" if en > sw else "sw"


def _species(text):
    found = [name for name, pattern in SPECIES_WORDS if _rx(pattern).search(text)]
    return found[0] if len(found) == 1 else None, len(found) > 1


def extract(text):
    text = (text or "").strip()
    rain = bool(_rx(RAIN_WORDS).search(text))
    result = {
        "report_type": "other", "count": None, "unit": None, "crop": None, "species": None,
        "rain_reported": rain, "moisture_pct": _moisture(text), "language": _language(text),
        "confidence": 0.3,
    }
    if not text:
        return result
    if _rx(r"mahindi|maize|\bcorn\b").search(text):
        result["crop"] = "maize"
    count = _count(text)
    species, mixed_species = _species(text)

    for report_type, pattern in TYPE_PATTERNS:
        if _rx(pattern).search(text):
            result["report_type"] = report_type
            break
    else:
        has_bags = bool(_rx(BAG_WORDS).search(text))
        if has_bags and _rx(WET_WORDS).search(text) or has_bags and rain and _rx(HARVEST_WORDS).search(text):
            result["report_type"] = "wet_bags"
        elif _rx(HARVEST_WORDS).search(text):
            result["report_type"] = "harvested"
        elif has_bags and count is not None:
            result["report_type"] = "wet_bags"  # "magunia 20" after a heavy-rain alert asking for wet bags
            result["confidence"] = 0.6
        elif rain:
            result["report_type"] = "rain"

    rtype = result["report_type"]
    if rtype in ("wet_bags", "bags_dried", "harvested"):
        result["unit"] = "bags" if count is not None else None
        result["crop"] = result["crop"] or "maize"
    if rtype in ("sick_animals", "dead_animals", "rvf_signs"):
        result["species"] = species
        result["unit"] = "animals" if count is not None else None
    if rtype != "other":
        result["count"] = count
        needs_count = rtype in ("wet_bags", "sick_animals", "dead_animals", "bags_dried")
        if result["confidence"] == 0.3:
            result["confidence"] = 0.8 if (count is not None or not needs_count) else 0.6
        if rtype in ("rain", "harvested") and count is None:
            result["confidence"] = 0.75
        if mixed_species:
            result["confidence"] = min(result["confidence"], 0.6)
    return result
