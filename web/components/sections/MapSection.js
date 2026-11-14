"use client";

import dynamic from "next/dynamic";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, errorDetail } from "../../lib/api";
import CellTrend from "../CellTrend";
import { useDesk } from "../desk";
import Timeline from "../Timeline";

const RiskMap = dynamic(() => import("../RiskMap"), { ssr: false });

// Report statuses the dispatch plan counts as unresolved.
const OPEN = new Set(["open", "inspection_scheduled"]);

// Same key and event name as RiskMap.js and the other tabs.
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

// The map's data is shared by the map behind the panel and the Risk map tab inside it, so it lives here.
const MapContext = createContext(null);
export const useRiskMap = () => useContext(MapContext);

export function MapProvider({ children }) {
  const [features, setFeatures] = useState([]);
  const [timeline, setTimeline] = useState(null);
  const [year, setYear] = useState(null);
  const [log, setLog] = useState({ reports: [], incidents: [], repairs: [] });
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  // What the map draws: "heat" or "cells", and the two overlays.
  const [view, setView] = useState("heat");
  const [showReports, setShowReports] = useState(true);
  const [showFloods, setShowFloods] = useState(true);
  const [focus, setFocus] = useState(null);
  // The two-week forecast, loaded by the Forecast tab. The map shows it while that tab is open.
  const [forecast, setForecast] = useState(null);
  const { revision, mapCell } = useDesk();

  // A deliberate pick: shown here and shared with the Overview, Dispatch, Sectors, Report and Tracker tabs.
  // The default top-hotspot selection on load does not go through here, so no tab looks scoped before a pick.
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
        // A ?cell= link is a pick someone shared, so it is published like one.
        if (known) select(wanted);
        else setSelectedId((cur) => cur || spots.hotspots[0]?.cell_id || "");
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

  // "Show on the map" from another tab: select the cell and fly to it.
  useEffect(() => {
    if (!mapCell) return;
    select(mapCell.id);
    setFocus(mapCell);
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

  const pickYear = useCallback((value) => setYear(value), []);
  // Pick a cell and fly the map to it, staying on the current tab.
  const focusCell = useCallback((id) => {
    select(id);
    setFocus({ id, at: Date.now() });
  }, [select]);
  const shownYear = year ?? detail?.outlook_year;

  const value = {
    features, yearFeatures, timeline, year, pickYear, shownYear, at, outlook, threshold, ranked, floods, marks, log,
    selectedId, select, focusCell, forecast, setForecast, detail, error, view, setView, showReports, setShowReports, showFloods, setShowFloods, focus,
  };
  return <MapContext.Provider value={value}>{children}</MapContext.Provider>;
}

// Where each forecast level sits on the map's colour ramp: likely red-orange, watch amber, quiet near the floor.
const FORECAST_SHADE = { likely: 0.45, watch: 0.2, quiet: 0.02 };
const LEVEL_WORD = { likely: "likely affected", watch: "on watch", quiet: "quiet" };

/** The map behind everything, and its legend. On the Forecast tab it shows the two-week forecast levels. */
export function MapCanvas() {
  const m = useRiskMap();
  const { tab } = useDesk();
  const forecastMode = tab === "forecast" && Boolean(m.forecast);

  const forecastFeatures = useMemo(() => {
    if (!m.forecast) return null;
    const byId = Object.fromEntries(m.forecast.cells.map((c) => [c.cell_id, c]));
    return m.features.map((f) => {
      const cell = byId[f.properties.cell_id];
      if (!cell) return f;
      const tip = `${cell.place_name} · ${LEVEL_WORD[cell.level]} to ${m.forecast.until} · ${Math.round(cell.window_total_mm)} mm forecast`;
      return { ...f, properties: { ...f.properties, probability: FORECAST_SHADE[cell.level], tip } };
    });
  }, [m.forecast, m.features]);

  return (
    <div className="mapwrap">
      <RiskMap features={forecastMode ? forecastFeatures : m.yearFeatures} marks={m.marks} floods={forecastMode ? [] : m.floods}
        year={m.shownYear} selectedId={m.selectedId} onSelect={m.select} view={m.view} showReports={m.showReports}
        showFloods={m.showFloods} focus={m.focus} />
      {forecastMode ? (
        <div className="map-legend" aria-label="Map legend">
          <span>Forecast to {m.forecast.until}:</span>
          {/* Swatches in the map's own colours for each level (the ramp in RiskMap.js at FORECAST_SHADE). */}
          <i className="lvl" style={{ background: "#e6703f" }} /><span>likely</span>
          <i className="lvl" style={{ background: "#d7a15a" }} /><span>watch</span>
          <i className="lvl" style={{ background: "#1a4246" }} /><span>quiet</span>
          {m.showReports && <><span className="report-dot" aria-hidden="true" /><span>open reports</span></>}
        </div>
      ) : (
        <div className="map-legend" aria-label="Map legend">
          <span className="swatch" />
          <span>low → high flood score</span>
          {m.showReports && <><span className="report-dot" aria-hidden="true" /><span>open reports</span></>}
          {m.showFloods && !m.outlook && <><span className="flood-ring" aria-hidden="true" /><span>recorded flood</span></>}
        </div>
      )}
    </div>
  );
}

/** The Risk map tab: headline figures, the year to show, the map layers, and the selected cell explained. */
export function MapPanel() {
  const m = useRiskMap();
  const { openForm } = useDesk();
  const { detail, timeline, at, outlook, threshold, ranked, shownYear, year, pickYear, selectedId, select } = m;

  const rankOf = useMemo(() => Object.fromEntries(ranked.map((p, i) => [p.cell_id, i + 1])), [ranked]);
  const flagged = threshold != null ? ranked.filter((p) => p.probability >= threshold).length : ranked.filter((p) => p.above_threshold).length;
  const openReports = m.log.reports.filter((r) => OPEN.has(r.status)).length;

  const ground = useMemo(() => {
    if (!detail) return null;
    const mine = (rows) => rows.filter((row) => row.cell_id === detail.cell_id);
    const reports = mine(m.log.reports);
    const incidents = mine(m.log.incidents);
    const repairs = mine(m.log.repairs);
    return {
      open: reports.filter((r) => OPEN.has(r.status)).length,
      incidents: incidents.length,
      repairs: repairs.length,
    };
  }, [detail, m.log]);

  const series = detail && timeline?.cells[detail.cell_id];
  const probability = detail ? (series && at >= 0 ? series.probability[at] : detail.probability) : null;
  const flooded = series && at >= 0 ? series.flooded[at] : null;
  const phase = timeline?.summary[at]?.phase || "outlook";
  const judged = detail && threshold != null ? verdict(probability, flooded, threshold) : null;
  const maxAbs = detail ? Math.max(...detail.shap.map((item) => Math.abs(item.log_odds)), 0.01) : 1;

  return (
    <>
      <div className="kpis">
        <div className="kpi hot"><b>{ranked.length ? flagged : "—"}</b><span>Cells flagged in {shownYear ?? "—"}</span></div>
        <div className="kpi"><b>{m.outlook ? "—" : m.floods.length}</b><span>{m.outlook ? "No flood record yet" : "Recorded floods"}</span></div>
        <div className="kpi"><b>{openReports}</b><span>Open drain reports</span></div>
      </div>

      <h2 className="sub">Year on the map</h2>
      {timeline ? <Timeline timeline={timeline} year={year} onYear={pickYear} /> : <p className="mute">Showing the {shownYear ?? 2024} outlook.</p>}

      <h2 className="sub">Map layer</h2>
      <select value={m.view} onChange={(event) => m.setView(event.target.value)} aria-label="Map layer">
        <option value="heat">Heatmap · smoothed concentration</option>
        <option value="cells">Cells · exact score per 5° cell</option>
      </select>
      <div className="checks">
        <label className="check"><input type="checkbox" checked={m.showReports} onChange={(e) => m.setShowReports(e.target.checked)} /> Open drain reports</label>
        <label className="check"><input type="checkbox" checked={m.showFloods} onChange={(e) => m.setShowFloods(e.target.checked)} /> Recorded floods</label>
      </div>

      <h2 className="sub">Selected cell</h2>
      {m.error && <p className="error">{m.error}</p>}
      {!detail && !m.error && <p className="mute">Loading the flood desk…</p>}
      {detail && (
        <div className="card cell">
          <p className="kicker">{shownYear ?? 2024} {PHASE_KICKER[phase]} · from {(shownYear ?? 2024) - 1} rainfall</p>
          <h3 className="cell-name">{detail.place_name}</h3>
          <p className="mute">
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

          <p className="kicker" style={{ marginTop: 16 }}>On the ground · drainage ledger</p>
          {ground && (
            <p className="reading">
              {ground.open} open drain {ground.open === 1 ? "report" : "reports"} · {ground.incidents} incidents
              logged · {ground.repairs} {ground.repairs === 1 ? "repair" : "repairs"}.
            </p>
          )}
          <div className="btn-row">
            <button type="button" className="act" onClick={() => openForm("report", detail.cell_id)}>Report a blocked drain</button>
            <button type="button" className="act ghost" onClick={() => openForm("incident", detail.cell_id)}>Log an incident</button>
          </div>

          <p className="kicker" style={{ marginTop: 16 }}>Sector flags · from the {detail.outlook_year} outlook</p>
          <p className="flags">
            {SECTORS.map(([key, label]) => (
              <span key={key} className={detail.sectors[key].active ? "pill on" : "pill"}>
                {label}: {detail.sectors[key].active ? "flag on" : "off"}
              </span>
            ))}
          </p>
        </div>
      )}

      <h2 className="sub">Hottest cells in {shownYear ?? 2024}</h2>
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
      <p className="mute">
        Scores are a yearly outlook for a whole 5° cell (about 500 km across), from the previous year's rainfall and
        elevation. Not a street-level forecast and not a warning.{" "}
        {/* <a href="#model">Model card</a> ·{" "} */}
        <a href="#dispatch">Dispatch plan</a>
      </p>
    </>
  );
}
