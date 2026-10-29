import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.app import impact


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DRAINSENSE_DB", str(tmp_path / "desk.sqlite"))
    monkeypatch.setenv("DRAINSENSE_SEED", "0")
    from api.app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def kenya_2023():
    """Event 5477, Kenya Oct-Nov 2023, as the API takes it."""
    table = pd.read_csv(impact.IMPACT_DIR / "event_features.csv", keep_default_na=False)
    row = table[table.Event_ID == 5477].iloc[0]
    body = {
        "start_date": row.Start_Date,
        "end_date": row.End_Date,
        "area_km2": float(row.Area_km2),
        "severity": float(row.Severity),
        "latitude": float(row.Footprint_Lat),
        "longitude": float(row.Footprint_Lon),
        "continent_region": row.Continent_Region,
        "main_cause": row.Main_Cause,
    }
    for field, column in impact.WEATHER_FIELDS.items():
        body[field] = float(row[column])
    return body, float(row.Flood_Impact_Index)


def test_model_card_lists_variants_inputs_and_caveats(client):
    card = client.get("/api/v1/impact/model").json()
    assert set(card["variants"]) == {"brief", "no_size"}
    assert "area_km2" in card["inputs"]["brief"]["required"]
    assert "area_km2" not in card["inputs"]["no_size"]["required"]
    assert "severity" not in card["inputs"]["brief"]["required"]
    assert card["data_checks"]["distinct_event_coordinates"] == 376
    assert card["data_checks"]["observed"]["distinct_footprint_centres"] > 5000
    assert "Open-Meteo" in card["limitations"][1]


def test_brief_prediction_carries_the_exact_formula(client, kenya_2023):
    body, index = kenya_2023
    out = client.post("/api/v1/impact/predict", json=body).json()
    assert out["variant"] == "brief"
    assert out["dfo_formula"]["rounded"] == index
    assert abs(out["flood_impact_index"] - index) < 0.5
    assert out["model_minus_formula"] == pytest.approx(out["flood_impact_index"] - out["dfo_formula"]["value"], abs=1e-3)
    # CatBoost SHAP values add up to the prediction.
    total = out["expected_value"] + sum(item["contribution"] for item in out["contributions"])
    assert total == pytest.approx(out["flood_impact_index"], abs=2e-3)
    assert out["contributions"][0]["feature"] == "Log_Area"


def test_no_size_needs_no_area_duration_or_severity(client, kenya_2023):
    body, _ = kenya_2023
    slim = {k: v for k, v in body.items() if k not in ("area_km2", "end_date", "severity")}
    out = client.post("/api/v1/impact/predict?variant=no_size", json=slim)
    assert out.status_code == 200
    result = out.json()
    assert result["dfo_formula"] is None
    features = {item["feature"] for item in result["contributions"]}
    assert "Log_Area" not in features and "Cause_Group" in features
    assert "early estimate" in result["reading"]


def test_missing_and_unknown_inputs_are_refused(client, kenya_2023):
    body, _ = kenya_2023
    no_area = {k: v for k, v in body.items() if k != "area_km2"}
    missing = client.post("/api/v1/impact/predict", json=no_area)
    assert missing.status_code == 422
    assert "area_km2" in missing.json()["detail"]
    place = client.post("/api/v1/impact/predict", json={**body, "continent_region": "Atlantis"})
    assert place.status_code == 422
    assert "continent_region" in place.json()["detail"]
    assert client.post("/api/v1/impact/predict", json={**body, "severity": 3}).status_code == 422
    backwards = {**body, "end_date": "2023-10-01"}
    assert client.post("/api/v1/impact/predict", json=backwards).status_code == 422
    assert client.post("/api/v1/impact/predict?variant=xgb", json=body).status_code == 422


def test_batch_scores_each_event_and_names_the_bad_one(client, kenya_2023):
    body, _ = kenya_2023
    ok = client.post("/api/v1/impact/predict/batch", json={"events": [body, {**body, "area_km2": 10.0}]}).json()
    assert ok["count"] == 2
    assert ok["results"][1]["flood_impact_index"] < ok["results"][0]["flood_impact_index"]
    bad = client.post("/api/v1/impact/predict/batch", json={"events": [body, {**body, "area_km2": None}]})
    assert bad.status_code == 422
    assert bad.json()["detail"].startswith("event 1:")
    single = client.post("/api/v1/impact/predict/batch", json={"events": [{**body, "area_km2": None}]})
    assert single.json()["detail"].startswith("event 0:")


def test_test_events_are_held_out_records(client):
    page = client.get("/api/v1/impact/test-events?country=kenya&limit=5").json()
    assert page["test_years"].startswith("2019")
    assert page["events"]
    assert all("Kenya" in event["country"] for event in page["events"])
    assert all(event["start_date"] >= "2019-01-01" for event in page["events"])
    dates = [event["start_date"] for event in page["events"]]
    assert dates == sorted(dates, reverse=True)


def test_every_model_column_has_an_api_field():
    card = impact.metrics()
    for variant in impact.VARIANT_NAMES:
        spec = card["variants"][variant]
        for column in [*spec["numeric_features"], *spec["categorical_features"]]:
            assert column in impact.DERIVED or column in impact.WEATHER_FIELDS.values(), column
        assert set(impact.required_fields(variant)) <= set(impact.ImpactEventIn.model_fields)


def test_cause_rules_match_the_training_groups():
    assert impact.cause_group("Tropical Storm Amphan") == "tropical_cyclone"
    assert impact.cause_group("Tropial Storm Chyba") == "tropical_cyclone"
    assert impact.cause_group("Extra-tropical cyclone") == "extratropical_storm"
    assert impact.cause_group("Dam Break Heavy Rain") == "dam_or_levee"
    assert impact.cause_group("Heavy RAin") == "heavy_rain"
    assert impact.cause_group("Landslide") == "other"


def test_committed_openapi_schema_matches_the_routes():
    """api/openapi.json is regenerated with `python -m api.export_openapi`."""
    import json

    from api.app.main import app
    from api.export_openapi import OPENAPI_PATH

    committed = json.loads(OPENAPI_PATH.read_text())
    live = app.openapi()

    def operations(schema):
        return {(path, method) for path, item in schema["paths"].items() for method in item}

    assert operations(committed) == operations(live)
    for path, item in committed["paths"].items():
        for operation in item.values():
            assert operation.get("tags"), f"{path} has no tag"
    predict = committed["paths"]["/api/v1/impact/predict"]["post"]
    assert set(predict["requestBody"]["content"]["application/json"]["examples"]) == {"brief", "no_size"}
    assert predict["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/ImpactPrediction")
    assert "422" in predict["responses"]
