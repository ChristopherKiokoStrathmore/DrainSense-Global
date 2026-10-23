"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { api } from "../lib/api";

const RiskMap = dynamic(() => import("../components/RiskMap"), { ssr: false });

export default function MapPage() {
  const [features, setFeatures] = useState([]);
  const [hotspots, setHotspots] = useState([]);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancel = false;
    Promise.all([api("/api/v1/risk/grid"), api("/api/v1/risk/hotspots?limit=8")])
      .then(([grid, spots]) => {
        if (cancel) return;
        setFeatures(grid.features);
        setHotspots(spots.hotspots);
        if (spots.hotspots[0]) setSelected(spots.hotspots[0].cell_id);
      })
      .catch((err) => setError(err.message));
    return () => {
      cancel = true;
    };
  }, []);

  useEffect(() => {
    if (!selected) return;
    let cancel = false;
    api(`/api/v1/risk/cells/${selected}`)
      .then((row) => {
        if (!cancel) setSelected(row);
      })
      .catch((err) => setError(err.message));
    return () => {
      cancel = true;
    };
  }, [typeof selected === "string" ? selected : selected?.cell_id]);

  const detail = selected && typeof selected === "object" ? selected : null;
  const maxAbs = detail ? Math.max(...detail.shap.map((item) => Math.abs(item.log_odds)), 0.01) : 1;

  return (
    <div className="desk">
      <RiskMap
        features={features}
        selectedId={detail?.cell_id || (typeof selected === "string" ? selected : "")}
        onSelect={setSelected}
      />
      <aside className="dossier">
        <p className="kicker">2024 outlook · feature year 2023</p>
        <div className="legend">
          <span className="swatch" />
          <span>low probability → high</span>
        </div>
        {error && <p className="error">{error}</p>}
        {!detail && !error && <p>Loading the flood desk…</p>}
        {detail && (
          <>
            <h1 className="cell-name">{detail.cell_id.replace("_", " ")}</h1>
            <p className="kicker">{detail.center_lat}°, {detail.center_lon}° · elev {detail.elev_m} m</p>
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
            <p className="kicker">Why this score · SHAP on the log-odds</p>
            <ul className="shap">
              {detail.shap.map((item) => {
                const width = `${(Math.abs(item.log_odds) / maxAbs) * 50}%`;
                return (
                  <li key={item.feature}>
                    <span>{item.label}</span>
                    <span className="track" title={`${item.log_odds} · ${item.direction}`}>
                      <i />
                      <span className={item.log_odds >= 0 ? "bar up" : "bar down"} style={{ width }} />
                    </span>
                  </li>
                );
              })}
            </ul>
          </>
        )}
        <p className="kicker" style={{ marginTop: 22 }}>Hottest cells</p>
        <ol className="hotlist">
          {hotspots.map((spot) => (
            <li key={spot.cell_id}>
              <button type="button" onClick={() => setSelected(spot.cell_id)}>
                <strong>{String(spot.rank).padStart(2, "0")}</strong>
                <span>{spot.cell_id}</span>
                <span>{spot.probability.toFixed(2)}</span>
              </button>
            </li>
          ))}
        </ol>
      </aside>
    </div>
  );
}
