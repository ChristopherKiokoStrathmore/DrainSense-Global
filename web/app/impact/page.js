"use client";

import { useEffect, useMemo, useState } from "react";
import BatchScorer from "../../components/BatchScorer";
import { BarList, Contributions, NEUTRAL, ParityScatter, SERIES } from "../../components/ImpactCharts";
import { API, api, errorDetail } from "../../lib/api";

const fmt = (value, places = 3) => (value == null ? "—" : Number(value).toFixed(places));

const REFERENCE_LABELS = {
  dfo_formula: "DFO formula",
  linear_log_area_log_days: "Linear, log area + log days",
  dfo_formula_without_severity: "Formula without severity",
  train_mean: "Training mean",
};

const EVENT_FIELDS = ["start_date", "end_date", "area_km2", "severity", "main_cause", "latitude", "longitude"];
const PLACE_FIELDS = ["continent_region"];

const words = (field) => field.replace(/_/g, " ").replace(/\b(mm|c|kmh|hpa|pct|m3m3|km2)\b/g, (u) => `(${u})`);

export default function ImpactPage() {
  const [card, setCard] = useState(null);
  const [events, setEvents] = useState([]);
  const [examples, setExamples] = useState({});
  const [health, setHealth] = useState(null);
  const [problems, setProblems] = useState({});

  // Each call fails on its own: a missing schema only empties the forms, a missing event list only the scatter.
  useEffect(() => {
    Promise.allSettled([
      api("/api/v1/impact/model"),
      api("/api/v1/impact/test-events?limit=1000"),
      api("/openapi.json"),
      api("/api/v1/health"),
    ]).then(([model, held, schema, ping]) => {
      const failed = {};
      if (model.status === "fulfilled") setCard(model.value);
      else {
        failed.model = errorDetail(model.reason);
        // fetch() rejects with a TypeError only when the API could not be reached at all.
        failed.unreachable = model.reason instanceof TypeError;
      }
      if (held.status === "fulfilled") setEvents(held.value.events);
      else failed.events = errorDetail(held.reason);
      if (schema.status === "fulfilled") {
        const found = schema.value.paths["/api/v1/impact/predict"].post.requestBody.content["application/json"].examples;
        setExamples({ brief: found.brief.value, no_size: found.no_size.value });
      } else {
        failed.schema = errorDetail(schema.reason);
      }
      setHealth(ping.status === "fulfilled" ? ping.value : false);
      setProblems(failed);
    });
  }, []);

  if (problems.model) {
    return (
      <main className="sheet">
        <h1>Flood impact model</h1>
        <p className="error">Could not load the model card from {API}: {problems.model}</p>
        {problems.unreachable && (
          <p className="reading">Start the API (see the README) or set NEXT_PUBLIC_API_URL, then reload.</p>
        )}
      </main>
    );
  }
  if (!card) return <main className="sheet"><p>Loading the impact model…</p></main>;

  const brief = card.variants.brief;
  const noSize = card.variants.no_size;
  const refs = card.baselines_temporal_test;
  const rmse = { brief: brief.temporal_test.rmse, no_size: noSize.temporal_test.rmse };

  const comparison = [
    ...Object.entries(REFERENCE_LABELS).map(([key, label]) => ({ key, label, scores: refs[key], color: NEUTRAL })),
    { key: "brief", label: SERIES.brief.label, scores: brief.temporal_test, color: SERIES.brief.color },
    { key: "no_size", label: SERIES.no_size.label, scores: noSize.temporal_test, color: SERIES.no_size.color },
  ]
    .map((row) => ({
      key: row.key,
      label: row.label,
      value: row.scores.rmse,
      color: row.color,
      detail: [["RMSE", fmt(row.scores.rmse)], ["MAE", fmt(row.scores.mae)], ["R²", fmt(row.scores.r2)], ["events", row.scores.n]],
    }))
    .sort((a, b) => a.value - b.value);

  return (
    <main className="sheet wide impact">
      <p className="kicker">Numbers from model/artifacts/impact/impact_metrics.json · generated {card.generated_at.slice(0, 10)}</p>
      <p className="status">
        <i className={health ? "dot ok" : "dot down"} aria-hidden="true" />
        API {health ? "online" : "unreachable"} at <code>{API}</code> · model built {card.generated_at.slice(0, 10)} ·{" "}
        <a href={`${API}/docs`} target="_blank" rel="noreferrer">Swagger UI</a> ·{" "}
        <a href={`${API}/openapi.json`} target="_blank" rel="noreferrer">OpenAPI JSON</a> ·{" "}
        <a href="#batch">Batch scoring</a>
      </p>
      <h1>Flood impact model</h1>
      <p className="banner">The index is a formula of area, duration and severity. Not a forecast.</p>
      <p className="reading">
        CatBoost regressor from the executive brief, scored on {card.split.n_test} DFO events that started in{" "}
        {card.split.test_years}. The target is {card.target_definition}. Given those three numbers the formula is
        exact, so the useful comparison is against it.
      </p>

      <div className="metrics">
        <div className="metric"><span>CatBoost brief, test RMSE</span><b>{fmt(rmse.brief)}</b></div>
        <div className="metric"><span>DFO formula, test RMSE</span><b>{fmt(refs.dfo_formula.rmse)}</b></div>
        <div className="metric"><span>CatBoost no_size, test RMSE</span><b>{fmt(rmse.no_size)}</b></div>
        <div className="metric"><span>Training mean, test RMSE</span><b>{fmt(refs.train_mean.rmse)}</b></div>
      </div>

      <section className="panel">
        <h2>Error by model, 2019–{card.split.test_years.slice(-4)}</h2>
        <p className="reading">
          Lower is better. Grey bars are references that need no training data beyond two coefficients. The brief model
          is given area and duration and lands next to the formula without severity.
        </p>
        <BarList rows={comparison} ariaLabel="Test RMSE by model" />
      </section>

      <section className="panel">
        <h2>Predicted against recorded</h2>
        <p className="reading">
          One dot per held-out event per model. On the line, the prediction equals the recorded index. Hover for the event.
        </p>
        {problems.events && <p className="error">Held-out events did not load: {problems.events}</p>}
        {events.length > 0 && <ParityScatter events={events} rmse={rmse} />}
      </section>

      <section className="two">
        <div className="panel">
          <h2>What drives brief</h2>
          <p className="reading">Mean |SHAP| on the test events, in index points.</p>
          <BarList
            rows={topShap(brief.mean_abs_shap_test, SERIES.brief.color)}
            ariaLabel="Mean absolute SHAP for the brief model"
            labelWidth={260}
          />
        </div>
        <div className="panel">
          <h2>What drives no_size</h2>
          <p className="reading">Same measure. Observed weather before the event and in its first three days, plus place and cause.</p>
          <BarList
            rows={topShap(noSize.mean_abs_shap_test, SERIES.no_size.color)}
            ariaLabel="Mean absolute SHAP for the no_size model"
            labelWidth={260}
          />
        </div>
      </section>

      {problems.schema && <p className="error">The OpenAPI examples did not load, so the forms start empty: {problems.schema}</p>}
      <Predictor card={card} examples={examples} />

      <BatchScorer card={card} examples={examples} />

      <HeldOut events={events} />

      <section className="panel">
        <h2>Data checks</h2>
        <table>
          <tbody>
            <tr><td>Rows where the rounded formula equals the index</td><td className="num">{card.data_checks.rows_where_rounded_formula_equals_index} of {card.data_checks.rows}</td></tr>
            <tr><td>Formula RMSE against the index · rounding alone</td><td className="num">{fmt(card.data_checks.formula_rmse_vs_index, 4)} · {fmt(card.data_checks.rounding_floor_rmse, 4)}</td></tr>
            <tr><td>Distinct event coordinates in the enriched CSV</td><td className="num">{card.data_checks.distinct_event_coordinates}</td></tr>
            <tr><td>Enriched log rainfall explained by days and latitude · adding area and severity</td><td className="num">{fmt(card.data_checks.log_total_precip_r2_from_days_and_abs_latitude, 2)} · {fmt(card.data_checks.log_total_precip_r2_adding_log_area_and_severity, 2)}</td></tr>
            <tr><td>Topsoil moisture vs event length in the enriched CSV (Spearman)</td><td className="num">{fmt(card.data_checks.spearman_topsoil_moisture_vs_days, 2)}</td></tr>
            <tr><td>Distinct footprint centres used for observed weather</td><td className="num">{card.data_checks.observed.distinct_footprint_centres}</td></tr>
            <tr><td>Median distance, enriched point to footprint centre</td><td className="num">{fmt(card.data_checks.observed.median_km_snapped_point_to_footprint_centre, 0)} km</td></tr>
            <tr><td>Enriched vs observed event rainfall (Spearman)</td><td className="num">{fmt(card.data_checks.observed.spearman_enriched_vs_observed_event_precip, 2)}</td></tr>
            <tr><td>Observed log rainfall explained by days and latitude · adding area and severity</td><td className="num">{fmt(card.data_checks.observed.log_event_precip_r2_from_days_and_abs_latitude, 2)} · {fmt(card.data_checks.observed.log_event_precip_r2_adding_log_area_and_severity, 2)}</td></tr>
            <tr><td>Brief's own protocol, test RMSE here · reported in the brief</td><td className="num">{fmt(card.brief_reproduction.test_rmse_best, 4)} · {card.brief_reproduction.brief_reported.test_rmse}</td></tr>
          </tbody>
        </table>
        <h2>Limitations</h2>
        <ol>
          {card.limitations.map((line) => <li key={line} className="reading">{line}</li>)}
        </ol>
      </section>
    </main>
  );
}

function topShap(values, color) {
  return Object.entries(values)
    .slice(0, 8)
    .map(([name, value]) => ({ key: name, label: name, value, color, detail: [["mean |SHAP|", fmt(value, 4)]] }));
}

function Predictor({ card, examples }) {
  const [variant, setVariant] = useState("brief");
  const [forms, setForms] = useState({ brief: {}, no_size: {} });
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Each variant starts from the API's own OpenAPI example and keeps its own edits.
  useEffect(() => {
    setForms((cur) => ({
      brief: { ...(examples.brief || {}), ...cur.brief },
      no_size: { ...(examples.no_size || {}), ...cur.no_size },
    }));
  }, [examples]);
  const values = forms[variant];

  const required = card.inputs[variant].required;
  const fields = useMemo(() => {
    const all = variant === "brief" ? [...required, "severity"] : required;
    return {
      event: EVENT_FIELDS.filter((f) => all.includes(f)),
      place: PLACE_FIELDS.filter((f) => all.includes(f)),
      weather: all.filter((f) => !EVENT_FIELDS.includes(f) && !PLACE_FIELDS.includes(f)),
    };
  }, [variant, required]);

  function input(field) {
    const value = values[field] ?? "";
    const set = (v) => setForms((cur) => ({ ...cur, [variant]: { ...cur[variant], [field]: v } }));
    if (field === "continent_region") {
      const options = card.categories.Continent_Region;
      return (
        <select value={value} onChange={(e) => set(e.target.value)}>
          {options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      );
    }
    if (field === "severity") {
      return (
        <select value={value} onChange={(e) => set(e.target.value === "" ? "" : Number(e.target.value))}>
          <option value="">not sent</option>
          {[1, 1.5, 2].map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      );
    }
    if (field.endsWith("_date")) return <input type="date" value={value} onChange={(e) => set(e.target.value)} />;
    if (field === "main_cause") return <input value={value} onChange={(e) => set(e.target.value)} />;
    return <input type="number" step="any" value={value} onChange={(e) => set(e.target.value === "" ? "" : Number(e.target.value))} />;
  }

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const body = {};
    for (const field of [...fields.event, ...fields.place, ...fields.weather]) {
      if (values[field] !== "" && values[field] != null) body[field] = values[field];
    }
    try {
      setResult(await api(`/api/v1/impact/predict?variant=${variant}`, { method: "POST", body: JSON.stringify(body) }));
    } catch (err) {
      setResult(null);
      setError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <h2>Score an event</h2>
      <p className="reading">
        Sends <code>POST /api/v1/impact/predict</code>. The form starts from the API's own examples: the Kenya flood of
        October–November 2023 for brief, and the July 2021 floods in Belgium and Germany for no_size.
      </p>
      <div className="toggles" role="group" aria-label="Variant">
        {Object.entries(SERIES).map(([key, s]) => (
          <button key={key} type="button" className={variant === key ? "toggle on" : "toggle"} aria-pressed={variant === key}
            onClick={() => { setVariant(key); setResult(null); setError(""); }}>
            <i style={{ background: s.color }} />{s.label}
          </button>
        ))}
      </div>
      <form className="form predictor" onSubmit={submit}>
        {[["Event", fields.event], ["Place", fields.place], ["Observed weather at the flood's centre (ERA5 via Open-Meteo)", fields.weather]].map(([title, list]) => (
          list.length > 0 && (
            <fieldset key={title}>
              <legend className="kicker">{title}</legend>
              <div className="fields">
                {list.map((field) => <label key={field}>{words(field)}{input(field)}</label>)}
              </div>
            </fieldset>
          )
        ))}
        <button className="solid" type="submit" disabled={busy}>{busy ? "Scoring…" : `Score with ${variant}`}</button>
      </form>
      {error && <p className="error">{error}</p>}
      {result && <PredictionResult result={result} />}
    </section>
  );
}

function PredictionResult({ result }) {
  const formula = result.dfo_formula;
  return (
    <div className="result">
      <div className="score-row">
        <div>
          <p className="kicker">{SERIES[result.variant].label} estimate</p>
          <p className="score">{fmt(result.flood_impact_index, 2)}</p>
        </div>
        <div>
          <p className="kicker">DFO formula</p>
          <p className="score quiet">{formula ? fmt(formula.value, 2) : "—"}</p>
        </div>
        <div className="reading">
          {formula
            ? <>Model − formula <b>{result.model_minus_formula >= 0 ? "+" : ""}{fmt(result.model_minus_formula)}</b>. {formula.inputs.days_inclusive} days × severity {formula.inputs.severity} × {formula.inputs.area_km2.toLocaleString()} km².</>
            : <>No formula: send end date, area and severity to get the exact value.</>}
          <br />Base value {fmt(result.expected_value)}, test RMSE {fmt(result.model_test_rmse)}.
        </div>
      </div>
      <p className="reading">{result.reading}</p>
      {result.outside_training_range.length > 0 && (
        <p className="error">Outside the training range: {result.outside_training_range.join(", ")}</p>
      )}
      <p className="kicker">What moved the estimate from the base value</p>
      <Contributions items={result.contributions} />
    </div>
  );
}

function HeldOut({ events }) {
  const [query, setQuery] = useState("");
  const [count, setCount] = useState(20);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? events.filter((e) => e.country.toLowerCase().includes(q) || e.iso_country_code.toLowerCase() === q) : events;
  }, [events, query]);
  return (
    <section className="panel">
      <h2>Held-out events</h2>
      <p className="reading">
        {events.length} DFO records the scored models never saw, newest first. Predictions come from the holdout models
        trained on 1985–2018. This table is also the data behind the scatter.
      </p>
      <label className="filter">Country or ISO code
        <input value={query} onChange={(e) => { setQuery(e.target.value); setCount(20); }} placeholder="e.g. Kenya or KE" />
      </label>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>Start</th><th>Country</th><th>Cause</th><th className="num">Area km²</th><th className="num">Days</th>
              <th className="num">Sev.</th><th className="num">Recorded</th><th className="num">Formula</th>
              <th className="num"><i className="key" style={{ background: SERIES.brief.color }} />brief</th>
              <th className="num"><i className="key" style={{ background: SERIES.no_size.color }} />no_size</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, count).map((e) => (
              <tr key={e.event_id}>
                <td>{e.start_date}</td>
                <td>{e.country}</td>
                <td>{e.cause_group.replace(/_/g, " ")}</td>
                <td className="num">{Math.round(e.area_km2).toLocaleString()}</td>
                <td className="num">{e.days_inclusive}</td>
                <td className="num">{e.severity}</td>
                <td className="num">{fmt(e.flood_impact_index, 1)}</td>
                <td className="num">{fmt(e.dfo_formula, 2)}</td>
                <td className="num">{fmt(e.predictions.brief, 2)}</td>
                <td className="num">{fmt(e.predictions.no_size, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="reading">
        Showing {Math.min(count, rows.length)} of {rows.length}.{" "}
        {count < rows.length && <button type="button" className="link" onClick={() => setCount((c) => c + 40)}>Show 40 more</button>}
      </p>
    </section>
  );
}
