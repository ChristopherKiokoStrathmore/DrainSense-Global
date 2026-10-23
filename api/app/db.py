"""SQLite log for drainage reports, inspections, repairs, and incidents.

Rows inserted by seed_synthetic are drill data and are marked origin='synthetic'.
Rows from the HTTP API are origin='community'. The server sets origin; clients cannot.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    cell_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    note TEXT NOT NULL,
    reporter TEXT NOT NULL,
    status TEXT NOT NULL,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY,
    report_id INTEGER,
    cell_id TEXT NOT NULL,
    crew TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    status TEXT NOT NULL,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repairs (
    id INTEGER PRIMARY KEY,
    inspection_id INTEGER,
    report_id INTEGER,
    cell_id TEXT NOT NULL,
    action TEXT NOT NULL,
    completed_on TEXT NOT NULL,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY,
    cell_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    occurred_on TEXT NOT NULL,
    note TEXT NOT NULL,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def db_path() -> Path:
    raw = os.environ.get("DRAINSENSE_DB")
    path = Path(raw) if raw else ROOT / "api" / "data" / "drainsense.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.commit()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_synthetic(con: sqlite3.Connection, ranked_cell_ids: list[str]) -> None:
    """Fixed drill rows so the desk is not empty. Every row is origin synthetic."""
    existing = con.execute("SELECT COUNT(*) AS n FROM reports").fetchone()["n"]
    if existing:
        return
    if len(ranked_cell_ids) < 5:
        return
    top = ranked_cell_ids[:5]
    created = "2026-10-23T00:00:00Z"

    def report(cell_id, severity, note, status):
        cur = con.execute(
            """INSERT INTO reports (cell_id, severity, note, reporter, status, origin, created_at)
               VALUES (?, ?, ?, ?, ?, 'synthetic', ?)""",
            (cell_id, severity, note, "DrainSense drill", status, created),
        )
        return cur.lastrowid

    lead = top[0]
    report_id = report(lead, "blocked", "Synthetic drill: grate blocked at the cell center, not a field report.", "repaired")
    inspection_id = con.execute(
        """INSERT INTO inspections (report_id, cell_id, crew, scheduled_for, status, origin, created_at)
           VALUES (?, ?, 'Drill crew A', '2025-02-20', 'done', 'synthetic', ?)""",
        (report_id, lead, created),
    ).lastrowid
    con.execute(
        """INSERT INTO repairs (inspection_id, report_id, cell_id, action, completed_on, origin, created_at)
           VALUES (?, ?, ?, 'Synthetic drill: cleared the grate and cut a silt bar.', '2025-03-01', 'synthetic', ?)""",
        (inspection_id, report_id, lead, created),
    )
    for occurred_on, kind in (
        ("2024-06-12", "standing water"),
        ("2024-11-03", "repeat blockage"),
        ("2025-01-18", "road overflow"),
        ("2025-09-02", "standing water"),
    ):
        con.execute(
            """INSERT INTO incidents (cell_id, kind, occurred_on, note, origin, created_at)
               VALUES (?, ?, ?, 'Synthetic drill incident.', 'synthetic', ?)""",
            (lead, kind, occurred_on, created),
        )

    second = top[1]
    report(second, "overflow", "Synthetic drill: recurring overflow, still open.", "open")
    for occurred_on in ("2024-04-01", "2025-02-01"):
        con.execute(
            """INSERT INTO incidents (cell_id, kind, occurred_on, note, origin, created_at)
               VALUES (?, 'overflow', ?, 'Synthetic drill incident.', 'synthetic', ?)""",
            (second, occurred_on, created),
        )

    for cell_id, severity, note in (
        (top[2], "slow", "Synthetic drill: slow drain, no crew assigned."),
        (top[3], "blocked", "Synthetic drill: blocked culvert, no crew assigned."),
        (top[4], "overflow", "Synthetic drill: ditch overtopped, no crew assigned."),
    ):
        report(cell_id, severity, note, "open")
    con.commit()


def rows(con: sqlite3.Connection, table: str) -> list[dict]:
    if table not in {"reports", "inspections", "repairs", "incidents"}:
        raise ValueError(table)
    return [dict(row) for row in con.execute(f"SELECT * FROM {table} ORDER BY id")]


def open_report_counts(con: sqlite3.Connection) -> dict[str, int]:
    query = """
        SELECT cell_id, COUNT(*) AS n FROM reports
        WHERE status IN ('open', 'inspection_scheduled')
        GROUP BY cell_id
    """
    return {row["cell_id"]: row["n"] for row in con.execute(query)}


def incident_counts(con: sqlite3.Connection) -> dict[str, int]:
    query = "SELECT cell_id, COUNT(*) AS n FROM incidents GROUP BY cell_id"
    return {row["cell_id"]: row["n"] for row in con.execute(query)}


def effectiveness(con: sqlite3.Connection) -> list[dict]:
    """Incident counts in the 365 days before and after each repair. Counts are whatever is logged."""
    repairs = rows(con, "repairs")
    out = []
    for repair in repairs:
        before = con.execute(
            """SELECT COUNT(*) AS n FROM incidents
               WHERE cell_id = ? AND occurred_on >= date(?, '-365 days') AND occurred_on < ?""",
            (repair["cell_id"], repair["completed_on"], repair["completed_on"]),
        ).fetchone()["n"]
        after = con.execute(
            """SELECT COUNT(*) AS n FROM incidents
               WHERE cell_id = ? AND occurred_on >= ? AND occurred_on < date(?, '+365 days')""",
            (repair["cell_id"], repair["completed_on"], repair["completed_on"]),
        ).fetchone()["n"]
        out.append({
            "repair_id": repair["id"],
            "cell_id": repair["cell_id"],
            "action": repair["action"],
            "completed_on": repair["completed_on"],
            "origin": repair["origin"],
            "incidents_365d_before": before,
            "incidents_365d_after": after,
            "delta": after - before,
            "reading": (
                "Synthetic drill comparison." if repair["origin"] == "synthetic"
                else "Comparison of logged incidents only. Not a controlled estimate of repair effect."
            ),
        })
    return out


def stamp() -> str:
    return _now()
