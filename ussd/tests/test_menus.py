import pytest

from backend import BackendError, DevBackend
from callback import create_app
from store import MemoryStore

PHONE = "+254712345678"


class FailingBackend(DevBackend):
    def register(self, *a, **kw):
        raise BackendError("500 INTERNAL")


@pytest.fixture
def env():
    backend, store = DevBackend(), MemoryStore()
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
    # Kiswahili, Jiandikishe, Mkulima, county 2 (Uasin Gishu), ward 1 (Kapseret), Ndiyo
    screen = walk(hop, "1", "1", "1", "2", "1", "1")
    assert screen == "END Asante! Umesajiliwa."
    name, body = backend.calls[-1]
    assert name == "register"
    assert body["role"] == "farmer" and body["preferred_lang"] == "sw" and body["consent"] is True
    assert body["ward_id"] == 2


def test_declined_consent_does_not_register(env):
    hop, backend, _ = env
    assert walk(hop, "1", "1", "1", "2", "1", "2") == "END Sawa, hujasajiliwa. Piga tena wakati wowote."
    assert "register" not in [c for c, _ in backend.calls]


def test_invalid_choice_reprompts_same_screen(env):
    hop, _, _ = env
    assert walk(hop, "1", "7").startswith("CON Chaguo si sahihi.\nMvuaSmart")
    # the bad input is skipped, so the next valid input still works
    assert hop("1*7*2").startswith("CON Unaona nini?")


def test_wet_bags_report(env):
    hop, backend, _ = env
    assert walk(hop, "2", "2", "1", "20").startswith("END Thank you! Your report")
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
    assert "Main menu > 2 > 1" in last and "1. More" not in last
    assert backend.calls == [("language", {"phone": PHONE, "preferred_lang": "en"})]  # advice is static


def test_elnino_back_navigation(env):
    hop, _, _ = env
    walk(hop, "1", "6", "4")
    assert hop("1*6*4*0").startswith("CON El Niño: mvua kubwa")       # tip -> topics
    assert hop("1*6*4*0*0").startswith("CON MvuaSmart\n1. Jiandikishe")  # topics -> main menu
    assert hop("1*6*4*0*0*3").startswith("END")                         # main menu still works
