"""Phone numbers are stored E.164 (+2547XXXXXXXX), API_CONTRACT §0."""
import re

_DIGITS = re.compile(r"\D")
_KENYA_MOBILE = re.compile(r"^\+254[17]\d{8}$")
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class InvalidPhone(ValueError):
    pass


def normalize_phone(raw, kenya_only=True):
    """'0712 345 678', '254712345678', '712345678' and '+254712345678' all become '+254712345678'."""
    if raw is None:
        raise InvalidPhone("phone is required")
    text = str(raw).strip()
    digits = _DIGITS.sub("", text)
    if text.startswith("+"):
        candidate = "+" + digits
    elif digits.startswith("254"):
        candidate = "+" + digits
    elif digits.startswith("0") and len(digits) == 10:
        candidate = "+254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "17":
        candidate = "+254" + digits
    else:
        candidate = "+" + digits
    if _KENYA_MOBILE.match(candidate):
        return candidate
    if not kenya_only and _E164.match(candidate):
        return candidate
    raise InvalidPhone(f"'{raw}' is not a Kenyan mobile number in E.164 form (+2547XXXXXXXX)")


def to_provider_msisdn(phone):
    """Wasiliana takes international numbers without the plus sign (2547XXXXXXXX)."""
    return phone.lstrip("+")


def mask_phone(phone):
    return phone[:-6] + "***" + phone[-3:] if phone and len(phone) > 8 else phone
