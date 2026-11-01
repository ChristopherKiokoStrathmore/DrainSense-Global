"use client";

import { useEffect, useState } from "react";
import { api, errorDetail } from "../../lib/api";

const FORMS = [
  ["report", "Report a blocked drain"],
  ["inspection", "Schedule an inspection"],
  ["repair", "Log a repair"],
  ["incident", "Log an incident"],
];
const ENDPOINT = {
  report: "/api/v1/reports",
  inspection: "/api/v1/inspections",
  repair: "/api/v1/repairs",
  incident: "/api/v1/incidents",
};
const SAVED = { report: "Report", inspection: "Inspection", repair: "Repair", incident: "Incident" };
// Fields emptied after a save. Cell, dates, reporter and crew carry over to the next entry.
const CLEARED = {
  report: ["note"],
  inspection: ["report_id"],
  repair: ["inspection_id", "report_id", "action"],
  incident: ["note"],
};
// Report statuses the dispatch plan counts as unresolved.
const OPEN = new Set(["open", "inspection_scheduled"]);
const INCIDENT_KINDS = ["standing water", "overflow", "repeat blockage", "road overflow"];

const today = () => new Date().toLocaleDateString("en-CA");

function blankDrafts() {
  return {
    report: { cell_id: "", severity: "blocked", reporter: "", note: "" },
    inspection: { report_id: "", cell_id: "", crew: "", scheduled_for: today() },
    repair: { inspection_id: "", report_id: "", cell_id: "", action: "", completed_on: today() },
    incident: { cell_id: "", kind: INCIDENT_KINDS[0], occurred_on: today(), note: "" },
  };
}

export default function OperationsPage() {
  const [log, setLog] = useState({ reports: [], inspections: [], repairs: [], incidents: [] });
  const [effect, setEffect] = useState([]);
  const [cells, setCells] = useState([]);
  const [kind, setKind] = useState("report");
  const [drafts, setDrafts] = useState(blankDrafts);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    const [reportBody, inspectionBody, repairBody, incidentBody, effectBody] = await Promise.all([
      api("/api/v1/reports"),
      api("/api/v1/inspections"),
      api("/api/v1/repairs"),
      api("/api/v1/incidents"),
      api("/api/v1/interventions/effectiveness"),
    ]);
    setLog({
      reports: reportBody.reports,
      inspections: inspectionBody.inspections,
      repairs: repairBody.repairs,
      incidents: incidentBody.incidents,
    });
    setEffect(effectBody.repairs);
  }

  useEffect(() => {
    // Links from the map and the dispatch list open a form on a cell: ?cell=s45_w75&form=inspection
    const query = new URLSearchParams(window.location.search);
    if (FORMS.some(([key]) => key === query.get("form"))) setKind(query.get("form"));
    const wanted = query.get("cell");
    load().catch((err) => setError(errorDetail(err)));
    api("/api/v1/risk/grid")
      .then((grid) => {
        const list = grid.features.map((f) => f.properties).sort((a, b) => a.rank - b.rank);
        setCells(list);
        const start = list.some((c) => c.cell_id === wanted) ? wanted : list[0]?.cell_id || "";
        setDrafts((cur) => Object.fromEntries(
          Object.entries(cur).map(([key, draft]) => [key, { ...draft, cell_id: draft.cell_id || start }]),
        ));
      })
      .catch((err) => setError(errorDetail(err)));
  }, []);

  const draft = drafts[kind];
  const set = (patch) => setDrafts((cur) => ({ ...cur, [kind]: { ...cur[kind], ...patch } }));

  // Start a form from a ledger row: the next step for that report or inspection.
  function startFrom(next, patch) {
    setKind(next);
    setNotice("");
    setError("");
    setDrafts((cur) => ({ ...cur, [next]: { ...cur[next], ...patch } }));
    document.getElementById("record")?.scrollIntoView({ behavior: "smooth" });
  }

  function pickReport(id) {
    const report = log.reports.find((r) => String(r.id) === id);
    set({ report_id: id, ...(report ? { cell_id: report.cell_id } : {}) });
  }

  function pickInspection(id) {
    const inspection = log.inspections.find((i) => String(i.id) === id);
    set(inspection
      ? { inspection_id: id, report_id: inspection.report_id == null ? "" : String(inspection.report_id), cell_id: inspection.cell_id }
      : { inspection_id: "", report_id: "" });
  }

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    const body = {};
    for (const [field, value] of Object.entries(draft)) {
      if (value === "") continue;
      body[field] = field === "report_id" || field === "inspection_id" ? Number(value) : value;
    }
    try {
      const row = await api(ENDPOINT[kind], { method: "POST", body: JSON.stringify(body) });
      setNotice(`${SAVED[kind]} #${row.id} saved for ${row.cell_id}.`);
      setDrafts((cur) => ({
        ...cur,
        [kind]: { ...cur[kind], ...Object.fromEntries(CLEARED[kind].map((field) => [field, ""])) },
      }));
      await load();
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  const openReports = log.reports.filter((r) => OPEN.has(r.status));
  const scheduled = log.inspections.filter((i) => i.status === "scheduled");
  const linked = Boolean(draft.report_id || draft.inspection_id);

  const cellSelect = (
    <label>
      Cell{linked && " (from the linked record)"}
      <select value={draft.cell_id} disabled={linked} onChange={(event) => set({ cell_id: event.target.value })}>
        {cells.map((cell) => (
          <option key={cell.cell_id} value={cell.cell_id}>
            {cell.cell_id} · rank {cell.rank} · p {cell.probability.toFixed(2)}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <main className="sheet">
      <p className="banner">Synthetic drill rows are marked. They are not field reports.</p>
      <h1>Drainage ledger</h1>
      <p className="reading">
        Residents' blocked-drain reports, the inspections and repairs that follow, and the incidents that come back,
        each tied to a risk cell. Open reports and repeat incidents feed the dispatch list; repairs are checked against
        the incidents logged before and after. The desk ships with a fixed synthetic drill so the pages are not empty.
        Anything you save here is stored as a community row.
      </p>

      <div className="metrics">
        <div className="metric"><span>Open reports</span><b>{openReports.length}</b></div>
        <div className="metric"><span>Inspections scheduled</span><b>{scheduled.length}</b></div>
        <div className="metric"><span>Repairs logged</span><b>{log.repairs.length}</b></div>
        <div className="metric"><span>Incidents logged</span><b>{log.incidents.length}</b></div>
      </div>

      <section id="record">
        <div className="toggles" role="group" aria-label="Record type">
          {FORMS.map(([key, label]) => (
            <button key={key} type="button" aria-pressed={kind === key}
              className={kind === key ? "toggle on" : "toggle"}
              onClick={() => { setKind(key); setNotice(""); setError(""); }}>
              {label}
            </button>
          ))}
        </div>
        <form className="form" onSubmit={submit}>
          {kind === "report" && (
            <>
              <p className="kicker">A resident or crew member reports a drain</p>
              {cellSelect}
              <label>
                Severity
                <select value={draft.severity} onChange={(event) => set({ severity: event.target.value })}>
                  <option value="blocked">blocked</option>
                  <option value="slow">slow</option>
                  <option value="overflow">overflow</option>
                </select>
              </label>
              <label>
                Reporter
                <input value={draft.reporter} required maxLength={80} onChange={(event) => set({ reporter: event.target.value })} />
              </label>
              <label>
                Note
                <textarea required maxLength={500} value={draft.note} onChange={(event) => set({ note: event.target.value })} />
              </label>
            </>
          )}
          {kind === "inspection" && (
            <>
              <p className="kicker">Send a crew to look. Linking a report marks it inspection scheduled.</p>
              <label>
                Report
                <select value={draft.report_id} onChange={(event) => pickReport(event.target.value)}>
                  <option value="">None, a routine inspection</option>
                  {openReports.map((r) => (
                    <option key={r.id} value={r.id}>#{r.id} · {r.cell_id} · {r.severity} · {r.status.replaceAll("_", " ")}</option>
                  ))}
                </select>
              </label>
              {cellSelect}
              <label>
                Crew
                <input value={draft.crew} required maxLength={80} onChange={(event) => set({ crew: event.target.value })} />
              </label>
              <label>
                Scheduled for
                <input type="date" required value={draft.scheduled_for} onChange={(event) => set({ scheduled_for: event.target.value })} />
              </label>
            </>
          )}
          {kind === "repair" && (
            <>
              <p className="kicker">Record the fix. Linking closes the inspection and marks the report repaired.</p>
              <label>
                Inspection
                <select value={draft.inspection_id} onChange={(event) => pickInspection(event.target.value)}>
                  <option value="">None</option>
                  {scheduled.map((i) => (
                    <option key={i.id} value={i.id}>#{i.id} · {i.cell_id} · {i.crew} · {i.scheduled_for}</option>
                  ))}
                </select>
              </label>
              {!draft.inspection_id && (
                <label>
                  Report
                  <select value={draft.report_id} onChange={(event) => pickReport(event.target.value)}>
                    <option value="">None</option>
                    {openReports.map((r) => (
                      <option key={r.id} value={r.id}>#{r.id} · {r.cell_id} · {r.severity}</option>
                    ))}
                  </select>
                </label>
              )}
              {cellSelect}
              <label>
                What was done
                <textarea required maxLength={500} value={draft.action} onChange={(event) => set({ action: event.target.value })} />
              </label>
              <label>
                Completed on
                <input type="date" required value={draft.completed_on} onChange={(event) => set({ completed_on: event.target.value })} />
              </label>
            </>
          )}
          {kind === "incident" && (
            <>
              <p className="kicker">Water on the ground. Two or more in a cell add points on the dispatch list.</p>
              {cellSelect}
              <label>
                Kind
                <input list="incident-kinds" required maxLength={80} value={draft.kind} onChange={(event) => set({ kind: event.target.value })} />
                <datalist id="incident-kinds">
                  {INCIDENT_KINDS.map((k) => <option key={k} value={k} />)}
                </datalist>
              </label>
              <label>
                Occurred on
                <input type="date" required value={draft.occurred_on} onChange={(event) => set({ occurred_on: event.target.value })} />
              </label>
              <label>
                Note
                <textarea required maxLength={500} value={draft.note} onChange={(event) => set({ note: event.target.value })} />
              </label>
            </>
          )}
          <button className="solid" type="submit" disabled={busy || !draft.cell_id}>
            {busy ? "Saving…" : FORMS.find(([key]) => key === kind)[1]}
          </button>
        </form>
        {notice && <p className="notice">{notice}</p>}
        {error && <p className="error">{error}</p>}
      </section>

      <h2>Reports</h2>
      <Ledger
        rows={log.reports}
        columns={["id", "cell_id", "severity", "status", "reporter", "note", "origin"]}
        action={(r) => r.status === "open" && (
          <button type="button" className="link" onClick={() => startFrom("inspection", { report_id: String(r.id), cell_id: r.cell_id })}>
            Schedule inspection
          </button>
        )}
      />
      <h2>Inspections</h2>
      <Ledger
        rows={log.inspections}
        columns={["id", "report_id", "cell_id", "crew", "scheduled_for", "status", "origin"]}
        action={(i) => i.status === "scheduled" && (
          <button type="button" className="link"
            onClick={() => startFrom("repair", { inspection_id: String(i.id), report_id: i.report_id == null ? "" : String(i.report_id), cell_id: i.cell_id })}>
            Log repair
          </button>
        )}
      />
      <h2>Repairs</h2>
      <Ledger rows={log.repairs} columns={["id", "inspection_id", "report_id", "cell_id", "action", "completed_on", "origin"]} />
      <h2>Incidents</h2>
      <Ledger rows={log.incidents} columns={["id", "cell_id", "kind", "occurred_on", "note", "origin"]} />

      <h2>Did the repair work?</h2>
      <p className="reading">
        Logged incidents in the same cell during the 365 days before a repair, and the 365 days after.
        This counts what was logged; it is not a controlled estimate, and a drop in a synthetic drill is not evidence
        that the repair worked.
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

function Ledger({ rows, columns, action }) {
  if (!rows.length) return <p>None logged.</p>;
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column.replaceAll("_", " ")}</th>)}
            {action && <th>Next step</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              {columns.map((column) => (
                <td key={column}>
                  {column === "origin"
                    ? <span className={`origin ${row.origin}`}>{row.origin}</span>
                    : column === "status" ? row.status.replaceAll("_", " ") : row[column] ?? "—"}
                </td>
              ))}
              {action && <td>{action(row)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
