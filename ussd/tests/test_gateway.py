"""Africa's Talking is the default USSD gateway; Wasiliana stays available with USSD_PROVIDER."""
import pytest

import gateway
from backend import DevBackend
from callback import create_app
from store import MemoryStore

# What Africa's Talking posts (form-encoded) to the USSD channel callback URL.
AT_FIRST_HOP = (b"sessionId=ATUid_6ab2c1f0e9d84f0b&serviceCode=%2A384%2A1234%23&phoneNumber=%2B254720523299"
                b"&networkCode=63902&text=")


def test_default_provider_is_africastalking(monkeypatch):
    monkeypatch.delenv("USSD_PROVIDER", raising=False)
    assert gateway.provider() == "africastalking"
    assert gateway.text_mode() == "cumulative"


def test_parse_africastalking_payload(monkeypatch):
    monkeypatch.delenv("USSD_PROVIDER", raising=False)
    req = gateway.parse("application/x-www-form-urlencoded", AT_FIRST_HOP)
    assert (req.session_id, req.phone, req.text, req.service_code) == (
        "ATUid_6ab2c1f0e9d84f0b", "+254720523299", "", "*384*1234#")
    later = gateway.parse("application/x-www-form-urlencoded",
                          AT_FIRST_HOP.replace(b"text=", b"text=1%2A2%2A3"))
    assert later.text == "1*2*3"


def test_africastalking_ignores_wasiliana_overrides(monkeypatch):
    monkeypatch.delenv("USSD_PROVIDER", raising=False)
    monkeypatch.setenv("WASILIANA_FIELD_PHONE", "MSISDN")
    monkeypatch.setenv("WASILIANA_TEXT_MODE", "latest")
    assert gateway.parse("application/x-www-form-urlencoded", AT_FIRST_HOP).phone == "+254720523299"
    assert gateway.text_mode() == "cumulative"


def test_missing_fields(monkeypatch):
    monkeypatch.delenv("USSD_PROVIDER", raising=False)
    with pytest.raises(gateway.BadRequest):
        gateway.parse("application/x-www-form-urlencoded", b"msisdn=0720523299&text=")


def test_wasiliana_still_selectable(monkeypatch):
    monkeypatch.setenv("USSD_PROVIDER", "wasiliana")
    monkeypatch.setenv("WASILIANA_FIELD_PHONE", "MSISDN")
    req = gateway.parse("application/json", b'{"sessionId":"w1","MSISDN":"0720523299","text":"1"}')
    assert (req.phone, req.text) == ("+254720523299", "1")


def test_full_session_as_africastalking_sends_it(monkeypatch):
    monkeypatch.delenv("USSD_PROVIDER", raising=False)
    client = create_app(backend=DevBackend(), store=MemoryStore()).test_client()
    first = client.post("/ussd", data=AT_FIRST_HOP, content_type="application/x-www-form-urlencoded")
    assert first.status_code == 200 and first.mimetype == "text/plain"
    assert first.get_data(as_text=True) == "CON Chagua lugha / Choose language\n1. Kiswahili\n2. English"
    second = client.post("/ussd", data=AT_FIRST_HOP.replace(b"text=", b"text=2"),
                         content_type="application/x-www-form-urlencoded")
    assert second.get_data(as_text=True).startswith("CON MvuaSmart\n1. Register")
    assert client.get("/healthz").get_data(as_text=True) == "ok africastalking"
