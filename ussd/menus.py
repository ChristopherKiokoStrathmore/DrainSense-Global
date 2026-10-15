"""MvuaSmart USSD menu state machine (Jiandikishe, Ripoti, Hali, Lugha, Acha, El Niño, Kausha).

The screen is a pure function of the inputs entered so far in this session
(`1*2*20`) plus what we knew about the number when the session started.
Every hop replays the whole path, so:

* API calls that end the session (register, report, status, stop, drying
  request or confirm) run on the END screen, which is always the newest input.
* Reads needed mid-session (the drying offer) are fetched once and kept in the
  per-session cache, so replays do not call the backend again.
* The language call is mid-session, so it runs only when the language choice is
  the newest input. Replays on later hops reuse the choice without calling again.
* An invalid input is skipped: the same screen is shown again with
  "Invalid choice", and later inputs are read against that same screen.

Menu screen text lives in TEXT below; El Niño advice lives in advice.py. Swahili copy must be checked by a native
speaker before the demo.
"""

from dataclasses import dataclass, field
from datetime import datetime

from advice import ELNINO
from backend import BackendError

LANG_PROMPT = "Chagua lugha / Choose language\n1. Kiswahili\n2. English"
MAX_SCREEN = 182  # USSD screen limit in characters, including "CON "
MORE = "98"

TEXT = {
    "sw": {
        "main": "MvuaSmart\n1. Jiandikishe\n2. Ripoti\n3. Hali ya kata yangu\n4. Lugha / Language\n5. Acha ujumbe\n6. Ushauri wa El Niño\n7. Kausha mahindi",
        "lang_set": "Lugha: Kiswahili",
        "invalid": "Chaguo si sahihi.",
        "error": "Samahani, jaribu tena.",
        "role": "Wewe ni:\n1. Mkulima\n2. Mfugaji\n3. Wote wawili",
        "county": "Chagua kaunti:",
        "ward": "Chagua kata:",
        "more": "Zaidi",
        "back": "Rudi",
        "main_menu": "Menyu kuu",
        "no_wards": "Samahani, hakuna kata zilizopatikana. Jaribu baadaye.",
        "consent": "Ukubali kupokea tahadhari za mvua na ushauri wa shamba bure kwa SMS?\n1. Ndiyo\n2. Hapana",
        "registered": "Asante! Umesajiliwa.",
        "declined": "Sawa, hujasajiliwa. Piga tena wakati wowote.",
        "report": "Unaona nini?\n1. Magunia yaliyolowa\n2. Mifugo wagonjwa/wafu\n3. Mafuriko\n4. Kisima/bwawa limekauka\n5. Nyingine",
        "bags_count": "Magunia mangapi yamelowa? Weka namba:",
        "animal_kind": "Mifugo:\n1. Wagonjwa\n2. Wamekufa",
        "animal_count": "Wanyama wangapi? Weka namba:",
        "bad_number": "Weka namba sahihi.",
        "reported": "Asante! Ripoti yako imepokelewa. Itasaidia kuboresha tahadhari za eneo lako.",
        "reported_bags": "Asante! Ripoti imepokelewa. Kuomba mashine ya kukausha, chagua 7 kwenye menyu kuu.",
        "dry_register_first": "Jiandikishe kwanza ili kuomba mashine ya kukausha: Menyu kuu > 1.",
        "dry_offer": "Mashine ya kukausha itafika {point} {when}. Magunia {bags}{fee}.\n1. Thibitisha\n2. Hapana",
        "dry_fee": ", ada KES {fee}",
        "dry_confirmed": "Umethibitisha! Peleka magunia {bags} {point} {when}.",
        "dry_declined": "Sawa, hujathibitisha. Piga tena ukibadilisha nia.",
        "dry_already": "Nafasi yako imethibitishwa: {point}, {when}, magunia {bags}.",
        "dry_bags": "Magunia mangapi ya mahindi yamelowa? Weka namba:",
        "dry_point": "Chagua mahali pa kupeleka magunia:",
        "dry_ask": "Omba kukausha magunia {bags}{at}?\n1. Ndiyo\n2. Hapana",
        "dry_at": " kwenye {point}",
        "outlook_title": "Okt-Des {year}: {pct}% uwezekano wa mvua kubwa sana. Chagua:",
        "dry_requested": "Ombi limepokelewa! Utapata SMS mashine ikipangwa. Sasa funika magunia na uyaweke juu ya mbao.",
        "dry_cancel": "Sawa, ombi halijatumwa.",
        "other": "Tafadhali tutumie SMS ukieleza unachoona shambani.",
        "stop_confirm": "Acha kupokea ujumbe wa MvuaSmart?\n1. Ndiyo, acha\n2. Hapana",
        "stopped": "Umeondolewa. Piga tena ili kujiandikisha upya.",
        "kept": "Sawa, utaendelea kupokea ujumbe wa MvuaSmart.",
    },
    "en": {
        "main": "MvuaSmart\n1. Register\n2. Report\n3. My ward status\n4. Lugha / Language\n5. Stop alerts\n6. El Niño advice\n7. Dry wet maize",
        "lang_set": "Language: English",
        "invalid": "Invalid choice.",
        "error": "Sorry, please try again.",
        "role": "You are a:\n1. Farmer\n2. Herder\n3. Both",
        "county": "Choose county:",
        "ward": "Choose ward:",
        "more": "More",
        "back": "Back",
        "main_menu": "Main menu",
        "no_wards": "Sorry, no wards are available. Please try later.",
        "consent": "Agree to receive free rain alerts and farm advice by SMS?\n1. Yes\n2. No",
        "registered": "Thank you! You are registered.",
        "declined": "OK, you are not registered. Dial again any time.",
        "report": "What do you see?\n1. Wet bags of grain\n2. Sick or dead animals\n3. Flooding\n4. Water point dry\n5. Other",
        "bags_count": "How many bags are wet? Enter a number:",
        "animal_kind": "Animals:\n1. Sick\n2. Dead",
        "animal_count": "How many animals? Enter a number:",
        "bad_number": "Enter a valid number.",
        "reported": "Thank you! Your report was received. It helps improve alerts for your area.",
        "reported_bags": "Thank you! Report received. To book a dryer, choose 7 on the main menu.",
        "dry_register_first": "Please register first to book a dryer: Main menu > 1.",
        "dry_offer": "A dryer will be at {point} on {when}. {bags} bags{fee}.\n1. Confirm\n2. No",
        "dry_fee": ", fee KES {fee}",
        "dry_confirmed": "Confirmed! Bring your {bags} bags to {point} on {when}.",
        "dry_declined": "OK, not confirmed. Dial again if you change your mind.",
        "dry_already": "Your drying slot is confirmed: {point}, {when}, {bags} bags.",
        "dry_bags": "How many bags of maize are wet? Enter a number:",
        "dry_point": "Choose where to bring your bags:",
        "dry_ask": "Request drying for {bags} bags{at}?\n1. Yes\n2. No",
        "dry_at": " at {point}",
        "outlook_title": "Oct-Dec {year}: {pct}% chance of very heavy rains. Choose:",
        "dry_requested": "Request received! You will get an SMS when a dryer is scheduled. Meanwhile cover the bags and keep them off the ground.",
        "dry_cancel": "OK, request not sent.",
        "other": "Please send us an SMS describing what you see on your farm.",
        "stop_confirm": "Stop MvuaSmart alerts?\n1. Yes, stop\n2. No",
        "stopped": "You have been unsubscribed. Dial again to register.",
        "kept": "OK, you will keep receiving MvuaSmart alerts.",
    },
}

LANGS = {"1": "sw", "2": "en"}
ROLES = {"1": "farmer", "2": "herder", "3": "both"}
# Menu 2 choice -> (report_type, unit). Animals pick sick/dead on the next screen.
REPORTS = {"1": ("wet_bags", "bags"), "3": ("flooding", None), "4": ("water_point_dry", None)}
ANIMALS = {"1": "sick_animals", "2": "dead_animals"}

SW_DAYS = ["Jumatatu", "Jumanne", "Jumatano", "Alhamisi", "Ijumaa", "Jumamosi", "Jumapili"]
EN_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
EN_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
POINT_NAME_MAX = 30  # collection point names are shortened on screen to fit the USSD limit


def format_when(iso: str, lang: str) -> str:
    """ISO-8601 time with offset -> "Jumanne 16/10 saa 2 asubuhi" / "Tue 16 Oct, 8:00 AM".

    Swahili time counts hours from 6 am, so 08:00 is "saa 2 asubuhi".
    The time is shown in the offset the backend sent (EAT, +03:00).
    """
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return iso or ""
    h, mins = dt.hour, f":{dt.minute:02d}" if dt.minute else ""
    if lang == "sw":
        period = ("asubuhi" if 5 <= h < 12 else "mchana" if h < 16
                  else "jioni" if h < 19 else "usiku")
        return f"{SW_DAYS[dt.weekday()]} {dt.day}/{dt.month} saa {(h - 6) % 12 or 12}{mins} {period}"
    return (f"{EN_DAYS[dt.weekday()]} {dt.day} {EN_MONTHS[dt.month - 1]}, "
            f"{h % 12 or 12}:{dt.minute:02d} {'AM' if h < 12 else 'PM'}")


def _short(name: str) -> str:
    name = (name or "").strip()
    return name if len(name) <= POINT_NAME_MAX else name[: POINT_NAME_MAX - 1] + "…"


def con(text: str) -> str:
    return f"CON {text}"


def end(text: str) -> str:
    return f"END {text}"


class Reply(Exception):
    """Raised to stop walking the path and send this screen."""

    def __init__(self, screen: str):
        super().__init__(screen)
        self.screen = screen


class Path:
    def __init__(self, text: str):
        self.tokens = [t.strip() for t in text.split("*")] if text else []
        self.i = 0

    def next(self) -> str | None:
        if self.i >= len(self.tokens):
            return None
        self.i += 1
        return self.tokens[self.i - 1]

    @property
    def at_newest(self) -> bool:
        """True when the token just read is the one the user entered this hop."""
        return self.i == len(self.tokens)


@dataclass
class Ctx:
    phone: str
    session_id: str
    lang: str
    backend: object
    store: object
    cache: dict = field(default_factory=dict)  # per-session, survives across hops


def _choose(path: Path, screen: str, valid, lang: str) -> str:
    """Read the next valid choice, or raise a CON prompt for `screen`."""
    invalid_last = False
    while True:
        token = path.next()
        if token is None:
            prefix = TEXT[lang]["invalid"] + "\n" if invalid_last else ""
            raise Reply(con(prefix + screen))
        if token in valid:
            return token
        invalid_last = path.at_newest


def _number(path: Path, screen: str, lang: str, lo: int = 1, hi: int = 100000) -> int:
    invalid_last = False
    while True:
        token = path.next()
        if token is None:
            prefix = TEXT[lang]["bad_number"] + "\n" if invalid_last else ""
            raise Reply(con(prefix + screen))
        if token.isdigit() and lo <= int(token) <= hi:
            return int(token)
        invalid_last = path.at_newest


def _pages(title: str, labels: list[str], more: str) -> list[str]:
    """Split a numbered list into screens that fit the USSD limit.

    Numbers run across pages (page 2 starts at 6, say), so a number always
    means the same item. Every page except the last ends with "98. More".
    """
    budget = MAX_SCREEN - len("CON ") - len(TEXT["sw"]["invalid"]) - 1
    footer = f"\n{MORE}. {more}"
    pages, current = [], title
    for n, label in enumerate(labels, start=1):
        line = f"\n{n}. {label}"
        if current != title and len(current) + len(line) + len(footer) > budget:
            pages.append(current + footer)
            current = title
        current += line
    pages.append(current)
    return pages


def _pick_from_list(path: Path, title: str, labels: list[str], lang: str) -> int:
    """Return the 0-based index chosen from a long, paged list."""
    pages = _pages(title, labels, TEXT[lang]["more"])
    valid = {str(n) for n in range(1, len(labels) + 1)}
    page = 0
    while True:
        more = {MORE} if page < len(pages) - 1 else set()
        token = _choose(path, pages[page], valid | more, lang)
        if token == MORE:
            page += 1
            continue
        return int(token) - 1


def _language(path: Path, ctx: Ctx, screen: str) -> str:
    choice = _choose(path, screen, LANGS, ctx.lang)
    lang = LANGS[choice]
    if path.at_newest:
        try:
            ctx.backend.set_language(ctx.phone, lang)
        except BackendError:
            raise Reply(end(TEXT[lang]["error"]))
        ctx.store.note_language(ctx.phone, lang)
    return lang


def _register(path: Path, ctx: Ctx) -> str:
    t = TEXT[ctx.lang]
    role = ROLES[_choose(path, t["role"], ROLES, ctx.lang)]

    counties = ctx.store.counties()
    if not counties:
        return end(t["no_wards"])
    county = counties[_pick_from_list(path, t["county"], counties, ctx.lang)]

    wards = ctx.store.wards(county)
    if not wards:
        return end(t["no_wards"])
    ward_id, _ = wards[_pick_from_list(path, t["ward"], [w for _, w in wards], ctx.lang)]

    if _choose(path, t["consent"], {"1", "2"}, ctx.lang) == "2":
        return end(t["declined"])
    ctx.backend.register(ctx.phone, ward_id=ward_id, role=role, lang=ctx.lang, consent=True)
    return end(t["registered"])


def _report(path: Path, ctx: Ctx) -> str:
    t = TEXT[ctx.lang]
    choice = _choose(path, t["report"], {"1", "2", "3", "4", "5"}, ctx.lang)
    if choice == "5":
        return end(t["other"])  # free text goes by SMS; nothing is sent from here
    if choice == "2":
        report_type = ANIMALS[_choose(path, t["animal_kind"], ANIMALS, ctx.lang)]
        count, unit = _number(path, t["animal_count"], ctx.lang), "animals"
    else:
        report_type, unit = REPORTS[choice]
        count = _number(path, t["bags_count"], ctx.lang) if report_type == "wet_bags" else None
    ctx.backend.report(ctx.phone, ctx.session_id, report_type, count, unit)
    return end(t["reported_bags"] if report_type == "wet_bags" else t["reported"])


def _status(ctx: Ctx) -> str:
    text = ctx.backend.ward_status(ctx.phone).strip()
    return end(text[: MAX_SCREEN - len("END ")] or TEXT[ctx.lang]["error"])


def _stop(path: Path, ctx: Ctx) -> str:
    t = TEXT[ctx.lang]
    if _choose(path, t["stop_confirm"], {"1", "2"}, ctx.lang) == "2":
        return end(t["kept"])
    ctx.backend.unsubscribe(ctx.phone)
    return end(t["stopped"])


def _drying(path: Path, ctx: Ctx) -> str:
    """Book soaked maize for drying, or confirm a slot the router offered.

    A request is a wet_bags report with the chosen collection point. The 18:00
    dryer router turns it into an OFFERED booking and SMS; the farmer can then
    confirm here (no inbound SMS needed).
    """
    t = TEXT[ctx.lang]
    info = ctx.cache.get("drying")
    if info is None:
        info = ctx.cache["drying"] = ctx.backend.drying(ctx.phone)
    if not info.get("registered"):
        return end(t["dry_register_first"])

    booking = info.get("booking") or {}
    slot = {"point": _short(booking.get("point_name", "")), "bags": booking.get("bags") or "",
            "when": format_when(booking.get("eta", ""), ctx.lang)}
    if booking.get("status") == "CONFIRMED":
        return end(t["dry_already"].format(**slot))
    if booking.get("status") == "OFFERED":
        fee = t["dry_fee"].format(fee=booking["fee_kes"]) if booking.get("fee_kes") else ""
        if _choose(path, t["dry_offer"].format(fee=fee, **slot), {"1", "2"}, ctx.lang) == "2":
            return end(t["dry_declined"])
        ctx.backend.confirm_booking(ctx.phone, booking["booking_id"])
        return end(t["dry_confirmed"].format(**slot))

    bags = _number(path, t["dry_bags"], ctx.lang, hi=1000)
    points = info.get("points") or []
    point = None
    if points:
        names = [_short(p["point_name"]) for p in points]
        point = points[_pick_from_list(path, t["dry_point"], names, ctx.lang)]
    at = t["dry_at"].format(point=_short(point["point_name"])) if point else ""
    if _choose(path, t["dry_ask"].format(bags=bags, at=at), {"1", "2"}, ctx.lang) == "2":
        return end(t["dry_cancel"])
    ctx.backend.report(ctx.phone, ctx.session_id, "wet_bags", bags, "bags",
                       point_id=point["point_id"] if point else None)
    return end(t["dry_requested"])


def _outlook_title(ctx: Ctx) -> str | None:
    """The ward's seasonal outlook as the menu-6 title (backend GET /ussd/seasonal-outlook), or None.

    Fetched once per session. No outlook, an unregistered phone or a backend error falls back to the
    static El Niño title, so the advice menu always works.
    """
    info = ctx.cache.get("seasonal")
    if info is None:
        try:
            info = ctx.backend.seasonal_outlook(ctx.phone)
        except BackendError:
            info = {}
        ctx.cache["seasonal"] = info
    probability, season = info.get("probability"), info.get("season")
    if probability is None or not season:
        return None
    return TEXT[ctx.lang]["outlook_title"].format(year=season.split()[-1], pct=round(probability * 100))


def _elnino(path: Path, ctx: Ctx) -> None:
    """Topic list -> tips, one per screen. Returns when the user picks 0 (main menu)."""
    t, content = TEXT[ctx.lang], ELNINO[ctx.lang]
    topics = content["topics"]
    menu = (_outlook_title(ctx) or content["title"]) + "".join(f"\n{n}. {label}" for n, (label, _) in enumerate(topics, 1))
    menu += f"\n0. {t['main_menu']}"
    valid = {"0"} | {str(n) for n in range(1, len(topics) + 1)}
    while True:
        choice = _choose(path, menu, valid, ctx.lang)
        if choice == "0":
            return
        tips = topics[int(choice) - 1][1]
        for i, tip in enumerate(tips):
            more = i < len(tips) - 1
            screen = tip + (f"\n1. {t['more']}" if more else "") + f"\n0. {t['back']}"
            if _choose(path, screen, {"0", "1"} if more else {"0"}, ctx.lang) == "0":
                break


def handle(text: str, ctx: Ctx, is_new: bool) -> str:
    """Return the full reply body (starting with CON or END) for this hop."""
    path = Path(text)
    try:
        if is_new:
            ctx.lang = _language(path, ctx, LANG_PROMPT)
        notice = ""
        while True:
            t = TEXT[ctx.lang]
            choice = _choose(path, notice + t["main"], {"1", "2", "3", "4", "5", "6", "7"}, ctx.lang)
            notice = ""
            if choice == "1":
                return _register(path, ctx)
            if choice == "2":
                return _report(path, ctx)
            if choice == "3":
                return _status(ctx)
            if choice == "4":
                ctx.lang = _language(path, ctx, LANG_PROMPT)
                notice = TEXT[ctx.lang]["lang_set"] + "\n"
                continue
            if choice == "5":
                return _stop(path, ctx)
            if choice == "6":
                _elnino(path, ctx)
            if choice == "7":
                return _drying(path, ctx)
    except Reply as r:
        return r.screen
    except BackendError:
        return end(TEXT[ctx.lang]["error"])
