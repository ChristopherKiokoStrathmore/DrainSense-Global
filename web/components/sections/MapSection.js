"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errorDetail } from "../../lib/api";
import CellTrend from "../CellTrend";
import { useDesk } from "../desk";
import Timeline from "../Timeline";

const RiskMap = dynamic(() => import("../RiskMap"), { ssr: false });

// Report statuses the dispatch plan counts as unresolved.
const OPEN = new Set(["open", "inspection_scheduled"]);

// Same key and event name as RiskMap.jsx and the other pages.
const SELECTED_KEY = "drainsense:selected";
const SELECTED_EVENT = "drainsense:selected-change";
function publishSelected(id) {
  try {
    window.localStorage.setItem(SELECTED_KEY, id);
  } catch {
    // Storage can be blocked; the event below still updates this tab.
  }
  window.dispatchEvent(new Event(SELECTED_EVENT));
}

const SECTORS = [
  ["agri_suppliers", "Agriculture"],
  ["health", "Public health"],
  ["insurers_logistics", "Insurance & logistics"],
];

const PHASE_KICKER = {
  fit: "fitted year · in-sample",
  test: "held-out test year",
  outlook: "outlook · the prediction",
};

function featureValue(name, value) {
  if (value == null) return "";
  if (name === "wet_day_frac") return `${Math.round(value * 100)}% of days`;
  if (name === "heavy_day_count") return `${Math.round(value)} days`;
  if (name === "elev_m") return `${Math.round(value).toLocaleString()} m`;
  return `${Math.round(value).toLocaleString()} mm`;
}

// How the year's score did against the catalogue, in words.
function verdict(probability, flooded, threshold) {
  const flagged = probability >= threshold;
  if (flooded == null) return null;
  if (flooded && flagged) return ["hit", "A DFO large flood was recorded here, and the model had the cell at or above its threshold."];
  if (flooded) return ["miss", "A DFO large flood was recorded here, but the model scored the cell below its threshold."];
  if (flagged) return ["alarm", "The model flagged this cell; no DFO large flood was recorded here that year."];
  return ["quiet", "Below the threshold, and no DFO large flood was recorded here that year."];
}

export default function MapSection() {
  const [features, setFeatures] = useState([]);
  const [timeline, setTimeline] = useState(null);
  const [year, setYear] = useState(null);
  const [log, setLog] = useState({ reports: [], incidents: [], repairs: [] });
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const { revision, mapCell, openForm } = useDesk();

  // A deliberate pick: shown here and shared with the Overview, Dispatch, Sectors and Ledger pages.
  // The default top-hotspot selection on load does not go through here, so no page looks scoped before a pick.
  const select = useCallback((id) => {
    setSelectedId(id);
    publishSelected(id);
  }, []);

  useEffect(() => {
    let cancel = false;
    const wanted = new URLSearchParams(window.location.search).get("cell");
    Promise.all([api("/api/v1/risk/grid"), api("/api/v1/risk/hotspots?limit=1")])
      .then(([grid, spots]) => {
        if (cancel) return;
        setFeatures(grid.features);
        const known = grid.features.some((f) => f.properties.cell_id === wanted);
        setSelectedId((cur) => cur || (known ? wanted : spots.hotspots[0]?.cell_id || ""));
      })
      .catch((err) => setError(errorDetail(err)));
    // Without the timeline the map still shows the outlook, as before.
    api("/api/v1/risk/timeline")
      .then((body) => {
        if (cancel) return;
        setTimeline(body);
        setYear(body.outlook_year);
      })
      .catch(() => {});
    return () => {
      cancel = true;
    };
  }, []);

  // The drainage log only adds report dots and counts, so the map still works without it.
  useEffect(() => {
    let cancel = false;
    Promise.allSettled([api("/api/v1/reports"), api("/api/v1/incidents"), api("/api/v1/repairs")]).then(
      ([reports, incidents, repairs]) => {
        if (cancel) return;
        setLog({
          reports: reports.status === "fulfilled" ? reports.value.reports : [],
          incidents: incidents.status === "fulfilled" ? incidents.value.incidents : [],
          repairs: repairs.status === "fulfilled" ? repairs.value.repairs : [],
        });
      },
    );
    return () => {
      cancel = true;
    };
  }, [revision]);

  // "Show on the map" from another section.
  useEffect(() => {
    if (mapCell) select(mapCell.id);
  }, [mapCell, select]);

  useEffect(() => {
    if (!selectedId) return undefined;
    let cancel = false;
    const url = new URL(window.location.href);
    url.searchParams.set("cell", selectedId);
    window.history.replaceState(null, "", url);
    api(`/api/v1/risk/cells/${selectedId}`)
      .then((row) => {
        if (!cancel) setDetail(row);
      })
      .catch((err) => setError(errorDetail(err)));
    return () => {
      cancel = true;
    };
  }, [selectedId]);

  const at = timeline && year != null ? timeline.years.indexOf(year) : -1;
  const outlook = !timeline || year === timeline.outlook_year;
  const threshold = timeline?.threshold;

  // The grid's geometry with the chosen year's score. The outlook year is the grid as served.
  const yearFeatures = useMemo(() => {
    if (at < 0 || outlook) return features;
    return features.map((f) => {
      const row = timeline.cells[f.properties.cell_id];
      if (!row) return f;
      const probability = row.probability[at];
      return { ...f, properties: { ...f.properties, probability, above_threshold: probability >= threshold } };
    });
  }, [features, timeline, at, outlook, threshold]);

  // Ranks and the hottest cells for the year on show.
  const ranked = useMemo(
    () => [...yearFeatures].map((f) => f.properties).sort((a, b) => b.probability - a.probability),
    [yearFeatures],
  );
  const rankOf = useMemo(() => Object.fromEntries(ranked.map((p, i) => [p.cell_id, i + 1])), [ranked]);

  const floods = useMemo(() => {
    if (at < 0) return [];
    return features
      .filter((f) => timeline.cells[f.properties.cell_id]?.flooded[at] === 1)
      .map((f) => ({
        cell_id: f.properties.cell_id,
        lon: f.properties.center_lon,
        lat: f.properties.center_lat,
        caught: timeline.cells[f.properties.cell_id].probability[at] >= threshold,
      }));
  }, [features, timeline, at, threshold]);

  // Open drain reports drawn over the risk cells: where complaints and model risk overlap.
  const marks = useMemo(() => {
    const open = {};
    for (const report of log.reports) {
      if (OPEN.has(report.status)) open[report.cell_id] = (open[report.cell_id] || 0) + 1;
    }
    return features
      .filter((f) => open[f.properties.cell_id])
      .map((f) => ({
        cell_id: f.properties.cell_id,
        place_name: f.properties.place_name,
        lon: f.properties.center_lon,
        lat: f.properties.center_lat,
        open: open[f.properties.cell_id],
      }));
  }, [features, log.reports]);

  const ground = useMemo(() => {
    if (!detail) return null;
    const mine = (rows) => rows.filter((row) => row.cell_id === detail.cell_id);
    const reports = mine(log.reports);
    const incidents = mine(log.incidents);
    const repairs = mine(log.repairs);
    return {
      open: reports.filter((r) => OPEN.has(r.status)).length,
      incidents: incidents.length,
      repairs: repairs.length,
      synthetic: [...reports, ...incidents, ...repairs].some((row) => row.origin === "synthetic"),
    };
  }, [detail, log]);

  const pickYear = useCallback((value) => setYear(value), []);

  const series = detail && timeline?.cells[detail.cell_id];
  const probability = detail ? (series && at >= 0 ? series.probability[at] : detail.probability) : null;
  const flooded = series && at >= 0 ? series.flooded[at] : null;
  const shownYear = year ?? detail?.outlook_year;
  const phase = timeline?.summary[at]?.phase || "outlook";
  const judged = detail && threshold != null ? verdict(probability, flooded, threshold) : null;
  const maxAbs = detail ? Math.max(...detail.shap.map((item) => Math.abs(item.log_odds)), 0.01) : 1;

  return (
    <div className="desk">
      <RiskMap features={yearFeatures} marks={marks} floods={floods} year={shownYear} selectedId={selectedId} onSelect={select} />
      {timeline && <Timeline timeline={timeline} year={year} onYear={pickYear} />}
      <aside className="dossier">
        <p className="kicker">{shownYear ?? 2024} {PHASE_KICKER[phase]} · from {(shownYear ?? 2024) - 1} rainfall</p>
        <div className="legend">
          <span className="swatch" />
          <span>low → high</span>
          <span className="report-dot" aria-hidden="true" />
          <span>open reports</span>
          {!outlook && <><span className="flood-ring" aria-hidden="true" /><span>recorded flood</span></>}
        </div>
        {error && <p className="error">{error}</p>}
        {!detail && !error && <p>Loading the flood desk…</p>}
        {detail && (
          <>
            <h1 className="cell-name">{detail.place_name}</h1>
            <p className="kicker">
              Cell {detail.cell_id} · {detail.center_lat}°, {detail.center_lon}° · elev {detail.elev_m} m
            </p>
            <div className="score-row">
              <p className="score">{probability.toFixed(2)}</p>
              <div className="waterline" aria-hidden="true">
                <span style={{ height: `${Math.max(6, probability * 100)}%` }} />
              </div>
              {judged && <span className={`verdict ${judged[0]}`}>{{ hit: "Caught", miss: "Missed", alarm: "False alarm", quiet: "Quiet" }[judged[0]]}</span>}
              <p className="reading">
                Rank {rankOf[detail.cell_id] ?? detail.rank} of {ranked.length} in {shownYear}. Threshold{" "}
                {(threshold != null ? probability >= threshold : detail.above_threshold) ? "met" : "not met"}.{" "}
                {outlook ? detail.reading : judged?.[1]}
              </p>
            </div>

            {series && (
              <>
                <p className="kicker">Flood score by year · pick a year to repaint the map</p>
                <CellTrend years={timeline.years} probability={series.probability} flooded={series.flooded}
                  threshold={threshold} outlookYear={timeline.outlook_year} year={year} onYear={pickYear} />
              </>
            )}
            <p className="reading">
              <b>Flood record.</b> A DFO large flood was centred in this cell in{" "}
              {series ? `${series.flooded.filter((f) => f === 1).length} of the ${series.flooded.filter((f) => f != null).length} labelled years` : `${detail.dfo_positive_years_2014_2018} of the 5 training years, 2014–2018`}.
            </p>

            <p className="kicker">Why the {detail.outlook_year} outlook scores as it does · SHAP on the log-odds</p>
            {!outlook && <p className="reading muted">Shown for the outlook year. Earlier years use the same model on their own rainfall.</p>}
            <ul className="shap">
              {detail.shap.map((item) => {
                const width = `${(Math.abs(item.log_odds) / maxAbs) * 50}%`;
                return (
                  <li key={item.feature}>
                    <span>{item.label} <small>{featureValue(item.feature, detail.features[item.feature])}</small></span>
                    <span className="track" title={`${item.log_odds} · ${item.direction}`}>
                      <i />
                      <span className={item.log_odds >= 0 ? "bar up" : "bar down"} style={{ width }} />
                    </span>
                  </li>
                );
              })}
            </ul>

            <p className="kicker" style={{ marginTop: 22 }}>On the ground · drainage ledger</p>
            {ground && (
              <p className="reading">
                {ground.open} open drain {ground.open === 1 ? "report" : "reports"} · {ground.incidents} incidents
                logged · {ground.repairs} {ground.repairs === 1 ? "repair" : "repairs"}.
                {ground.synthetic && " Some of these rows are synthetic drill data."}
              </p>
            )}
            <p className="actions">
              <button type="button" className="link" onClick={() => openForm("report", detail.cell_id)}>Report a blocked drain here</button>
              <button type="button" className="link" onClick={() => openForm("incident", detail.cell_id)}>Log an incident</button>
              <a href="#dispatch">Dispatch list</a>
            </p>

            <p className="kicker" style={{ marginTop: 22 }}>Sector flags · from the {detail.outlook_year} outlook</p>
            <p className="flags">
              {SECTORS.map(([key, label]) => (
                <span key={key} className={detail.sectors[key].active ? "pill on" : "pill"}>
                  {label}: {detail.sectors[key].active ? "flag on" : "off"}
                </span>
              ))}
            </p>
          </>
        )}
        <p className="kicker" style={{ marginTop: 22 }}>Hottest cells in {shownYear ?? 2024}</p>
        <ol className="hotlist">
          {ranked.slice(0, 8).map((spot, i) => (
            <li key={spot.cell_id}>
              <button type="button" aria-current={spot.cell_id === selectedId ? "true" : undefined} onClick={() => select(spot.cell_id)}>
                <strong>{String(i + 1).padStart(2, "0")}</strong>
                <span title={spot.cell_id}>
                  {spot.place_name}
                  {at >= 0 && timeline.cells[spot.cell_id]?.flooded[at] === 1 && <span className="flood-ring inline" title="DFO flood recorded" />}
                </span>
                <span>{spot.probability.toFixed(2)}</span>
              </button>
            </li>
          ))}
        </ol>
      </aside>
    </div>
  );
}