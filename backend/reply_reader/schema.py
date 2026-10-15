"""The reply-reader JSON contract (API_CONTRACT §2.4), validated server-side.

Anything that is not exactly this shape is a SchemaError and the report goes to NEEDS_REVIEW.
"""
REPORT_TYPES = [
    "wet_bags", "sick_animals", "dead_animals", "flooding", "water_point_dry", "harvested", "rain",
    "bags_dried", "rvf_signs", "booking_confirm", "stop", "other",
]
UNITS = ["bags", "animals"]
CROPS = ["maize"]
SPECIES = ["cattle", "goats", "sheep", "camels"]
LANGS = ["sw", "en"]

FIELDS = ["report_type", "count", "unit", "crop", "species", "rain_reported", "moisture_pct", "language", "confidence"]

# JSON Schema handed to OCI GenAI as the structured-output format.
JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": FIELDS,
    "properties": {
        "report_type": {"type": "string", "enum": REPORT_TYPES},
        "count": {"type": ["number", "null"]},
        # Nullable enums are checked by validate(); providers differ on null inside "enum".
        "unit": {"type": ["string", "null"]},
        "crop": {"type": ["string", "null"]},
        "species": {"type": ["string", "null"]},
        "rain_reported": {"type": ["boolean", "null"]},
        "moisture_pct": {"type": ["number", "null"]},
        "language": {"type": "string", "enum": LANGS},
        "confidence": {"type": "number"},
    },
}


class SchemaError(ValueError):
    pass


def _number(value, name, low, high):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SchemaError(f"{name} must be a number or null")
    if not (low <= value <= high):
        raise SchemaError(f"{name} out of range")
    return int(value) if float(value).is_integer() else float(value)


def _choice(value, name, allowed, nullable=True):
    if value is None and nullable:
        return None
    if isinstance(value, str) and value.lower() in allowed:
        return value.lower()
    if value in ("null", "none") and nullable:
        return None
    raise SchemaError(f"{name} must be one of {allowed}")


def validate(data):
    """Return a clean dict with exactly FIELDS, or raise SchemaError."""
    if not isinstance(data, dict):
        raise SchemaError("output is not a JSON object")
    extra = set(data) - set(FIELDS)
    if extra:
        raise SchemaError(f"unexpected keys {sorted(extra)}")
    missing = [f for f in ("report_type", "language", "confidence") if f not in data]
    if missing:
        raise SchemaError(f"missing keys {missing}")
    rain = data.get("rain_reported")
    if rain is not None and not isinstance(rain, bool):
        raise SchemaError("rain_reported must be true, false or null")
    confidence = _number(data.get("confidence"), "confidence", 0, 1)
    if confidence is None:
        raise SchemaError("confidence is required")
    return {
        "report_type": _choice(data.get("report_type"), "report_type", REPORT_TYPES, nullable=False),
        "count": _number(data.get("count"), "count", 0, 100000),
        "unit": _choice(data.get("unit"), "unit", UNITS),
        "crop": _choice(data.get("crop"), "crop", CROPS),
        "species": _choice(data.get("species"), "species", SPECIES),
        "rain_reported": rain,
        "moisture_pct": _number(data.get("moisture_pct"), "moisture_pct", 0, 100),
        "language": _choice(data.get("language"), "language", LANGS, nullable=False),
        "confidence": float(confidence),
    }
