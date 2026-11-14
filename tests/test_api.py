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


def test_cells_carry_place_names_not_grid_codes(client):
    grid = client.get("/api/v1/risk/grid").json()
    names = {f["properties"]["cell_id"]: f["properties"]["place_name"] for f in grid["features"]}
    assert all(names.values())
    assert names["s45_w75"] == "Los Lagos Region, Chile"
    detail = client.get("/api/v1/risk/cells/s45_w75").json()
    assert detail["place_name"] == "Los Lagos Region, Chile"
    assert detail["country_code"] == "CL"
    plan = client.get("/api/v1/response/plan?limit=3").json()["plan"]
    assert all(item["place_name"] for item in plan)


def test_place_name_falls_back_to_coordinates():
    from api.app import scoring

    assert scoring._coords(-42.5, -72.5) == "42.5°S 72.5°W"
    assert scoring._coords(2.5, 12.5) == "2.5°N 12.5°E"


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


def test_timeline_scores_every_year_with_the_map_model(client):
    body = client.get("/api/v1/risk/timeline").json()
    years = body["years"]
    assert years[0] == 2014 and years[-1] == body["outlook_year"] == 2024
    grid = {f["properties"]["cell_id"]: f["properties"]["probability"] for f in client.get("/api/v1/risk/grid").json()["features"]}
    assert set(body["cells"]) == set(grid)
    # The outlook column is the map: same model, same inputs.
    for cell_id, probability in grid.items():
        assert abs(body["cells"][cell_id]["probability"][-1] - probability) < 1e-5
        assert body["cells"][cell_id]["flooded"][-1] is None
    # Test-year means are the ones in the committed metrics card.
    means = {row["year"]: row["mean_predicted_probability"] for row in client.get("/api/v1/metrics").json()["temporal_test"]["by_year"]}
    summary = {row["year"]: row for row in body["summary"]}
    for year, mean in means.items():
        assert abs(summary[year]["mean_probability"] - mean) < 1e-5
        assert summary[year]["phase"] == "test"
        row = summary[year]
        assert row["hits"] + row["missed"] == row["recorded_flood_cells"]
    assert summary[2024]["phase"] == "outlook" and summary[2024]["recorded_flood_cells"] is None


def _fake_forecast(monkeypatch, rain_for):
    """Replace the Open-Meteo call: rain_for(cell) gives 16 daily totals from today."""
    from datetime import timedelta

    from api.app import forecast

    def fetch(cells):
        start = forecast.today()
        dates = [(start + timedelta(days=i)).isoformat() for i in range(16)]
        return {c["cell_id"]: {"dates": dates, "rain": rain_for(c)} for c in cells}

    monkeypatch.setattr(forecast, "_fetch_rows", fetch)
    monkeypatch.setitem(forecast._cache, "rows", None)


def test_forecast_window_is_capped_at_two_weeks(client, monkeypatch):
    from datetime import timedelta

    from api.app import forecast

    _fake_forecast(monkeypatch, lambda c: [0.0] * 16)
    today = forecast.today()
    ok = client.get(f"/api/v1/risk/forecast?until={(today + timedelta(days=14)).isoformat()}")
    assert ok.status_code == 200
    assert ok.json()["days"] == 15
    assert client.get(f"/api/v1/risk/forecast?until={(today + timedelta(days=15)).isoformat()}").status_code == 422
    assert client.get(f"/api/v1/risk/forecast?until={(today - timedelta(days=1)).isoformat()}").status_code == 422
    default = client.get("/api/v1/risk/forecast").json()
    assert default["until"] == default["latest_until"] == (today + timedelta(days=14)).isoformat()


def test_forecast_levels_follow_the_rule(client, monkeypatch):
    _fake_forecast(monkeypatch, lambda c: [60.0 if c["cell_id"] == "s45_w75" else 0.0] * 16)
    body = client.get("/api/v1/risk/forecast").json()
    by_id = {c["cell_id"]: c for c in body["cells"]}
    wet = by_id["s45_w75"]  # top-ranked cell, far above the threshold, with 60 mm every day
    assert wet["level"] == "likely"
    assert wet["heavy_days"] == 15
    assert body["cells"][0]["level"] == "likely"
    dry = [c for c in body["cells"] if c["cell_id"] != "s45_w75"]
    assert all(c["level"] == "quiet" for c in dry)
    assert body["counts"] == {"likely": 1, "watch": 0, "quiet": len(dry)}
    # Only days inside the window count.
    short = client.get(f"/api/v1/risk/forecast?until={body['issued']}").json()
    assert {c["cell_id"]: c for c in short["cells"]}["s45_w75"]["window_total_mm"] == 60.0
