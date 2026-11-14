"use client";

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { api, errorDetail } from "../../lib/api";
import { useDesk } from "../desk";
import { useRiskMap } from "./MapSection";

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

// Report statuses in order, for the progress steps on each report card.
const REPORT_STEPS = [["open", "Reported"], ["inspection_scheduled", "Inspection"], ["repaired", "Repaired"]];
// Each list in the tracker shows this many cards before "Show all".
const FIRST = 6;

// The cell picked on the map (written by RiskMap). Same key and event name as RiskMap.jsx and the other tabs.
const SELECTED_KEY = "drainsense:selected";
const SELECTED_EVENT = "drainsense:selected-change";
function subscribeSelected(callback) {
  window.addEventListener("storage", callback); // another tab
  window.addEventListener(SELECTED_EVENT, callback); // this tab
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener(SELECTED_EVENT, callback);
  };
}
function readSelected() {
  try {
    return window.localStorage.getItem(SELECTED_KEY) || null;
  } catch {
    return null;
  }
}
function writeSelected(id) {
  try {
    if (id) window.localStorage.setItem(SELECTED_KEY, id);
    else window.localStorage.removeItem(SELECTED_KEY);
  } catch {
    // Storage can be blocked; the event below still updates this page.
  }
  window.dispatchEvent(new Event(SELECTED_EVENT));
}

const today = () => new Date().toLocaleDateString("en-CA");

function blankDrafts() {
  return {
    report: { cell_id: "", severity: "blocked", reporter: "", note: "" },
    inspection: { report_id: "", cell_id: "", crew: "", scheduled_for: today() },
    repair: { inspection_id: "", report_id: "", cell_id: "", action: "", completed_on: today() },
    incident: { cell_id: "", kind: INCIDENT_KINDS[0], occurred_on: today(), note: "" },
  };
}

// The drainage ledger, in two tabs: part "form" is the Report tab (record a report, inspection, repair or
// incident), part "tracker" is the Tracker tab (everything logged, and whether repairs cut the incidents).
export default function LedgerSection({ part = "form" }) {
  const [log, setLog] = useState({ reports: [], inspections: [], repairs: [], incidents: [] });
  const [effect, setEffect] = useState([]);
  const [kind, setKind] = useState("report");
  const [drafts, setDrafts] = useState(blankDrafts);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { bump, revision, formRequest, openForm } = useDesk();
  const { features } = useRiskMap();
  // Ledger rows carry only the cell id; the grid has each cell's reverse-geocoded place name.
  const cells = useMemo(() => features.map((f) => f.properties).sort((a, b) => a.rank - b.rank), [features]);
  // null on the server and until the browser has read it, so the first render matches.
  const selectedId = useSyncExternalStore(subscribeSelected, readSelected, () => null);

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

  // Old links still open a form: /operations?form=inspection redirects to the Report tab. (?cell= is read by the map.)
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    if (FORMS.some(([key]) => key === query.get("form"))) setKind(query.get("form"));
  }, []);

  // Both tabs reload after any save, so the tracker shows what the Report tab just recorded.
  useEffect(() => {
    load().catch((err) => setError(errorDetail(err)));
  }, [revision]);

  // Until a cell is picked on the map, the forms start on the top-ranked cell.
  useEffect(() => {
    const start = cells[0]?.cell_id;
    if (!start) return;
    setDrafts((cur) => Object.fromEntries(
      Object.entries(cur).map(([key, draft]) => [key, { ...draft, cell_id: draft.cell_id || start }]),
    ));
  }, [cells]);

  // The map, the dispatch plan and the tracker open a form on a cell; the desk switches to this tab.
  useEffect(() => {
    if (!formRequest || part !== "form") return;
    setKind(formRequest.kind);
    setNotice("");
    setError("");
    setDrafts((cur) => {
      const draft = { ...cur[formRequest.kind], cell_id: formRequest.cellId };
      // A linked report or inspection would lock the cell to another one, so the link is dropped unless the
      // request names one (the tracker's "Schedule inspection" on a report).
      for (const link of ["report_id", "inspection_id"]) if (link in draft) draft[link] = "";
      return { ...cur, [formRequest.kind]: { ...draft, ...(formRequest.patch || {}) } };
    });
  }, [formRequest, part]);

  const placeOf = useMemo(() => Object.fromEntries(cells.map((c) => [c.cell_id, c.place_name])), [cells]);

  // The cell picked on the map. Until the grid has loaded, trust the saved id so the page does not flash unfiltered.
  const picked = selectedId ? cells.find((c) => c.cell_id === selectedId) : null;
  const scopeId = cells.length ? picked?.cell_id ?? null : selectedId;

  // Forms start on the picked cell, and follow it when it changes. A form linked to a report or inspection keeps that record's cell.
  useEffect(() => {
    if (!picked) return;
    setDrafts((cur) => Object.fromEntries(
      Object.entries(cur).map(([key, draft]) => [
        key,
        draft.report_id || draft.inspection_id ? draft : { ...draft, cell_id: picked.cell_id },
      ]),
    ));
  }, [picked?.cell_id]);

  const draft = drafts[kind];
  const set = (patch) => setDrafts((cur) => ({ ...cur, [kind]: { ...cur[kind], ...patch } }));

  // Start a form from a tracker card: the next step for that report or inspection, on the Report tab.
  function startFrom(next, patch) {
    openForm(next, patch.cell_id, patch);
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
      setNotice(`${SAVED[kind]} #${row.id} saved for ${placeOf[row.cell_id] || row.cell_id}.`);
      setDrafts((cur) => ({
        ...cur,
        [kind]: { ...cur[kind], ...Object.fromEntries(CLEARED[kind].map((field) => [field, ""])) },
      }));
      bump(); // reloads this tab, the tracker, the map dots and the dispatch plan
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  // Everything shown below is narrowed to the picked cell when there is one.
  const inScope = (row) => !scopeId || row.cell_id === scopeId;
  const shown = {
    reports: log.reports.filter(inScope),
    inspections: log.inspections.filter(inScope),
    repairs: log.repairs.filter(inScope),
    incidents: log.incidents.filter(inScope),
  };
  const shownEffect = effect.filter(inScope);
  const emptyText = scopeId ? "None logged in this cell." : "None logged.";

  const openReports = shown.reports.filter((r) => OPEN.has(r.status));
  const scheduled = shown.inspections.filter((i) => i.status === "scheduled");
  const linked = Boolean(draft.report_id || draft.inspection_id);

  const cellSelect = (
    <label>
      Cell{linked && " (from the linked record)"}
      <select value={draft.cell_id} disabled={linked} onChange={(event) => set({ cell_id: event.target.value })}>
        {cells.map((cell) => (
          <option key={cell.cell_id} value={cell.cell_id}>
            {cell.place_name} ({cell.cell_id}) · rank {cell.rank} · p {cell.probability.toFixed(2)}
          </option>
        ))}
      </select>
    </label>
  );

  const scopeChip = picked && (
    <p className="scope">
      <b>{picked.place_name || picked.cell_id}</b>
      <span>picked on the map</span>
      <a
        href="#"
        onClick={(e) => {
          e.preventDefault();
          writeSelected(null);
        }}
      >
        Show all
      </a>
    </p>
  );

  if (part === "tracker") {
    const place = (row) => <span title={row.cell_id}>{placeOf[row.cell_id] || row.cell_id}</span>;
    return (
      <div className="sheet">
        <h1>Incident &amp; repair tracker</h1>
        <p className="mute">
          Every report, inspection, repair and incident, newest first. Open reports and repeat incidents feed the
          dispatch plan; repairs are checked against the incidents logged before and after.
        </p>
        {scopeChip}
        {error && <p className="error">{error}</p>}

        <div className="kpis four">
          <div className="kpi hot"><b>{openReports.length}</b><span>Open reports</span></div>
          <div className="kpi"><b>{scheduled.length}</b><span>Inspections due</span></div>
          <div className="kpi"><b>{shown.repairs.length}</b><span>Repairs</span></div>
          <div className="kpi"><b>{shown.incidents.length}</b><span>Incidents</span></div>
        </div>

        <h2 className="sub">Reports</h2>
        <Cards empty={emptyText} rows={shown.reports} render={(r) => {
          const at = REPORT_STEPS.findIndex(([status]) => status === r.status);
          return (
            <>
              <div className="row"><b>#{r.id} · {r.severity} drain</b><span className={`origin ${r.origin}`}>{r.origin}</span></div>
              <div className="mute">{place(r)} · {r.reporter}</div>
              {r.note && <div className="note">{r.note}</div>}
              <div className="steps">
                {REPORT_STEPS.map(([status, label], n) => <span key={status} className={n <= at ? "d" : ""}>{label}</span>)}
              </div>
              {r.status === "open" && (
                <button type="button" className="act small" onClick={() => startFrom("inspection", { report_id: String(r.id), cell_id: r.cell_id })}>
                  Schedule inspection →
                </button>
              )}
            </>
          );
        }} />

        <h2 className="sub">Inspections</h2>
        <Cards empty={emptyText} rows={shown.inspections} render={(i) => (
          <>
            <div className="row"><b>#{i.id} · {i.crew}</b><span className={`origin ${i.origin}`}>{i.origin}</span></div>
            <div className="mute">{place(i)} · {i.scheduled_for} · {i.status}{i.report_id != null && ` · report #${i.report_id}`}</div>
            {i.status === "scheduled" && (
              <button type="button" className="act small"
                onClick={() => startFrom("repair", { inspection_id: String(i.id), report_id: i.report_id == null ? "" : String(i.report_id), cell_id: i.cell_id })}>
                Log repair →
              </button>
            )}
          </>
        )} />

        <h2 className="sub">Repairs</h2>
        <Cards empty={emptyText} rows={shown.repairs} render={(r) => (
          <>
            <div className="row"><b>#{r.id} · {r.completed_on}</b><span className={`origin ${r.origin}`}>{r.origin}</span></div>
            <div className="mute">{place(r)}{r.inspection_id != null && ` · inspection #${r.inspection_id}`}{r.report_id != null && ` · report #${r.report_id}`}</div>
            <div className="note">{r.action}</div>
          </>
        )} />

        <h2 className="sub">Incidents</h2>
        <Cards empty={emptyText} rows={shown.incidents} render={(r) => (
          <>
            <div className="row"><b>#{r.id} · {r.kind}</b><span className={`origin ${r.origin}`}>{r.origin}</span></div>
            <div className="mute">{place(r)} · {r.occurred_on}</div>
            {r.note && <div className="note">{r.note}</div>}
          </>
        )} />

        <h2 className="sub">Did the repair work?</h2>
        <p className="mute">
          Logged incidents in the same cell during the 365 days before a repair, and the 365 days after.
        </p>
        <table className="compact">
          <thead>
            <tr><th>Place</th><th className="num">Before</th><th className="num">After</th><th className="num">Δ</th></tr>
          </thead>
          <tbody>
            {shownEffect.length === 0 && (
              <tr><td colSpan={4}>{scopeId ? "No repairs logged in this cell." : "No repairs logged."}</td></tr>
            )}
            {shownEffect.map((row) => (
              <tr key={row.repair_id}>
                <td title={row.reading}>
                  {placeOf[row.cell_id] || row.cell_id}
                  <div className="mute">{row.completed_on} · <span className={`origin ${row.origin}`}>{row.origin}</span></div>
                </td>
                <td className="num">{row.incidents_365d_before}</td>
                <td className="num">{row.incidents_365d_after}</td>
                <td className="num">{row.delta}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="sheet">
      <h1>Community drainage report</h1>
      <p className="mute">
        Report a blocked drain, or record the inspection, repair or incident that follows. Each entry is tied to a risk
        cell; pick one on the map or below.
      </p>
      {scopeChip}

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
      <p className="mute">Saved entries appear in the <a href="#tracker">Tracker</a> and feed the <a href="#dispatch">Dispatch plan</a>.</p>
    </div>
  );
}

// A tracker list as cards, newest first, with the rest behind "Show all".
function Cards({ rows, render, empty }) {
  const [all, setAll] = useState(false);
  if (!rows.length) return <p className="mute">{empty}</p>;
  const newest = [...rows].sort((a, b) => b.id - a.id);
  const shown = all ? newest : newest.slice(0, FIRST);
  return (
    <>
      {shown.map((row) => <div className="card job" key={row.id}>{render(row)}</div>)}
      {rows.length > FIRST && (
        <button type="button" className="act ghost small" onClick={() => setAll((on) => !on)}>
          {all ? "Show fewer" : `Show all ${rows.length}`}
        </button>
      )}
    </>
  );
}
