import pytest

from backend import BackendError, DevBackend
from callback import create_app
from store import MemoryStore

PHONE = "+254712345678"
# Fixed list so tests don't depend on edits to db/seed/cooperatives.csv.
# Sorted on screen: 1 Ainabkoi FCS, 2 Moiben FCS, 3 Tuiyo FCS, 4 Other.
TEST_COOPS = [(4, "Tuiyo FCS"), (3, "Moiben FCS"), (1, "Ainabkoi FCS")]


class FailingBackend(DevBackend):
    def register(self, *a, **kw):
        raise BackendError("500 INTERNAL")


@pytest.fixture
def env():
    backend, store = DevBackend(), MemoryStore(coops=TEST_COOPS)
    client = create_app(backend=backend, store=store).test_client()

    def hop(text, session="s1", phone=PHONE):
        return client.post("/ussd", data={"sessionId": session, "phoneNumber": phone,
                                          "text": text}).get_data(as_text=True)
    return hop, backend, store


def walk(hop, *inputs, session="s1"):
    """Send each cumulative path in turn, like a phone does; return the last screen."""
    text, screen = "", hop("", session)
    for entry in inputs:
        text = f"{text}*{entry}" if text else entry
        screen = hop(text, session)
    return screen


def test_new_number_gets_exact_language_prompt(env):
    hop, _, _ = env
    assert hop("") == "CON Chagua lugha / Choose language\n1. Kiswahili\n2. English"


def test_language_choice_saved_once_then_main_menu(env):
    hop, backend, _ = env
    assert walk(hop, "2").startswith("CON MvuaSmart\n1. Register")
    assert hop("2*2").startswith("CON What do you see?")  # replay must not call /language again
    assert [c for c, _ in backend.calls].count("language") == 1


def test_returning_number_skips_language_prompt(env):
    hop, _, store = env
    store.note_language(PHONE, "sw")
    assert hop("", session="s9").startswith("CON MvuaSmart\n1. Jiandikishe")


def test_full_registration_in_swahili(env):
    hop, backend, _ = env
    # Kiswahili, Jiandikishe, Mkulima, cooperative 3 (Tuiyo FCS), membership no., Ndiyo
    assert walk(hop, "1", "1", "1") == ("CON Chagua chama chako cha ushirika:\n1. Ainabkoi FCS\n"
                                       "2. Moiben FCS\n3. Tuiyo FCS\n4. Chama kingine")
    assert hop("1*1*1*3") == "CON Weka namba yako ya uanachama:"
    assert hop("1*1*1*3*tf-0042").startswith("CON Tuiyo FCS, namba TF-0042.\nUkubali kupokea")
    assert hop("1*1*1*3*tf-0042*1") == "END Asante! Umesajiliwa."
    assert backend.calls[-1] == ("register", {"phone": PHONE, "coop_id": 4, "membership_no": "TF-0042",
                                              "role": "farmer", "preferred_lang": "sw", "consent": True})


def test_declined_consent_does_not_register(env):
    hop, backend, _ = env
    assert walk(hop, "1", "1", "1", "2", "1", "2") == "END Sawa, hujasajiliwa. Piga tena wakati wowote."
    assert "register" not in [c for c, _ in backend.calls]


def test_invalid_choice_reprompts_same_screen(env):
    hop, _, _ = env
    assert walk(hop, "1", "9").startswith("CON Chaguo si sahihi.\nMvuaSmart")
    # the bad input is skipped, so the next valid input still works
    assert hop("1*9*2").startswith("CON Unaona nini?")


def test_wet_bags_report(env):
    hop, backend, _ = env
    assert walk(hop, "2", "2", "1", "20") == "END Thank you! Report received. To book a dryer, choose 7 on the main menu."
    assert backend.calls[-1] == ("report", {"phone": PHONE, "session_id": "s1",
                                            "report_type": "wet_bags", "count": 20, "unit": "bags"})


def test_dead_animals_report_and_bad_number(env):
    hop, backend, _ = env
    assert walk(hop, "1", "2", "2", "2", "abc").startswith("CON Weka namba sahihi.")
    assert hop("1*2*2*2*abc*3").startswith("END Asante!")
    assert backend.calls[-1][1]["report_type"] == "dead_animals"
    assert backend.calls[-1][1]["count"] == 3


def test_flooding_report_has_no_count(env):
    hop, backend, _ = env
    walk(hop, "1", "2", "3")
    assert backend.calls[-1][1] == {"phone": PHONE, "session_id": "s1",
                                         "report_type": "flooding", "count": None, "unit": None}


def test_other_report_asks_for_sms_without_calling_backend(env):
    hop, backend, _ = env
    assert walk(hop, "1", "2", "5").startswith("END Tafadhali tutumie SMS")
    assert "report" not in [c for c, _ in backend.calls]


def test_ward_status(env):
    hop, _, _ = env
    assert walk(hop, "2", "3") == "END Your ward: no risk score today."


def test_change_language_mid_session(env):
    hop, backend, _ = env
    assert walk(hop, "1", "4", "2").startswith("CON Language: English\nMvuaSmart\n1. Register")
    assert hop("1*4*2*3") == "END Your ward: no risk score today."
    assert [kw["preferred_lang"] for c, kw in backend.calls if c == "language"] == ["sw", "en"]


def test_unsubscribe(env):
    hop, backend, _ = env
    assert walk(hop, "1", "5", "1") == "END Umeondolewa. Piga tena ili kujiandikisha upya."
    assert backend.calls[-1][0] == "unsubscribe"


def test_backend_error_becomes_end_screen_in_user_language():
    store = MemoryStore()
    store.note_language(PHONE, "en")
    client = create_app(backend=FailingBackend(), store=store).test_client()
    body = client.post("/ussd", data={"sessionId": "e1", "phoneNumber": PHONE,
                                      "text": "1*1*2*1*1"}).get_data(as_text=True)
    assert body == "END Sorry, please try again."


def test_session_row_upserted_each_hop(env):
    hop, _, store = env
    walk(hop, "1", "2")
    assert store.sessions["s1"]["last_text"] == "1*2"
    assert store.sessions["s1"]["phone"] == PHONE


def test_screens_fit_ussd_limit():
    import menus
    for lang in menus.TEXT.values():
        for key in ("main", "role", "consent", "report", "stop_confirm"):
            assert len("CON " + lang["invalid"] + "\n" + lang[key]) <= menus.MAX_SCREEN, key
    from advice import ELNINO
    for code, content in ELNINO.items():
        t = menus.TEXT[code]
        worst = f"CON {t['invalid']}\n"
        menu = content["title"] + "".join(f"\n{n}. {l}" for n, (l, _) in enumerate(content["topics"], 1))
        assert len(worst + menu + f"\n0. {t['main_menu']}") <= menus.MAX_SCREEN, code
        for _, tips in content["topics"]:
            for tip in tips:
                screen = worst + tip + f"\n1. {t['more']}\n0. {t['back']}"
                assert len(screen) <= menus.MAX_SCREEN, (len(screen), tip)
    pages = menus._pages("Chagua kata:", [f"Kata ndefu sana {i}" for i in range(40)], "Zaidi")
    assert len(pages) > 1 and all(len("CON " + p) <= menus.MAX_SCREEN for p in pages)


def test_advice_topics_match_across_languages():
    from advice import ELNINO
    sw, en = ELNINO["sw"]["topics"], ELNINO["en"]["topics"]
    assert [len(t) for _, t in sw] == [len(t) for _, t in en]


def test_elnino_topics_and_tips(env):
    hop, backend, _ = env
    assert walk(hop, "2", "6").startswith("CON El Niño: heavy rain is coming. Choose:\n1. Planting")
    screen = hop("2*6*2")
    assert screen.startswith("CON If your crop is mature, harvest now.") and screen.endswith("1. More\n0. Back")
    assert hop("2*6*2*1").startswith("CON Dry grain fully")
    last = hop("2*6*2*1*1*1")
    assert "Main menu > 7" in last and "1. More" not in last
    assert backend.calls == [("language", {"phone": PHONE, "preferred_lang": "en"})]  # advice is static


def test_elnino_back_navigation(env):
    hop, _, _ = env
    walk(hop, "1", "6", "4")
    assert hop("1*6*4*0").startswith("CON El Niño: mvua kubwa")       # tip -> topics
    assert hop("1*6*4*0*0").startswith("CON MvuaSmart\n1. Jiandikishe")  # topics -> main menu
    assert hop("1*6*4*0*0*3").startswith("END")                         # main menu still works


# --- Drying booking (menu 7) ---

REG = ("1", "1", "1", "2", "1", "1")  # Kiswahili, register as farmer in Kapseret, consent


def test_format_when():
    import menus
    iso = "2026-10-20T08:00:00+03:00"  # a Tuesday
    assert menus.format_when(iso, "sw") == "Jumanne 20/10 saa 2 asubuhi"
    assert menus.format_when(iso, "en") == "Tue 20 Oct, 8:00 AM"
    assert menus.format_when("2026-10-20T19:30:00+03:00", "sw") == "Jumanne 20/10 saa 1:30 usiku"
    assert menus.format_when("2026-10-20T06:00:00+03:00", "sw") == "Jumanne 20/10 saa 12 asubuhi"
    assert menus.format_when("2026-10-20T13:00:00+03:00", "en") == "Tue 20 Oct, 1:00 PM"
    assert menus.format_when("not a date", "en") == "not a date"


def test_drying_needs_registration(env):
    hop, _, _ = env
    assert walk(hop, "1", "7") == "END Jiandikishe kwanza ili kuomba mashine ya kukausha: Menyu kuu > 1."


def test_drying_request_then_offer_then_confirm(env):
    hop, backend, _ = env
    walk(hop, *REG, session="reg")

    # Request: bags -> collection point -> confirm
    assert walk(hop, "7", session="d1") == "CON Magunia mangapi ya mahindi yamelowa? Weka namba:"
    assert hop("7*15", session="d1").startswith("CON Chagua mahali pa kupeleka magunia:\n1. Moiben market")
    assert hop("7*15*2", session="d1").startswith("CON Omba kukausha magunia 15 kwenye Kapseret coop store?")
    assert hop("7*15*2*1", session="d1").startswith("END Ombi limepokelewa!")
    assert backend.calls[-1] == ("report", {"phone": PHONE, "session_id": "d1", "report_type": "wet_bags",
                                            "count": 15, "unit": "bags", "point_id": 4})
    drying_reads = [c for c, _ in backend.calls if c == "drying"]
    assert len(drying_reads) == 1  # fetched once per session, reused on replays

    # The router offered a slot: next session shows it and confirms
    offer = walk(hop, "7", session="d2")
    assert offer.startswith("CON Mashine ya kukausha itafika Kapseret coop store ")
    assert "saa 2 asubuhi. Magunia 15, ada KES 900." in offer and offer.endswith("1. Thibitisha\n2. Hapana")
    assert hop("7*1", session="d2").startswith("END Umethibitisha! Peleka magunia 15 Kapseret coop store")
    assert backend.calls[-1][0] == "drying-confirm"

    # Already confirmed
    assert walk(hop, "7", session="d3").startswith("END Nafasi yako imethibitishwa: Kapseret coop store,")


def test_drying_cancel_and_decline_make_no_writes(env):
    hop, backend, _ = env
    walk(hop, *REG, session="reg")
    assert walk(hop, "7", "10", "1", "2", session="c1") == "END Sawa, ombi halijatumwa."
    backend.bookings[PHONE] = {"booking_id": 9, "status": "OFFERED", "point_name": "Moiben market",
                               "eta": "2026-10-20T08:00:00+03:00", "bags": 10, "fee_kes": None}
    offer = walk(hop, "7", session="c2")
    assert "Magunia 10.\n" in offer  # no fee line when the backend has no fee
    assert hop("7*2", session="c2") == "END Sawa, hujathibitisha. Piga tena ukibadilisha nia."
    assert not any(c in ("drying-confirm",) for c, _ in backend.calls)
    assert sum(1 for c, kw in backend.calls if c == "report") == 0


def test_drying_without_points_skips_point_choice(env):
    hop, backend, _ = env
    walk(hop, *REG, session="reg")
    backend.points = []
    assert walk(hop, "7", "5", session="p1") == "CON Omba kukausha magunia 5?\n1. Ndiyo\n2. Hapana"
    hop("7*5*1", session="p1")
    assert "point_id" not in backend.calls[-1][1]


def test_drying_backend_error_ends_politely():
    class Down(DevBackend):
        def drying(self, phone):
            raise BackendError("502 UPSTREAM_ERROR")
    store = MemoryStore()
    store.note_language(PHONE, "en")
    client = create_app(backend=Down(), store=store).test_client()
    body = client.post("/ussd", data={"sessionId": "x", "phoneNumber": PHONE, "text": "7"}).get_data(as_text=True)
    assert body == "END Sorry, please try again."


def test_drying_screens_fit_with_long_names():
    import menus
    long = "Kapsoya Primary School Collection Point Number Two"
    when = {"sw": menus.format_when("2026-10-22T16:30:00+03:00", "sw"),
            "en": menus.format_when("2026-10-22T16:30:00+03:00", "en")}
    for code, t in menus.TEXT.items():
        point = menus._short(long)
        screens = [
            menus.con(t["invalid"] + "\n" + t["dry_offer"].format(point=point, when=when[code], bags=999,
                                                                 fee=t["dry_fee"].format(fee=59940))),
            menus.con(t["invalid"] + "\n" + t["dry_ask"].format(bags=999, at=t["dry_at"].format(point=point))),
            menus.end(t["dry_confirmed"].format(point=point, when=when[code], bags=999)),
            menus.end(t["dry_already"].format(point=point, when=when[code], bags=999)),
            menus.end(t["dry_requested"]), menus.end(t["reported_bags"]),
            menus.con(t["invalid"] + "\n" + t["main"]),
        ]
        for screen in screens:
            assert len(screen) <= menus.MAX_SCREEN, (len(screen), screen)


def test_reused_session_id_with_other_phone_does_not_share_cache(env):
    hop, backend, store = env
    walk(hop, *REG, session="reg")
    backend.bookings[PHONE] = {"booking_id": 1, "status": "CONFIRMED", "point_name": "Moiben market",
                               "eta": "2026-10-20T08:00:00+03:00", "bags": 5, "fee_kes": 300}
    assert walk(hop, "7", session="same").startswith("END Nafasi yako imethibitishwa")
    other = "+254799999999"
    store.note_language(other, "en")
    assert hop("7", session="same", phone=other) == "END Please register first to book a dryer: Main menu > 1."


# --- Cooperative registration ---

def test_registration_other_cooperative_skips_membership(env):
    hop, backend, _ = env
    assert walk(hop, "2", "1", "3", "4").startswith("CON Agree to receive")  # no member no. asked
    assert hop("2*1*3*4*1") == "END Thank you! You are registered."
    body = backend.calls[-1][1]
    assert body["coop_id"] is None and body["membership_no"] is None and body["role"] == "both"


def test_invalid_membership_number_reprompts(env):
    hop, _, _ = env
    assert walk(hop, "2", "1", "1", "1", "#12!") == ("CON Invalid number. Use letters and digits only.\n"
                                                    "Enter your membership number:")
    assert hop("2*1*1*1*#12!*5531").startswith("CON Ainabkoi FCS, member no. 5531.")


def test_long_cooperative_list_pages_with_98():
    store = MemoryStore()  # the real seed list
    client = create_app(backend=DevBackend(), store=store).test_client()
    post = lambda t: client.post("/ussd", data={"sessionId": "pg", "phoneNumber": PHONE,
                                                "text": t}).get_data(as_text=True)
    post(""); post("2")
    first = post("2*1*1")
    assert "98. More" in first
    n = len(store.cooperatives())
    text = "2*1*1"
    while "98. More" in post(text):
        text += "*98"
    assert f"{n + 1}. Other / not listed" in post(text)
    assert post(f"{text}*{n}") == "CON Enter your membership number:"  # last cooperative on last page


def test_seed_cooperatives_csv_is_valid():
    import csv
    from store import COOP_CSV
    rows = list(csv.DictReader(open(COOP_CSV, encoding="utf-8")))
    assert rows and len({r["coop_id"] for r in rows}) == len(rows)
    for r in rows:
        assert r["county"] and r["source_url"].startswith("https://")
        assert len(r["short_name"]) <= 24
        assert r["location_basis"] in ("STATED", "INFERRED", "CONFIRMED")


def test_consent_with_member_summary_fits():
    import menus
    for t in menus.TEXT.values():
        screen = menus.con(t["invalid"] + "\n" + t["member_summary"].format(coop="X" * 24, no="9" * 20)
                           + t["consent"])
        assert len(screen) <= menus.MAX_SCREEN, len(screen)
