"""End-to-end: the real USSD app against a running backend (API_CONTRACT §5), no stubs.

Skipped unless NGAO_E2E_BACKEND_URL is set. CI starts the backend on SQLite with the synthetic
demo data (`manage.py seed_reference --templates && manage.py seed_demo`) and runs:

    NGAO_E2E_BACKEND_URL=http://127.0.0.1:8000/ngao/v1 NGAO_E2E_API_TOKEN=... \
    NGAO_E2E_USSD_TOKEN=... pytest tests/test_backend_e2e.py
"""
import datetime as dt
import os
import uuid

import pytest
import requests

from backend import BackendClient
from callback import create_app
from store import MemoryStore

BASE = os.getenv("NGAO_E2E_BACKEND_URL", "").rstrip("/")
pytestmark = pytest.mark.skipif(not BASE, reason="NGAO_E2E_BACKEND_URL not set")


@pytest.fixture(scope="module")
def api():
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {os.environ['NGAO_E2E_API_TOKEN']}"
    return session


@pytest.fixture(scope="module")
def store(api):
    """One test cooperative in Kapseret, using the backend's real ward id.

    Production reads cooperatives (and their ward_id) from COOPERATIVES in the same database.
    """
    items = api.get(f"{BASE}/wards", timeout=10).json()["items"]
    kapseret = next(w["ward_id"] for w in items if w["county"] == "Uasin Gishu" and w["ward_name"] == "Kapseret")
    return MemoryStore(coops=[(990, "E2E Kapseret Coop", kapseret)])


@pytest.fixture(scope="module")
def client(store):
    backend = BackendClient(BASE, os.environ["NGAO_E2E_USSD_TOKEN"], timeout=10)
    return create_app(backend=backend, store=store).test_client()


def walk(client, phone, *inputs):
    """One USSD session with cumulative text; returns every screen."""
    session_id, text, screens = uuid.uuid4().hex, "", []
    for i, value in enumerate(("",) + inputs):
        text = "" if i == 0 else value if i == 1 else f"{text}*{value}"
        response = client.post("/ussd", data={"sessionId": session_id, "phoneNumber": phone,
                                              "serviceCode": "*384#", "text": text})
        assert response.status_code == 200
        screens.append(response.get_data(as_text=True))
    return screens


def test_farmer_journey(client, store, api):
    phone = f"+2547{uuid.uuid4().int % 10**8:08d}"
    assert walk(client, phone, "2")[-1].startswith("CON MvuaSmart")

    # Register, farmer, cooperative 1 (E2E Kapseret Coop), membership number, consent
    assert walk(client, phone, "1", "1", "1", "E2E-001", "1")[-1] == "END Thank you! You are registered."

    assert walk(client, phone, "3")[-1].startswith("END Kapseret ward:")
    assert walk(client, phone, "2", "1", "25")[-1].startswith("END Thank you! Report received.")
    assert walk(client, phone, "7", "40", "1", "1")[-1].startswith("END Request received!")

    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    plan = api.post(f"{BASE}/jobs/plan-routes", json={"route_date": tomorrow}, timeout=30)
    assert plan.status_code == 202, plan.text

    offer = walk(client, phone, "7", "1")
    assert "Kapseret coop store" in offer[-2] and "40 bags" in offer[-2]
    assert offer[-1].startswith("END Confirmed!")
    assert walk(client, phone, "7")[-1].startswith("END Your drying slot is confirmed")

    assert walk(client, phone, "5", "1")[-1].startswith("END You have been unsubscribed")
