"use client";

import { useEffect, useState } from "react";
import { api } from "../../lib/api";

export default function OperationsPage() {
  const [reports, setReports] = useState([]);
  const [inspections, setInspections] = useState([]);
  const [repairs, setRepairs] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [effect, setEffect] = useState([]);
  const [cells, setCells] = useState([]);
  const [error, setError] = useState("");
  const [form, setForm] = useState({ cell_id: "", severity: "blocked", note: "", reporter: "" });

  async function load() {
    const [reportBody, inspectionBody, repairBody, incidentBody, effectBody, spots] = await Promise.all([
      api("/api/v1/reports"),
      api("/api/v1/inspections"),
      api("/api/v1/repairs"),
      api("/api/v1/incidents"),
      api("/api/v1/interventions/effectiveness"),
      api("/api/v1/risk/hotspots?limit=12"),
    ]);
    setReports(reportBody.reports);
    setInspections(inspectionBody.inspections);
    setRepairs(repairBody.repairs);
    setIncidents(incidentBody.incidents);
    setEffect(effectBody.repairs);
    setCells(spots.hotspots);
    setForm((current) => ({ ...current, cell_id: current.cell_id || spots.hotspots[0]?.cell_id || "" }));
  }

  useEffect(() => {
    load().catch((err) => setError(err.message));
  }, []);

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      await api("/api/v1/reports", { method: "POST", body: JSON.stringify(form) });
      setForm((current) => ({ ...current, note: "" }));
      await load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <main className="sheet">
      <p className="banner">Synthetic drill rows are marked. They are not field reports.</p>
      <h1>Drainage ledger</h1>
      <p className="reading">
        Community reports, inspections, repairs, and repeat incidents. The desk ships with a fixed
        synthetic drill so the pages are not empty. Anything you file here is stored as a community row.
      </p>
      {error && <p className="error">{error}</p>}
      <form className="form" onSubmit={submit}>
        <p className="kicker">File a community report</p>
        <label>
          Cell
          <select value={form.cell_id} onChange={(event) => setForm({ ...form, cell_id: event.target.value })}>
            {cells.map((cell) => (
              <option key={cell.cell_id} value={cell.cell_id}>{cell.cell_id} · p {cell.probability.toFixed(2)}</option>
            ))}
          </select>
        </label>
        <label>
          Severity
          <select value={form.severity} onChange={(event) => setForm({ ...form, severity: event.target.value })}>
            <option value="blocked">blocked</option>
            <option value="slow">slow</option>
            <option value="overflow">overflow</option>
          </select>
        </label>
        <label>
          Reporter
          <input value={form.reporter} required onChange={(event) => setForm({ ...form, reporter: event.target.value })} />
        </label>
        <label>
          Note
          <textarea required value={form.note} onChange={(event) => setForm({ ...form, note: event.target.value })} />
        </label>
        <button className="solid" type="submit">File report</button>
      </form>

      <h2>Reports</h2>
      <Ledger rows={reports} columns={["id", "cell_id", "severity", "status", "reporter", "note", "origin"]} />
      <h2>Inspections</h2>
      <Ledger rows={inspections} columns={["id", "cell_id", "crew", "scheduled_for", "status", "origin"]} />
      <h2>Repairs</h2>
      <Ledger rows={repairs} columns={["id", "cell_id", "action", "completed_on", "origin"]} />
      <h2>Incidents</h2>
      <Ledger rows={incidents} columns={["id", "cell_id", "kind", "occurred_on", "note", "origin"]} />

      <h2>Intervention window</h2>
      <p className="reading">
        Logged incidents in the same cell during the 365 days before a repair, and the 365 days after.
        A drop in a synthetic drill is not evidence that the repair worked.
      </p>
      <table>
        <thead>
          <tr>
            <th>Cell</th><th>Completed</th><th>Before</th><th>After</th><th>Delta</th><th>Origin</th>
          </tr>
        </thead>
        <tbody>
          {effect.map((row) => (
            <tr key={row.repair_id}>
              <td>{row.cell_id}</td>
              <td>{row.completed_on}</td>
              <td>{row.incidents_365d_before}</td>
              <td>{row.incidents_365d_after}</td>
              <td>{row.delta}</td>
              <td><span className={`origin ${row.origin}`}>{row.origin}</span><div>{row.reading}</div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}

function Ledger({ rows, columns }) {
  if (!rows.length) return <p>None logged.</p>;
  return (
    <table>
      <thead>
        <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id}>
            {columns.map((column) => (
              <td key={column}>
                {column === "origin"
                  ? <span className={`origin ${row.origin}`}>{row.origin}</span>
                  : row[column]}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
