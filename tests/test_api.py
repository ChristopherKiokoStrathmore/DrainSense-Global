import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAINSENSE_DB", str(tmp_path / "desk.sqlite"))
    monkeypatch.setenv("DRAINSENSE_SEED", "1")
    from api.app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_health_and_grid(client):
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["cells"] > 100
    grid = client.get("/api/v1/risk/grid").json()
    assert grid["type"] == "FeatureCollection"
    assert grid["features"]
    probs = [feat["properties"]["probability"] for feat in grid["features"]]
    assert all(0 <= p <= 1 for p in probs)


def test_cell_shap_and_derived_sector_language(client):
    hotspot = client.get("/api/v1/risk/hotspots?limit=1").json()["hotspots"][0]
    detail = client.get(f"/api/v1/risk/cells/{hotspot['cell_id']}").json()
    assert detail["data_origin"] == "model"
    assert detail["shap"]
    assert abs(sum(item["log_odds"] for item in detail["shap"]) + detail["expected_log_odds"]) >= 0
    sectors = detail["sectors"]
    assert "derived indicator" in sectors["agri_suppliers"]["kind"]
    assert "Not a yield forecast" in sectors["agri_suppliers"]["statement"]
    assert "Not a cholera" in sectors["health"]["statement"]
    assert "not a loss ratio" in sectors["insurers_logistics"]["statement"]
    page = client.get("/api/v1/sectors").json()
    assert page["kind"] == "derived indicators"


def test_synthetic_seed_and_community_origin(client):
    reports = client.get("/api/v1/reports").json()["reports"]
    assert reports
    assert all(row["origin"] == "synthetic" for row in reports)
    cell_id = reports[0]["cell_id"]
    created = client.post("/api/v1/reports", json={
        "cell_id": cell_id,
        "severity": "blocked",
        "note": "Culvert under the market road is silted.",
        "reporter": "Amina",
        "origin": "synthetic",
    })
    assert created.status_code == 201
    assert created.json()["origin"] == "community"
    missing = client.post("/api/v1/reports", json={
        "cell_id": "n90_e90",
        "severity": "blocked",
        "note": "nowhere",
        "reporter": "Amina",
    })
    assert missing.status_code == 404


def test_effectiveness_matches_the_incident_log(client):
    payload = client.get("/api/v1/interventions/effectiveness").json()
    assert payload["repairs"]
    repair = payload["repairs"][0]
    assert repair["origin"] == "synthetic"
    incidents = client.get("/api/v1/incidents").json()["incidents"]
    cell_incidents = [row for row in incidents if row["cell_id"] == repair["cell_id"]]
    before = sum(
        1 for row in cell_incidents
        if _shift(repair["completed_on"], -365) <= row["occurred_on"] < repair["completed_on"]
    )
    after = sum(
        1 for row in cell_incidents
        if repair["completed_on"] <= row["occurred_on"] < _shift(repair["completed_on"], 365)
    )
    assert repair["incidents_365d_before"] == before
    assert repair["incidents_365d_after"] == after
    assert before > after  # the drill repair is the only one, and the log drops after it


def test_response_plan_uses_model_and_open_reports(client):
    plan = client.get("/api/v1/response/plan?limit=15").json()
    priorities = [item["priority"] for item in plan["plan"]]
    assert priorities == sorted(priorities, reverse=True)
    top = plan["plan"][0]
    parts = top["components"]
    assert parts["model_probability"]["origin"] == "model"
    assert parts["open_reports"]["origin"] == "database"
    expected = (
        parts["model_probability"]["points"]
        + parts["open_reports"]["points"]
        + parts["recurring_incidents"]["points"]
    )
    assert abs(top["priority"] - round(expected, 3)) < 1e-6


def test_metrics_endpoint_is_the_committed_file(client):
    body = client.get("/api/v1/metrics").json()
    assert body["temporal_test"]["roc_auc"] is not None
    assert body["dummy_prior"]["brier"] is not None
    assert "limitations" in body and len(body["limitations"]) >= 5


def _shift(day: str, days: int) -> str:
    from datetime import date, timedelta
    year, month, number = (int(part) for part in day.split("-"))
    return (date(year, month, number) + timedelta(days=days)).isoformat()


def test_seed_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAINSENSE_DB", str(tmp_path / "empty.sqlite"))
    monkeypatch.setenv("DRAINSENSE_SEED", "0")
    # A second TestClient on the same app object re-runs lifespan.
    from api.app.main import app

    with TestClient(app) as test_client:
        assert test_client.get("/api/v1/reports").json()["reports"] == []
    os.environ.pop("DRAINSENSE_SEED", None)
