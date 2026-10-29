"""DrainSense API: model scores, derived sector flags, and the drainage log."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from api.app import db, impact, scoring

SEVERITIES = {"blocked", "slow", "overflow"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    con = db.connect()
    try:
        db.init_db(con)
        if os.environ.get("DRAINSENSE_SEED", "1") != "0":
            ranked = [row["cell_id"] for row in scoring.hotspots(limit=5)]
            db.seed_synthetic(con, ranked)
    finally:
        con.close()
    yield


DESCRIPTION = """
Global flood desk API. Two separate models are served here.

* **Risk map** (`/risk`, `/sectors`, `/response`): logistic flood-susceptibility scores for 5-degree cells,
  from `model/train.py`.
* **Flood impact** (`/impact`): the CatBoost regressor from the executive brief, from `model/impact.py`.
  Its target is the DFO magnitude, log10(days × severity × area_km2). When those three are known the
  formula is exact, and every prediction returns it next to the model's estimate. Weather inputs are
  Open-Meteo (ERA5 / ERA5-Land) daily values at the centre of the flooded area. Read
  `GET /api/v1/impact/model` before using the numbers.

Drainage reports, inspections, repairs and incidents shipped with the app are synthetic drill rows.
"""

TAGS = [
    {"name": "impact model", "description": "CatBoost flood-impact regressor (variants `brief` and `no_size`)."},
    {"name": "risk map", "description": "Logistic flood-susceptibility scores for 5-degree cells."},
    {"name": "drainage log", "description": "Reports, inspections, repairs and incidents stored in SQLite."},
    {"name": "system", "description": "Health and the risk model's metrics card."},
]

IMPACT_422 = {
    422: {
        "description": (
            "Request rejected. Schema errors return the standard list under `detail`. "
            "An event the variant cannot score returns a string, e.g. `variant brief needs: area_km2`."
        ),
    },
}

app = FastAPI(
    title="DrainSense",
    version="0.2.0",
    description=DESCRIPTION,
    openapi_tags=TAGS,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ReportIn(BaseModel):
    cell_id: str
    severity: str
    note: str = Field(min_length=1, max_length=500)
    reporter: str = Field(min_length=1, max_length=80)


class InspectionIn(BaseModel):
    cell_id: str
    report_id: int | None = None
    crew: str = Field(min_length=1, max_length=80)
    scheduled_for: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class RepairIn(BaseModel):
    cell_id: str
    inspection_id: int | None = None
    report_id: int | None = None
    action: str = Field(min_length=1, max_length=500)
    completed_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class IncidentIn(BaseModel):
    cell_id: str
    kind: str = Field(min_length=1, max_length=80)
    occurred_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    note: str = Field(min_length=1, max_length=500)


@app.exception_handler(impact.ModelOutOfStep)
async def impact_out_of_step(request: Request, exc: impact.ModelOutOfStep):
    # A handled 503 keeps the CORS headers, so the desk can show this message instead of "Failed to fetch".
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


def _require_cell(cell_id: str) -> dict:
    row = scoring.cell(cell_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown cell {cell_id}")
    return row


@app.get("/api/v1/health", tags=["system"])
def health():
    return {"status": "ok", "cells": len(scoring.cells()), "service": "drainsense"}


@app.get("/api/v1/metrics", tags=["system"])
def get_metrics():
    return scoring.metrics()


@app.get("/api/v1/impact/model", tags=["impact model"], response_model=impact.ModelCard,
         summary="Model card: metrics, baselines, data checks, required inputs")
def impact_model():
    return impact.model_card()


@app.post("/api/v1/impact/predict", tags=["impact model"], response_model=impact.ImpactPrediction,
          responses=IMPACT_422, summary="Score one flood event")
def impact_predict(
    body: Annotated[impact.ImpactEventIn, Body(openapi_examples=impact.PREDICT_EXAMPLES)],
    variant: Annotated[Literal["brief", "no_size"], Query(description="Which CatBoost model scores the event")] = "brief",
):
    """Returns the estimate, SHAP contributions, and the exact DFO formula value when
    `end_date`, `area_km2` and `severity` are all sent. Required fields per variant are listed
    under `inputs` in `GET /api/v1/impact/model`."""
    try:
        return impact.predict([body], variant)[0]
    except impact.ImpactInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/impact/predict/batch", tags=["impact model"], response_model=impact.ImpactBatchOut,
          responses=IMPACT_422, summary="Score up to 500 flood events")
def impact_predict_batch(
    body: Annotated[impact.ImpactBatchIn, Body(openapi_examples=impact.BATCH_EXAMPLES)],
    variant: Annotated[Literal["brief", "no_size"], Query(description="Which CatBoost model scores the events")] = "brief",
):
    """One bad event fails the whole call, and the message starts with its index (`event 1: ...`)."""
    try:
        results = impact.predict(body.events, variant, label_index=True)
    except impact.ImpactInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"variant": variant, "count": len(results), "results": results}


@app.get("/api/v1/impact/test-events", tags=["impact model"], response_model=impact.HeldOutEventsOut,
         summary="Held-out 2019-2024 events with both holdout predictions")
def impact_test_events(
    limit: Annotated[int, Query(description="1 to 1000; values outside are clamped. 1000 returns all 792.")] = 50,
    country: Annotated[str | None, Query(description="Country name substring or ISO code, e.g. kenya or KE")] = None,
):
    limit = max(1, min(limit, 1000))
    card = impact.metrics()
    return {
        "test_years": card["split"]["test_years"],
        "rmse": {name: v["temporal_test"]["rmse"] for name, v in card["variants"].items()},
        "events": impact.test_events(limit, country),
    }


@app.get("/api/v1/risk/grid", tags=["risk map"])
def risk_grid():
    return scoring.grid()


@app.get("/api/v1/risk/hotspots", tags=["risk map"])
def risk_hotspots(limit: int = 12):
    limit = max(1, min(limit, 50))
    return {"outlook_year": 2024, "feature_year": 2023, "hotspots": scoring.hotspots(limit)}


@app.get("/api/v1/risk/cells/{cell_id}", tags=["risk map"])
def risk_cell(cell_id: str):
    row = scoring.detail(cell_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown cell {cell_id}")
    return row


@app.get("/api/v1/sectors", tags=["risk map"])
def sectors(limit: int = 8):
    limit = max(1, min(limit, 30))
    cards = []
    for row in scoring.hotspots(limit):
        full = scoring.cell(row["cell_id"])
        cards.append({"cell": row, "indicators": scoring.sector_indicators(full)})
    meta = scoring.metrics()
    return {
        "kind": "derived indicators",
        "disclaimer": (
            "These panels do not add data. They apply fixed rules to the flood-model score, "
            "elevation, and previous-year rainfall."
        ),
        "threshold": meta["model"]["threshold"],
        "cards": cards,
    }


@app.get("/api/v1/reports", tags=["drainage log"])
def list_reports():
    con = db.connect()
    try:
        return {"reports": db.rows(con, "reports")}
    finally:
        con.close()


@app.post("/api/v1/reports", status_code=201, tags=["drainage log"])
def create_report(body: ReportIn):
    if body.severity not in SEVERITIES:
        raise HTTPException(status_code=422, detail="severity must be blocked, slow, or overflow")
    _require_cell(body.cell_id)
    con = db.connect()
    try:
        cur = con.execute(
            """INSERT INTO reports (cell_id, severity, note, reporter, status, origin, created_at)
               VALUES (?, ?, ?, ?, 'open', 'community', ?)""",
            (body.cell_id, body.severity, body.note.strip(), body.reporter.strip(), db.stamp()),
        )
        con.commit()
        return dict(con.execute("SELECT * FROM reports WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        con.close()


@app.get("/api/v1/inspections", tags=["drainage log"])
def list_inspections():
    con = db.connect()
    try:
        return {"inspections": db.rows(con, "inspections")}
    finally:
        con.close()


@app.post("/api/v1/inspections", status_code=201, tags=["drainage log"])
def create_inspection(body: InspectionIn):
    _require_cell(body.cell_id)
    con = db.connect()
    try:
        if body.report_id is not None:
            report = con.execute("SELECT * FROM reports WHERE id = ?", (body.report_id,)).fetchone()
            if report is None:
                raise HTTPException(status_code=404, detail="Unknown report")
            con.execute(
                "UPDATE reports SET status = 'inspection_scheduled' WHERE id = ? AND status = 'open'",
                (body.report_id,),
            )
        cur = con.execute(
            """INSERT INTO inspections (report_id, cell_id, crew, scheduled_for, status, origin, created_at)
               VALUES (?, ?, ?, ?, 'scheduled', 'community', ?)""",
            (body.report_id, body.cell_id, body.crew.strip(), body.scheduled_for, db.stamp()),
        )
        con.commit()
        return dict(con.execute("SELECT * FROM inspections WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        con.close()


@app.get("/api/v1/repairs", tags=["drainage log"])
def list_repairs():
    con = db.connect()
    try:
        return {"repairs": db.rows(con, "repairs")}
    finally:
        con.close()


@app.post("/api/v1/repairs", status_code=201, tags=["drainage log"])
def create_repair(body: RepairIn):
    _require_cell(body.cell_id)
    con = db.connect()
    try:
        if body.report_id is not None:
            con.execute("UPDATE reports SET status = 'repaired' WHERE id = ?", (body.report_id,))
        if body.inspection_id is not None:
            con.execute("UPDATE inspections SET status = 'done' WHERE id = ?", (body.inspection_id,))
        cur = con.execute(
            """INSERT INTO repairs (inspection_id, report_id, cell_id, action, completed_on, origin, created_at)
               VALUES (?, ?, ?, ?, ?, 'community', ?)""",
            (body.inspection_id, body.report_id, body.cell_id, body.action.strip(), body.completed_on, db.stamp()),
        )
        con.commit()
        return dict(con.execute("SELECT * FROM repairs WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        con.close()


@app.get("/api/v1/incidents", tags=["drainage log"])
def list_incidents():
    con = db.connect()
    try:
        return {"incidents": db.rows(con, "incidents")}
    finally:
        con.close()


@app.post("/api/v1/incidents", status_code=201, tags=["drainage log"])
def create_incident(body: IncidentIn):
    _require_cell(body.cell_id)
    con = db.connect()
    try:
        cur = con.execute(
            """INSERT INTO incidents (cell_id, kind, occurred_on, note, origin, created_at)
               VALUES (?, ?, ?, ?, 'community', ?)""",
            (body.cell_id, body.kind.strip(), body.occurred_on, body.note.strip(), db.stamp()),
        )
        con.commit()
        return dict(con.execute("SELECT * FROM incidents WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        con.close()


@app.get("/api/v1/interventions/effectiveness", tags=["drainage log"])
def intervention_effectiveness():
    con = db.connect()
    try:
        items = db.effectiveness(con)
    finally:
        con.close()
    return {
        "definition": (
            "For each repair, count logged incidents in that cell in the 365 days before the "
            "completion date and the 365 days after. This is not a causal effect."
        ),
        "repairs": items,
    }


@app.get("/api/v1/response/plan", tags=["risk map"])
def response_plan(limit: int = 15):
    """Rank cells for inspection. Model points are the outlook score. Log points come from SQLite."""
    limit = max(1, min(limit, 40))
    con = db.connect()
    try:
        open_reports = db.open_report_counts(con)
        incidents = db.incident_counts(con)
    finally:
        con.close()
    plan = []
    for row in scoring.cells():
        open_n = open_reports.get(row["cell_id"], 0)
        incident_n = incidents.get(row["cell_id"], 0)
        recurring = incident_n if incident_n >= 2 else 0
        model_points = round(1000 * row["probability"], 3)
        report_points = 25 * open_n
        recurring_points = 10 * recurring
        plan.append({
            "cell_id": row["cell_id"],
            "center_lat": row["center_lat"],
            "center_lon": row["center_lon"],
            "probability": row["probability"],
            "priority": round(model_points + report_points + recurring_points, 3),
            "components": {
                "model_probability": {
                    "points": model_points,
                    "origin": "model",
                    "note": "1000 × the 2024 outlook probability from model/train.py",
                },
                "open_reports": {
                    "count": open_n,
                    "points": report_points,
                    "origin": "database",
                    "note": "25 points per open or scheduled drainage report. Drill rows are synthetic.",
                },
                "recurring_incidents": {
                    "count": recurring,
                    "points": recurring_points,
                    "origin": "database",
                    "note": "10 points per logged incident when the cell has two or more. Otherwise zero.",
                },
            },
            "action": "Send an inspection crew here before cells with a lower priority.",
        })
    plan.sort(key=lambda item: (-item["priority"], item["cell_id"]))
    for index, item in enumerate(plan, start=1):
        item["rank"] = index
    return {
        "rule": "priority = 1000 × model probability + 25 × open reports + 10 × recurring incidents",
        "plan": plan[:limit],
    }
