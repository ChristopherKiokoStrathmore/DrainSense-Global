"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { API, api } from "../../lib/api";
import { useDesk } from "../desk";

// Report statuses the dispatch plan counts as unresolved.
const OPEN = new Set(["open", "inspection_scheduled"]);


// Whiteboard bands: Urgent (red), Moderate risk (amber), OK for now (green).
// Amber starts at the model's own threshold; red starts at RED_AT.
const RED_AT = 0.3;
const BANDS = {
  red: { key: "red", label: "Urgent" },
  amber: { key: "amber", label: "Moderate risk" },
  green: { key: "green", label: "OK for now" },
};
function band(score, threshold) {
  if (score == null || threshold == null) return null;
  if (score >= RED_AT) return BANDS.red;
  if (score >= threshold) return BANDS.amber;
  return BANDS.green;
}

function BandPill({ value }) {
  if (!value) return null;
  return <span className={`band ${value.key}`}>{value.label}</span>;
}
// The cell picked on the map. Same key and event name as RiskMap.jsx and the other pages.
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

// Each gap in today's practice, and the part of the desk that answers it.
const ANSWERS = [
  {
    problem: "Flood records, rainfall and terrain, and residents' drain reports sit in different places.",
    part: "Risk map",
    href: "#map",
    answer: "A flood score per land cell for every year 2014–2024 from the previous year's rainfall and elevation, played as a time series against the floods the DFO catalogue recorded, with the 2024 outlook and open drain reports on top.",
  },
  {
    problem: "Crews are sent where the last complaint came from.",
    part: "Dispatch plan",
    href: "#dispatch",
    answer: "Cells ranked by flood score, unresolved reports and repeat incidents together. Every point is shown with its source.",
  },
  {
    problem: "Nobody can say afterwards whether a repair cut the number of incidents.",
    part: "Drainage ledger",
    href: "#ledger",
    answer: "Report, inspection, repair and incident in one log per cell, with incidents counted the year before and after each repair.",
  },
];

const LOOP = [
  ["Report", "A resident reports a blocked drain in a cell.", "#ledger"],
  ["Score", "The flood model's outlook for that cell, and why.", "#map"],
  ["Rank", "Score, open reports and repeat incidents set the order.", "#dispatch"],
  ["Inspect", "A crew is scheduled against the report.", "#ledger"],
  ["Repair", "The fix is logged and the report closed.", "#ledger"],
  ["Measure", "Incidents the year before and after the repair.", "#ledger"],
];

export default function OverviewSection() {
  // Raw API data. Scoping to the picked cell happens at render time, so a new pick shows at once.
  const [data, setData] = useState({});
  const [down, setDown] = useState(false);
  const { revision } = useDesk();
  // null on the server and until the browser has read it, so the first render matches.
  const selectedId = useSyncExternalStore(subscribeSelected, readSelected, () => null);

  // Each figure loads on its own, so one failing call leaves a dash rather than a blank page.
  useEffect(() => {
    Promise.allSettled([
      api("/api/v1/risk/grid"),
      api("/api/v1/reports"),
      api("/api/v1/interventions/effectiveness"),
      api("/api/v1/response/plan?limit=40"), // the whole ranking, so a picked cell is found
      api("/api/v1/metrics"),
    ]).then(([grid, reports, effect, plan, metrics]) => {
      const value = (result) => (result.status === "fulfilled" ? result.value : null);
      setData({
        cells: value(grid)?.features.map((f) => f.properties),
        open: value(reports)?.reports.filter((r) => OPEN.has(r.status)),
        repairs: value(effect)?.repairs,
        plan: value(plan)?.plan,
        metrics: value(metrics),
      });
      // fetch() rejects with a TypeError only when the API could not be reached at all.
      setDown([grid, reports, effect, plan, metrics].every((r) => r.status === "rejected" && r.reason instanceof TypeError));
    });
  }, [revision]);

  const { cells, metrics } = data;
  const picked = selectedId ? cells?.find((c) => c.cell_id === selectedId) : null;
  const scoped = Boolean(selectedId);
  const inScope = (row) => !picked || row.cell_id === picked.cell_id;

  const open = data.open?.filter(inScope);
  const repairs = data.repairs?.filter(inScope);
  const measured = repairs?.at(-1);
  const top = picked ? data.plan?.find((p) => p.cell_id === picked.cell_id) : data.plan?.[0];
  const threshold = metrics ? ` ${metrics.model.threshold}` : "";

  const cut = metrics?.model.threshold;
  const pickedBand = picked ? band(picked.probability, cut) : null;
  const topBand = top ? band(top.probability, cut) : null;
  const counts = cells && cut != null
    ? cells.reduce((n, c) => {
        const b = band(c.probability, cut);
        if (b) n[b.key] += 1;
        return n;
      }, { red: 0, amber: 0, green: 0 })
    : null;
  return (
    <div className="sheet wide overview">
      <section className="hero">
        <p className="kicker">Flood-risk and drainage desk</p>
        <h1>Send crews where risk and reports overlap</h1>
        <p className="lede">
          DrainSense puts historical flood records, rainfall and terrain, and residents' blocked-drain reports in one
          place. It scores where a large flood is most likely, says why, ranks where to inspect first, and checks
          afterwards whether repairs cut the incidents. Every number is open through a documented API.
        </p>
        <div className="hero-actions">
          <a className="btn primary" href="#map">Open the risk map</a>
          <a className="btn secondary" href="#dispatch">See the dispatch plan</a>
        </div>
      </section>

      {down && (
        <p className="error">
          The API at {API || "this site"} did not answer. Start it (see the README) or set NEXT_PUBLIC_API_URL, then reload.
        </p>
      )}

      {scoped && cells && (
        <p className="scope">
          <b>{picked ? picked.place_name : selectedId}</b>
          <span>
            {picked
              ? `picked on the map · rank ${picked.rank} of ${cells.length} · flood score ${picked.probability.toFixed(2)}`
              : "picked on the map · not in the scored cells"}
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

      <section className="stats" aria-label="Desk at a glance">
        <div className={`stat ${pickedBand ? pickedBand.key : ""}`}>
          <span>{picked ? "Selected cell flood score" : "Land cells scored for 2024"}</span>
          <b>{picked ? picked.probability.toFixed(2) : cells?.length ?? "—"}</b>
          {picked ? (
            <small>
              <BandPill value={pickedBand} /> {picked.above_threshold ? "at or above" : "below"} the model threshold{threshold}
            </small>
          ) : (
            <small>
              {counts ? (
                <>
                  <span className="band red">{counts.red} urgent</span>{" "}
                  <span className="band amber">{counts.amber} moderate</span>{" "}
                  <span className="band green">{counts.green} ok</span>
                </>
              ) : ""}
            </small>
          )}
        </div>
        <div className={`stat ${topBand ? topBand.key : ""}`}>
          <span>{picked ? "This cell on the dispatch plan" : "First on the dispatch plan"}</span>
          <b>{top ? top.place_name : picked ? picked.place_name : "—"}</b>
          <small>
            {top ? (
              <>
                <BandPill value={topBand} /> rank {top.rank} · priority {top.priority.toFixed(1)} · flood score {top.probability.toFixed(2)}
              </>
            ) : picked ? "not in the top 40 of the dispatch plan" : ""}
          </small>
        </div>
        <div className="stat">
          <span>Open drain reports</span>
          <b>{open?.length ?? "—"}</b>
          <small>{open ? `${open.filter((r) => r.origin === "synthetic").length} of them synthetic drill rows` : ""}</small>
        </div>
        <div className="stat">
          <span>Repairs with a before / after count</span>
          <b>{repairs ? repairs.length : "—"}</b>
          <small>
            {measured
              ? `latest: ${measured.incidents_365d_before} incidents the year before, ${measured.incidents_365d_after} after (${measured.origin})`
              : ""}
          </small>
        </div>
      </section>

      <section className="panel">
        <h2>What it answers</h2>
        <div className="answer-grid">
          {ANSWERS.map((row) => (
            <a key={row.part} href={row.href} className="answer">
              <span className="answer-problem">{row.problem}</span>
              <b>{row.part}</b>
              <span className="answer-text">{row.answer}</span>
            </a>
          ))}
        </div>
      </section>

      <section className="panel">
        <h2>The maintenance loop</h2>
        <p className="reading">From a single complaint to a measured repair, on one desk.</p>
        <ol className="loop">
          {LOOP.map(([step, line, href]) => (
            <li key={step}>
              <a href={href}><b>{step}</b></a>
              {line}
            </li>
          ))}
        </ol>
      </section>

      <section className="panel provenance">
        <h2>What is real, and what is not</h2>
        <ul className="reading">
          <li>Flood labels: Global Flood Records (DFO Flood Observatory), Zenodo, CC BY 4.0.</li>
          <li>Rainfall and elevation: Open-Meteo historical archive (ERA5 reanalysis) and its 90 m elevation model.</li>
          <li>Drain reports, inspections, repairs and incidents shipped with the desk are synthetic drill rows, marked <span className="origin synthetic">synthetic</span>. Rows saved here are marked <span className="origin community">community</span>.</li>
          <li>Sector flags are rules on the flood score, elevation and rainfall. They are not crop, disease or loss models.</li>
          <li>Cells are 5° on a side, about 500 km at the equator. A score is an outlook for the whole cell, not a street-level forecast and not a warning.</li>
        </ul>
      </section>
    </div>
  );
}