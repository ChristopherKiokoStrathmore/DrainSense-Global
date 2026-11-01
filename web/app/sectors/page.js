"use client";

import { useEffect, useState } from "react";
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

const words = (field) => field.replaceAll("_", " ");
const shown = (value) => (typeof value === "boolean" ? (value ? "yes" : "no") : typeof value === "number" ? Number(value.toFixed(3)).toLocaleString() : value);

export default function SectorsPage() {
  const [body, setBody] = useState(null);
  const [error, setError] = useState("");
  const [index, setIndex] = useState(0);

  useEffect(() => {
    api("/api/v1/sectors?limit=30").then(setBody).catch((err) => setError(errorDetail(err)));
  }, []);

  const cards = body?.cards || [];
  const card = cards[index];
  const first = cards[0]?.indicators;

  return (
    <main className="sheet wide">
      <p className="banner">Derived indicators · not forecasts of yield, disease, or loss</p>
      <h1>Sector flags</h1>
      <p className="reading">
        The same flood risk matters to farm suppliers, health teams, insurers and logistics planners. Rather than each
        rebuilding the picture, the desk turns the flood score into an attention flag per sector with a fixed, published
        rule. {body?.disclaimer}
      </p>
      {error && <p className="error">{error}</p>}

      {first && (
        <div className="cards" style={{ marginTop: 18 }}>
          {SECTORS.map((sector) => {
            const flagged = cards.filter((c) => c.indicators[sector.key].active).length;
            return (
              <article className="card" key={sector.key}>
                <p className="kicker">{sector.who}</p>
                <h2>{sector.title}</h2>
                <p className="reading"><b>Flag when:</b> {sector.rule({
                  threshold: body.threshold,
                  train_median_rain_mm: first.agri_suppliers.inputs.train_median_rain_mm,
                })}</p>
                <p className="reading">On in {flagged} of the {cards.length} highest-scoring cells.</p>
              </article>
            );
          })}
        </div>
      )}

      {cards.length > 0 && (
        <>
          <h2>The {cards.length} highest-scoring cells</h2>
          <p className="reading">Pick a row to see the inputs behind each flag.</p>
          <div className="scroll">
            <table className="matrix">
              <thead>
                <tr>
                  <th>Rank</th><th>Cell</th><th className="num">Flood score</th>
                  {SECTORS.map((s) => <th key={s.key}>{s.title}</th>)}
                </tr>
              </thead>
              <tbody>
                {cards.map((item, i) => (
                  <tr key={item.cell.cell_id} className={i === index ? "on" : ""}>
                    <td>{item.cell.rank}</td>
                    <td><button type="button" className="link" onClick={() => setIndex(i)}>{item.cell.cell_id}</button></td>
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
          <h2>{card.cell.cell_id} · flood score {card.cell.probability.toFixed(2)}</h2>
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
    </main>
  );
}
