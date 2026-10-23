"use client";

import { useEffect, useState } from "react";
import { api } from "../../lib/api";

export default function ModelPage() {
  const [metrics, setMetrics] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/api/v1/metrics").then(setMetrics).catch((err) => setError(err.message));
  }, []);

  if (error) return <main className="sheet"><p className="error">{error}</p></main>;
  if (!metrics) return <main className="sheet"><p>Loading the model card…</p></main>;

  const test = metrics.temporal_test;
  const dummy = metrics.dummy_prior;
  const fmt = (value) => (value == null ? "—" : Number(value).toFixed(3));

  return (
    <main className="sheet">
      <p className="kicker">Numbers from model/artifacts/metrics.json</p>
      <h1>Model card</h1>
      <p className="reading">{metrics.target}</p>
      <div className="metrics">
        <div className="metric"><span>ROC-AUC</span><b>{fmt(test.roc_auc)}</b></div>
        <div className="metric"><span>F1</span><b>{fmt(test.f1)}</b></div>
        <div className="metric"><span>Brier</span><b>{fmt(test.brier)}</b></div>
        <div className="metric"><span>Dummy prior Brier</span><b>{fmt(dummy.brier)}</b></div>
      </div>
      <p className="reading">
        Logistic regression, C = {metrics.model.C}, decision threshold {metrics.model.threshold} chosen by
        F1 on year-blocked out-of-fold training predictions. Test label years {metrics.split.test_label_years.join(", ")}.
        Year-clustered 90% AUC interval {fmt(test.roc_auc_year_bootstrap_90ci?.[0])}–{fmt(test.roc_auc_year_bootstrap_90ci?.[1])}.
        Train positive rate {fmt(metrics.train_positive_rate)}, test positive rate {fmt(metrics.test_positive_rate)}.
        Rows: {metrics.n_train_rows} train, {metrics.n_test_rows} test, {metrics.n_cells} cells.
      </p>
      <h2>Test years</h2>
      <table>
        <thead><tr><th>Year</th><th>Observed positive rate</th><th>Mean predicted probability</th></tr></thead>
        <tbody>
          {test.by_year.map((row) => (
            <tr key={row.year}>
              <td>{row.year}</td>
              <td>{fmt(row.observed_positive_rate)}</td>
              <td>{fmt(row.mean_predicted_probability)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>Baselines</h2>
      <p className="reading">
        DummyClassifier prior Brier {fmt(dummy.brier)}, ROC-AUC {fmt(dummy.roc_auc)}, F1 at its predict() {fmt(dummy.f1_at_predict)}.
        Most-frequent Brier {fmt(metrics.dummy_most_frequent.brier)}.
      </p>
      <h2>Unseen longitude block</h2>
      <p className="reading">
        Cells with southwest longitude in [{metrics.spatial_holdout_definition.sw_lon_from_inclusive}, {metrics.spatial_holdout_definition.sw_lon_to_exclusive})
        were held out. Status {metrics.spatial_holdout.status}. ROC-AUC {fmt(metrics.spatial_holdout.roc_auc)},
        F1 {fmt(metrics.spatial_holdout.f1)}, Brier {fmt(metrics.spatial_holdout.brier)}.
        {metrics.spatial_holdout_definition.note}
      </p>
      <h2>Mean |SHAP| on the test set (log-odds)</h2>
      <table>
        <tbody>
          {Object.entries(metrics.mean_abs_shap_test_log_odds).map(([name, value]) => (
            <tr key={name}><td>{name}</td><td>{fmt(value)}</td></tr>
          ))}
        </tbody>
      </table>
      <h2>Limitations</h2>
      <ol>
        {metrics.limitations.map((line) => <li key={line} className="reading">{line}</li>)}
      </ol>
    </main>
  );
}
