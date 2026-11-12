"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { api, errorDetail } from "../../lib/api";
import { useDesk } from "../desk";

// Slots 1-3 of the dataviz reference palette, validated on the paper surface. Orange and aqua are under 3:1
// there, so each segment is named in the legend and its points are in the table under the bar.
const PARTS = [
  ["model_probability", "Flood model", "#2a78d6"],
  ["open_reports", "Open reports", "#eb6834"],
  ["recurring_incidents", "Repeat incidents", "#1baf7a"],
];
// The longest bar spans this share of the row, leaving room for the value at its tip.
const SPAN = 82;
// With a cell picked on the map, fetch the largest ranking the API supports.
const ALL_CELLS = 40;

// The cell picked on the map (written by RiskMap). Same key and event name as RiskMap.jsx and the other pages.
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
function clearSelected() {
  try {
    window.localStorage.removeItem(SELECTED_KEY);
  } catch {
    // Storage can be blocked; the event below still resets this page.
  }
  window.dispatchEvent(new Event(SELECTED_EVENT));
}

export default function DispatchSection() {
  const [plan, setPlan] = useState(null);
  const [limit, setLimit] = useState(12);
  const [error, setError] = useState("");
  const { revision, openForm, showOnMap } = useDesk();

  // null on the server and until the browser has read it, so the first render matches.
  const selectedId = useSyncExternalStore(subscribeSelected, readSelected, () => null);
  const scoped = Boolean(selectedId);

  useEffect(() => {
    api(`/api/v1/response/plan?limit=${scoped ? ALL_CELLS : limit}`).then(setPlan).catch((err) => setError(errorDetail(err)));
  }, [limit, revision, scoped]);

  const all = plan?.plan || [];
  // Bars stay on the scale of the whole ranking, so one cell still shows how it compares.
  const max = Math.max(...all.map((item) => item.priority), 1);
  const items = scoped ? all.filter((item) => item.cell_id === selectedId) : all;
  const picked = scoped ? items[0] : null;

  return (
    <div className="sheet">
      <p className="kicker">Where to send a crew first</p>
      <h1>Dispatch plan</h1>
      <p className="reading">
        One ranking from three kinds of evidence: the flood model's outlook for the cell, unresolved drain reports, and
        incidents that keep coming back. Every point is shown with its source. Model points are the real score; report and
        incident points come from the drainage ledger, and the rows shipped with this prototype are synthetic.
      </p>
      {plan && <p className="reading"><code>{plan.rule}</code>. One open report adds 25 points, as much as 0.025 of model probability.</p>}
      {error && <p className="error">{error}</p>}

      {scoped && plan && (
        <p className="scope">
          <b>{picked ? picked.place_name : selectedId}</b>
          <span>
            {picked ? `picked on the map · rank ${picked.rank} of ${all.length}` : "picked on the map · not on the dispatch plan"}
          </span>
          <a
            href="#"
            onClick={(e) => {
              e.preventDefault();
              clearSelected();
            }}
          >
            Show all
          </a>
        </p>
      )}

      <div className="plan-controls">
        <p className="plan-legend">
          {PARTS.map(([key, label, color]) => (
            <span key={key}><i style={{ background: color }} />{label}</span>
          ))}
        </p>
        <label className="filter">
          Show
          <select value={limit} disabled={scoped} onChange={(event) => setLimit(Number(event.target.value))}>
            {[12, 25, 40].map((n) => <option key={n} value={n}>top {n} cells</option>)}
          </select>
        </label>
      </div>

      <ol className="plan">
        {items.map((item) => (
          <li key={item.cell_id}>
            <div className="plan-head">
              <span className="rank">{String(item.rank).padStart(2, "0")}</span>
              <strong title={`Cell ${item.cell_id}`}>{item.place_name}</strong>
              <span className="reading">
                p {item.probability.toFixed(3)} · {item.components.open_reports.count} open{" "}
                {item.components.open_reports.count === 1 ? "report" : "reports"} ·{" "}
                {item.components.recurring_incidents.count} repeat incidents counted
              </span>
            </div>
            <PriorityBar item={item} max={max} />
            <table className="parts">
              <tbody>
                {PARTS.map(([key, label, color]) => {
                  const part = item.components[key];
                  return (
                    <tr key={key}>
                      <td><i className="key" style={{ background: color }} />{label}</td>
                      <td className="num">{part.points}</td>
                      <td><span className={`origin ${part.origin}`}>{part.origin}</span></td>
                      <td>{part.note}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="actions">
              <button type="button" className="link" onClick={() => openForm("inspection", item.cell_id)}>Schedule an inspection</button>
              <button type="button" className="link" onClick={() => showOnMap(item.cell_id)}>Show on the map</button>
            </p>
          </li>
        ))}
      </ol>
    </div>
  );
}

function PriorityBar({ item, max }) {
  const [hover, setHover] = useState(null);
  let start = 0;
  const segments = PARTS.map(([key, label, color]) => {
    const points = item.components[key].points;
    const segment = { key, label, color, points, start };
    start += points;
    return segment;
  }).filter((segment) => segment.points > 0);
  const at = (points) => `${(points / max) * SPAN}%`;
  const summary = segments.map((s) => `${s.label} ${s.points}`).join(", ");
  return (
    <div className="pbar" role="img" aria-label={`Priority ${item.priority}: ${summary}`}>
      <div className="pbar-track" style={{ width: at(item.priority) }}>
        {segments.map((segment) => (
          <span key={segment.key} style={{ flexGrow: segment.points, background: segment.color }}
            onMouseEnter={() => setHover(segment)} onMouseLeave={() => setHover(null)} />
        ))}
      </div>
      <b>{item.priority.toFixed(1)}</b>
      {hover && (
        <div className="tip" style={{ left: at(hover.start + hover.points / 2) }}>
          <strong>{hover.label}</strong>
          <span>{hover.points} of {item.priority} points</span>
        </div>
      )}
    </div>
  );
}