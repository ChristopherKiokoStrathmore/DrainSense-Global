"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { api, errorDetail } from "../../lib/api";

// The same rules as sector_indicators in api/app/scoring.py, in short. The API's own statement is shown per cell.
const SECTORS = [
  {
    key: "agri_suppliers",
    title: "Agriculture",
    who: "Farm suppliers planning stock and inputs",
    rule: (i) => `Flood score at or above ${i.threshold}, and the previous year wetter than the training median (${Math.round(i.train_median_rain_mm).toLocaleString()} mm).`,
  },
  {
    key: "health",
    title: "Public health",
    who: "Health teams watching water-borne and vector-borne disease",
    rule: (i) => `Flood score at or above ${i.threshold}, and the cell lies under 200 m.`,
  },
  {
    key: "insurers_logistics",
    title: "Insurance & logistics",
    who: "Insurers and logistics planners pricing exposure and routes",
    rule: (i) => `Flood score at or above ${i.threshold}, or a DFO large flood centred here in 2 or more of 2014–2018.`,
  },
];

// The usual list is the top 30 cells. With a cell picked on the map, fetch them all so that cell is found wherever it ranks.
const TOP = 30;
const ALL_CELLS = 500;

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

const words = (field) => field.replaceAll("_", " ");
const shown = (value) => (typeof value === "boolean" ? (value ? "yes" : "no") : typeof value === "number" ? Number(value.toFixed(3)).toLocaleString() : value);

export default function SectorsSection() {
  const [body, setBody] = useState(null);
  const [error, setError] = useState("");
  const [index, setIndex] = useState(0);
  // null on the server and until the browser has read it, so the first render matches.
  const selectedId = useSyncExternalStore(subscribeSelected, readSelected, () => null);
  const scoped = Boolean(selectedId);

  useEffect(() => {
    api(`/api/v1/sectors?limit=${scoped ? ALL_CELLS : TOP}`).then(setBody).catch((err) => setError(errorDetail(err)));
  }, [scoped]);

  const all = body?.cards || [];
  const top = all.slice(0, TOP);
  const first = all[0]?.indicators;

  // With a cell picked: its row and its detail cards. Otherwise the top list, with a row to browse.
  const pickedCard = scoped ? all.find((c) => c.cell.cell_id === selectedId) : null;
  const rows = scoped ? (pickedCard ? [pickedCard] : []) : top;
  const card = scoped ? pickedCard : top[index];

  return (
    <div className="sheet wide">
      <p className="banner">Derived indicators · not forecasts of yield, disease, or loss</p>
      <h1>Sector flags</h1>
      <p className="reading">
        The same flood risk matters to farm suppliers, health teams, insurers and logistics planners. Rather than each
        rebuilding the picture, the desk turns the flood score into an attention flag per sector with a fixed, published
        rule. {body?.disclaimer}
      </p>
      {error && <p className="error">{error}</p>}

      {scoped && body && (
        <p className="scope">
          <b>{pickedCard ? pickedCard.cell.place_name : selectedId}</b>
          <span>{pickedCard ? `picked on the map · rank ${pickedCard.cell.rank}` : "picked on the map · no sector card for this cell"}</span>
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

      {first && (
        <div className="cards" style={{ marginTop: 18 }}>
          {SECTORS.map((sector) => {
            const flagged = top.filter((c) => c.indicators[sector.key].active).length;
            return (
              <article className="card" key={sector.key}>
                <p className="kicker">{sector.who}</p>
                <h2>{sector.title}</h2>
                <p className="reading"><b>Flag when:</b> {sector.rule({
                  threshold: body.threshold,
                  train_median_rain_mm: first.agri_suppliers.inputs.train_median_rain_mm,
                })}</p>
                <p className="reading">On in {flagged} of the {top.length} highest-scoring cells.</p>
                {pickedCard && (
                  <p className="reading">
                    This cell: <b>{pickedCard.indicators[sector.key].active ? "flag on" : "flag off"}</b>
                  </p>
                )}
              </article>
            );
          })}
        </div>
      )}

      {rows.length > 0 && (
        <>
          <h2>{scoped ? "The picked cell" : `The ${rows.length} highest-scoring cells`}</h2>
          {!scoped && <p className="reading">Pick a row to see the inputs behind each flag.</p>}
          <div className="scroll">
            <table className="matrix">
              <thead>
                <tr>
                  <th>Rank</th><th>Place</th><th className="num">Flood score</th>
                  {SECTORS.map((s) => <th key={s.key}>{s.title}</th>)}
                </tr>
              </thead>
              <tbody>
                {rows.map((item, i) => (
                  <tr key={item.cell.cell_id} className={item === card ? "on" : ""}>
                    <td>{item.cell.rank}</td>
                    <td>
                      <button type="button" className="link" onClick={() => !scoped && setIndex(i)} title={item.cell.cell_id}>
                        {item.cell.place_name}
                      </button>
                    </td>
                    <td className="num">{item.cell.probability.toFixed(2)}</td>
                    {SECTORS.map((s) => (
                      <td key={s.key}>
                        {item.indicators[s.key].active ? <span className="pill on">flag on</span> : <span className="muted">off</span>}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {card && (
        <>
          <h2>{card.cell.place_name} · flood score {card.cell.probability.toFixed(2)}</h2>
          <div className="cards">
            {SECTORS.map((sector) => {
              const indicator = card.indicators[sector.key];
              return (
                <article className="card" key={sector.key}>
                  <p className="kicker">{indicator.kind}</p>
                  <h2>{sector.title}</h2>
                  <span className={indicator.active ? "pill on" : "pill"}>{indicator.active ? "flag on" : "flag off"}</span>
                  <p className="reading">{indicator.statement}</p>
                  <table>
                    <tbody>
                      {Object.entries(indicator.inputs).map(([name, value]) => (
                        <tr key={name}><td>{words(name)}</td><td className="num">{shown(value)}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </article>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}