"use client";

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";
import { API, api } from "../lib/api";

// Report statuses the dispatch plan counts as unresolved.
const OPEN = new Set(["open", "inspection_scheduled"]);
const fmt = (value, places = 2) => (value == null ? "—" : Number(value).toFixed(places));

// The cell picked on the map (written by RiskMap). Same key and event name as RiskMap.jsx.
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
    href: "/map",
    answer: "One flood score per land cell from rainfall and elevation, trained on the DFO flood catalogue, with open drain reports drawn on top.",
  },
  {
    problem: "Crews are sent where the last complaint came from.",
    part: "Dispatch plan",
    href: "/response",
    answer: "Cells ranked by flood score, unresolved reports and repeat incidents together. Every point is shown with its source.",
  },
  {
    problem: "Nobody can say afterwards whether a repair cut the number of incidents.",
    part: "Drainage ledger",
    href: "/operations",
    answer: "Report, inspection, repair and incident in one log per cell, with incidents counted the year before and after each repair.",
  },
  {
    problem: "Risk scores are black boxes: no reason, no measured accuracy, no line between real and filler data.",
    part: "Model card",
    href: "/model",
    answer: "Why each cell scores as it does (SHAP), how the model does on later years against dummy baselines, and every synthetic row marked.",
  },
  {
    problem: "Farm suppliers, health teams, insurers and logistics planners each rebuild the same flood picture.",
    part: "Sector flags",
    href: "/sectors",
    answer: "Fixed, published rules turn the flood score into attention flags for agriculture, public health, and insurance and logistics.",
  },
  {
    problem: "The size of a recorded flood, and what drove it, is hard to compare across events.",
    part: "Impact model",
    href: "/impact",
    answer: "A CatBoost estimate of the DFO flood impact index for one event or 500, shown next to the exact formula.",
  },
];

const LOOP = [
  ["Report", "A resident reports a blocked drain in a cell.", "/operations"],
  ["Score", "The flood model's outlook for that cell, and why.", "/map"],
  ["Rank", "Score, open reports and repeat incidents set the order.", "/response"],
  ["Inspect", "A crew is scheduled against the report.", "/operations"],
  ["Repair", "The fix is logged and the report closed.", "/operations"],
  ["Measure", "Incidents the year before and after the repair.", "/operations"],
];

export default function OverviewPage() {
  const [facts, setFacts] = useState({});
  const [down, setDown] = useState(false);
  // null on the server and until the browser has read it, so the first render matches.
  const selectedId = useSyncExternalStore(subscribeSelected, readSelected, () => null);

  // Each figure loads on its own, so one failing call leaves a dash rather than a blank page.
  // The lists are kept whole, so a cell picked on the map can be filtered out of them without another request.
  useEffect(() => {
    Promise.allSettled([
      api("/api/v1/risk/grid"),
      api("/api/v1/reports"),
      api("/api/v1/interventions/effectiveness"),
      api("/api/v1/response/plan?limit=500"),
      api("/api/v1/metrics"),
      api("/api/v1/impact/model"),
    ]).then(([grid, reports, effect, plan, metrics, impact]) => {
      const value = (result) => (result.status === "fulfilled" ? result.value : null);
      setFacts({
        cells: value(grid)?.features.map((f) => f.properties),
        reports: value(reports)?.reports,
        repairs: value(effect)?.repairs,
        plan: value(plan)?.plan,
        metrics: value(metrics),
        impact: value(impact),
      });
      // fetch() rejects with a TypeError only when the API could not be reached at all.
      setDown([grid, reports, effect, plan, metrics, impact].every((r) => r.status === "rejected" && r.reason instanceof TypeError));
    });
  }, []);

  const { metrics, impact, plan } = facts;

  // Everything below is global, or narrowed to the picked cell when there is one.
  const picked = selectedId ? facts.cells?.find((c) => c.cell_id === selectedId) : null;
  const inScope = (row) => !picked || row.cell_id === picked.cell_id;

  const cells = facts.cells;
  const open = facts.reports?.filter((r) => OPEN.has(r.status) && inScope(r));
  const repairs = facts.repairs?.filter(inScope);
  const measured = repairs?.at(-1);

  const topIndex = picked ? plan?.findIndex((p) => p.cell_id === picked.cell_id) ?? -1 : 0;
  const top = plan && topIndex >= 0 ? plan[topIndex] : null;
  const threshold = metrics?.model.threshold;
  const placeName = (cell) => cell.place_name || cell.cell_id;

  return (
    <main className="sheet wide overview">
      <section className="hero">
        <div className="hero-copy">
          <p className="kicker">Flood-risk and drainage desk</p>
          <h1>Send crews where risk and reports overlap</h1>
          <p className="lede">
            DrainSense puts historical flood records, rainfall and terrain, and residents' blocked-drain reports in one
            place. It scores where a large flood is most likely, says why, ranks where to inspect first, and checks
            afterwards whether repairs cut the incidents. Every number is open through a documented API.
          </p>
          <div className="hero-actions">
            <Link className="btn primary" href="/map">Open the risk map</Link>
            <Link className="btn secondary" href="/response">See the dispatch plan</Link>
          </div>
        </div>

        <div className="glance">
          {down && (
            <p className="error">
              The API at {API || "this site"} did not answer. Start it (see the README) or set NEXT_PUBLIC_API_URL, then reload.
            </p>
          )}

          {picked && (
            <p className="scope">
              <b>{placeName(picked)}</b>
              <span>picked on the map</span>
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
            {picked ? (
              <div className="stat picked">
                <span>Flood score for 2024, picked cell</span>
                <b>{fmt(picked.probability)}</b>
                <small>
                  {picked.above_threshold ? "At or above" : "Below"} the model threshold{threshold != null ? ` ${threshold}` : ""} · 1 of {cells.length} cells
                </small>
              </div>
            ) : (
              <div className="stat">
                <span>Land cells scored for 2024</span>
                <b>{cells?.length ?? "—"}</b>
                <small>
                  {cells ? `${cells.filter((c) => c.above_threshold).length} at or above the model threshold${threshold != null ? ` ${threshold}` : ""}` : ""}
                </small>
              </div>
            )}
            <div className={picked ? "stat picked" : "stat"}>
              <span>{picked ? "Dispatch plan, picked cell" : "First on the dispatch plan"}</span>
              <b className="name">{picked ? placeName(picked) : top ? top.place_name : "—"}</b>
              <small>
                {top
                  ? `${picked ? `rank ${topIndex + 1} of ${plan.length} · ` : ""}priority ${top.priority.toFixed(1)} · flood score ${top.probability.toFixed(2)}`
                  : picked && plan
                    ? "not on the dispatch plan"
                    : ""}
              </small>
            </div>
            <div className={picked ? "stat picked" : "stat"}>
              <span>Open drain reports</span>
              <b>{open?.length ?? "—"}</b>
              <small>{open ? `${open.filter((r) => r.origin === "synthetic").length} of them synthetic drill rows` : ""}</small>
            </div>
            <div className={picked ? "stat picked" : "stat"}>
              <span>Repairs with a before / after count</span>
              <b>{repairs ? repairs.length : "—"}</b>
              <small>
                {measured
                  ? `latest: ${measured.incidents_365d_before} incidents the year before, ${measured.incidents_365d_after} after (${measured.origin})`
                  : picked && repairs
                    ? "no repairs logged in this cell"
                    : ""}
              </small>
            </div>
            {/*<div className="stat">*/}
            {/*  <span>Risk model ROC-AUC, later years</span>*/}
            {/*  <b>{metrics ? fmt(metrics.temporal_test.roc_auc, 3) : "—"}</b>*/}
            {/*  <small>{metrics ? `test years ${metrics.split.test_label_years[0]}–${metrics.split.test_label_years.at(-1)} · dummy ${fmt(metrics.dummy_prior.roc_auc, 3)}` : ""}</small>*/}
            {/*</div>*/}
            {/*<div className="stat">*/}
            {/*  <span>Impact model test RMSE</span>*/}
            {/*  <b>{impact ? fmt(impact.variants.brief.temporal_test.rmse, 3) : "—"}</b>*/}
            {/*  <small>{impact ? `${impact.split.n_test} held-out events · exact formula ${fmt(impact.baselines_temporal_test.dfo_formula.rmse, 3)}` : ""}</small>*/}
            {/*</div>*/}
          </section>
        </div>
      </section>

      <section className="panel">
        <h2>What it answers</h2>
        <div className="answer-grid">
          {ANSWERS.map((row) => (
            <Link key={row.part} href={row.href} className="answer">
              <span className="answer-problem">{row.problem}</span>
              <b>{row.part}</b>
              <span className="answer-text">{row.answer}</span>
            </Link>
          ))}
          <Link href="/endpoints" className="answer answer-wide">
            <span className="answer-problem">Each tool keeps its numbers to itself.</span>
            <b>API</b>
            <span className="answer-text">Every page reads the same documented endpoints, with Swagger, a published schema and a Postman collection.</span>
          </Link>
        </div>
      </section>

      <section className="panel">
        <h2>The maintenance loop</h2>
        <p className="reading">From a single complaint to a measured repair, on one desk.</p>
        <ol className="loop">
          {LOOP.map(([step, line, href]) => (
            <li key={step}>
              <Link href={href}><b>{step}</b></Link>
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
    </main>
  );
}