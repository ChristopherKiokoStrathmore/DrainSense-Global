"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, errorDetail } from "../../lib/api";

const RiskMap = dynamic(() => import("../../components/RiskMap"), { ssr: false });

// Report statuses the dispatch plan counts as unresolved.
const OPEN = new Set(["open", "inspection_scheduled"]);

const SECTORS = [
  ["agri_suppliers", "Agriculture"],
  ["health", "Public health"],
  ["insurers_logistics", "Insurance & logistics"],
];

function featureValue(name, value) {
  if (value == null) return "";
  if (name === "wet_day_frac") return `${Math.round(value * 100)}% of days`;
  if (name === "heavy_day_count") return `${Math.round(value)} days`;
  if (name === "elev_m") return `${Math.round(value).toLocaleString()} m`;
  return `${Math.round(value).toLocaleString()} mm`;
}

export default function MapPage() {
  const [features, setFeatures] = useState([]);
  const [hotspots, setHotspots] = useState([]);
  const [log, setLog] = useState({ reports: [], incidents: [], repairs: [] });
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancel = false;
    const wanted = new URLSearchParams(window.location.search).get("cell");
    Promise.all([api("/api/v1/risk/grid"), api("/api/v1/risk/hotspots?limit=8")])
      .then(([grid, spots]) => {
        if (cancel) return;
        setFeatures(grid.features);
        setHotspots(spots.hotspots);
        const known = grid.features.some((f) => f.properties.cell_id === wanted);
        setSelectedId(known ? wanted : spots.hotspots[0]?.cell_id || "");
      })
      .catch((err) => setError(errorDetail(err)));
    // The drainage log only adds report dots and counts, so the map still works without it.
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
  }, []);

  useEffect(() => {
    if (!selectedId) return undefined;
    let cancel = false;
    window.history.replaceState(null, "", `?cell=${selectedId}`);
    api(`/api/v1/risk/cells/${selectedId}`)
      .then((row) => {
        if (!cancel) setDetail(row);
      })
      .catch((err) => setError(errorDetail(err)));
    return () => {
      cancel = true;
    };
  }, [selectedId]);

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

  const maxAbs = detail ? Math.max(...detail.shap.map((item) => Math.abs(item.log_odds)), 0.01) : 1;

  return (
    <div className="desk">
      <RiskMap features={features} marks={marks} selectedId={selectedId} onSelect={setSelectedId} />
      <aside className="dossier">
        <p className="kicker">2024 outlook · feature year 2023</p>
        <div className="legend">
          <span className="swatch" />
          <span>low probability → high</span>
          <span className="report-dot" aria-hidden="true" />
          <span>open drain reports</span>
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
              <p className="score">{detail.probability.toFixed(2)}</p>
              <div className="waterline" aria-hidden="true">
                <span style={{ height: `${Math.max(6, detail.probability * 100)}%` }} />
              </div>
              <p className="reading">
                Rank {detail.rank}. Threshold {detail.above_threshold ? "met" : "not met"}.
                {" "}{detail.reading}
              </p>
            </div>
            <p className="reading">
              <b>Flood record.</b> A DFO large flood was centred in this cell in{" "}
              {detail.dfo_positive_years_2014_2018} of the 5 training years, 2014–2018.
            </p>

            <p className="kicker">Why this score · SHAP on the log-odds</p>
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
              <Link href={`/operations?cell=${detail.cell_id}&form=report#record`}>Report a blocked drain here</Link>
              <Link href={`/operations?cell=${detail.cell_id}&form=incident#record`}>Log an incident</Link>
              <Link href="/response">Dispatch list</Link>
            </p>

            <p className="kicker" style={{ marginTop: 22 }}>Sector flags · derived from this score</p>
            <p className="flags">
              {SECTORS.map(([key, label]) => (
                <span key={key} className={detail.sectors[key].active ? "pill on" : "pill"}>
                  {label}: {detail.sectors[key].active ? "flag on" : "off"}
                </span>
              ))}
            </p>
          </>
        )}
        <p className="kicker" style={{ marginTop: 22 }}>Hottest cells</p>
        <ol className="hotlist">
          {hotspots.map((spot) => (
            <li key={spot.cell_id}>
              <button type="button" onClick={() => setSelectedId(spot.cell_id)}>
                <strong>{String(spot.rank).padStart(2, "0")}</strong>
                <span title={spot.cell_id}>{spot.place_name}</span>
                <span>{spot.probability.toFixed(2)}</span>
              </button>
            </li>
          ))}
        </ol>
      </aside>
    </div>
  );
}
