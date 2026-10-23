"use client";

import { useEffect, useState } from "react";
import { api } from "../../lib/api";

const KEYS = [
  ["agri_suppliers", "Agri suppliers"],
  ["health", "Health"],
  ["insurers_logistics", "Insurers / logistics"],
];

export default function SectorsPage() {
  const [body, setBody] = useState(null);
  const [error, setError] = useState("");
  const [index, setIndex] = useState(0);

  useEffect(() => {
    api("/api/v1/sectors?limit=8").then(setBody).catch((err) => setError(err.message));
  }, []);

  const card = body?.cards?.[index];

  return (
    <main className="sheet">
      <p className="banner">Derived indicators · not forecasts of yield, disease, or loss</p>
      <h1>Sector views</h1>
      <p className="reading">{body?.disclaimer}</p>
      {error && <p className="error">{error}</p>}
      {body && (
        <label>
          Cell
          <select value={index} onChange={(event) => setIndex(Number(event.target.value))}>
            {body.cards.map((item, i) => (
              <option key={item.cell.cell_id} value={i}>
                {item.cell.cell_id} · p {item.cell.probability.toFixed(2)}
              </option>
            ))}
          </select>
        </label>
      )}
      {card && (
        <div className="cards" style={{ marginTop: 18 }}>
          {KEYS.map(([key, title]) => {
            const indicator = card.indicators[key];
            return (
              <article className="card" key={key}>
                <p className="kicker">{indicator.kind}</p>
                <h2>{title}</h2>
                <span className={indicator.active ? "pill on" : "pill"}>{indicator.active ? "flag on" : "flag off"}</span>
                <p className="reading">{indicator.statement}</p>
              </article>
            );
          })}
        </div>
      )}
    </main>
  );
}
