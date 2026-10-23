"use client";

import { useEffect, useState } from "react";
import { api } from "../../lib/api";

export default function ResponsePage() {
  const [plan, setPlan] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/api/v1/response/plan?limit=12").then(setPlan).catch((err) => setError(err.message));
  }, []);

  return (
    <main className="sheet">
      <p className="kicker">Where to send a crew first</p>
      <h1>Dispatch list</h1>
      <p className="reading">
        Priority mixes the flood-model outlook with the drainage log. Model points are the real score.
        Report and incident points come from the database, and the rows shipped with this prototype are synthetic.
      </p>
      {plan && <p className="reading">{plan.rule}</p>}
      {error && <p className="error">{error}</p>}
      <ol className="hotlist">
        {(plan?.plan || []).map((item) => (
          <li key={item.cell_id} style={{ padding: "12px 0", borderTop: "1px solid var(--line)" }}>
            <span className="rank">{String(item.rank).padStart(2, "0")}</span>
            <strong>{item.cell_id}</strong>
            <span> · priority {item.priority.toFixed(1)} · p {item.probability.toFixed(3)}</span>
            <p className="reading">{item.action}</p>
            <table>
              <tbody>
                {Object.entries(item.components).map(([name, part]) => (
                  <tr key={name}>
                    <td>{name.replaceAll("_", " ")}</td>
                    <td>{part.points}</td>
                    <td><span className={`origin ${part.origin}`}>{part.origin}</span></td>
                    <td>{part.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </li>
        ))}
      </ol>
    </main>
  );
}
