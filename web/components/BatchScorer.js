"use client";

import { useState } from "react";
import { api } from "../lib/api";
import { downloadCsv, parseCsv, toCsv } from "../lib/csv";
import { SERIES } from "./ImpactCharts";

const CHUNK = 500; // POST /predict/batch takes at most 500 events per call
const MAX_ROWS = 5000;
const TEXT_FIELDS = new Set(["start_date", "end_date", "continent_region", "main_cause"]);
const SHOWN = 50;

const fmt = (value, places = 2) => (value == null ? "—" : Number(value).toFixed(places));

function columnsFor(card, variant) {
  const required = card.inputs[variant].required;
  return variant === "brief" ? [...required, "severity"] : required;
}

// "event 3: ..." and schema errors at body.events[3] both become "Row N" of the CSV (1-based, header excluded).
function rowError(err, offset) {
  let detail;
  try {
    detail = JSON.parse(err.message).detail;
  } catch {
    return err.message;
  }
  if (typeof detail === "string") {
    return detail.replace(/^event (\d+):/, (_, k) => `Row ${offset + Number(k) + 1}:`);
  }
  return detail
    .map((d) => {
      const [, , index, ...rest] = d.loc;
      const where = typeof index === "number" ? `Row ${offset + index + 1}` : d.loc.join(".");
      return `${where}: ${rest.length ? `${rest.join(".")} ` : ""}${d.msg}`;
    })
    .join("\n");
}

export default function BatchScorer({ card, examples }) {
  const [variant, setVariant] = useState("brief");
  const [text, setText] = useState("");
  const [fileName, setFileName] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const [data, setData] = useState(null);

  const known = new Set([...columnsFor(card, "brief"), ...columnsFor(card, "no_size")]);

  function template(withExample) {
    const header = columnsFor(card, variant);
    const rows = withExample && examples[variant] ? [examples[variant]] : [];
    return toCsv(header, rows);
  }

  async function onFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    setText(await file.text());
    setData(null);
    setError("");
  }

  async function run() {
    setError("");
    setData(null);
    const { header, rows } = parseCsv(text);
    if (!rows.length) return setError("No data rows. Paste a CSV with a header line, or load the example.");
    if (rows.length > MAX_ROWS) return setError(`${rows.length} rows; the dashboard scores at most ${MAX_ROWS} at a time.`);
    const required = card.inputs[variant].required;
    const missing = required.filter((c) => !header.includes(c));
    if (missing.length) {
      return setError(`Missing columns for ${variant}: ${missing.join(", ")}. Download the template for the full header.`);
    }
    const problems = [];
    const events = rows.map((row, i) => {
      const event = {};
      for (const [key, raw] of Object.entries(row)) {
        if (!known.has(key) || raw === "") continue;
        if (TEXT_FIELDS.has(key)) {
          event[key] = raw;
        } else if (Number.isFinite(Number(raw))) {
          event[key] = Number(raw);
        } else {
          problems.push(`Row ${i + 1}: ${key} "${raw}" is not a number`);
        }
      }
      const empty = required.filter((c) => event[c] === undefined && !problems.some((p) => p.startsWith(`Row ${i + 1}: ${c} `)));
      if (empty.length) problems.push(`Row ${i + 1}: empty ${empty.join(", ")}`);
      return event;
    });
    if (problems.length) {
      const more = problems.length > 8 ? `\n…and ${problems.length - 8} more` : "";
      return setError(problems.slice(0, 8).join("\n") + more);
    }

    setBusy(true);
    const results = [];
    try {
      for (let start = 0; start < events.length; start += CHUNK) {
        const end = Math.min(start + CHUNK, events.length);
        setProgress(`Scoring rows ${start + 1}–${end} of ${events.length}…`);
        try {
          const body = await api(`/api/v1/impact/predict/batch?variant=${variant}`, {
            method: "POST",
            body: JSON.stringify({ events: events.slice(start, end) }),
          });
          results.push(...body.results);
        } catch (err) {
          throw new Error(rowError(err, start));
        }
      }
      const ignored = header.filter((h) => !known.has(h));
      setData({ variant, header, rows, results, ignored });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
      setProgress("");
    }
  }

  function downloadResults() {
    const extra = ["variant", "flood_impact_index", "dfo_formula", "model_minus_formula", "top_driver",
      "top_driver_contribution", "outside_training_range"];
    const rows = data.rows.map((row, i) => {
      const r = data.results[i];
      return {
        ...row,
        variant: r.variant,
        flood_impact_index: r.flood_impact_index,
        dfo_formula: r.dfo_formula?.value ?? "",
        model_minus_formula: r.model_minus_formula ?? "",
        top_driver: r.contributions[0]?.feature ?? "",
        top_driver_contribution: r.contributions[0]?.contribution ?? "",
        outside_training_range: r.outside_training_range.join(" "),
      };
    });
    const header = [...data.header, ...extra.filter((h) => !data.header.includes(h))];
    downloadCsv(`impact_scores_${data.variant}.csv`, toCsv(header, rows));
  }

  return (
    <section className="panel" id="batch">
      <h2>Score a batch</h2>
      <p className="reading">
        Sends <code>POST /api/v1/impact/predict/batch</code>, {CHUNK} rows per call. One CSV row per event, one
        column per API field. The template has the header for the chosen variant and the API's own example row.
      </p>
      <div className="toggles" role="group" aria-label="Variant">
        {Object.entries(SERIES).map(([key, s]) => (
          <button key={key} type="button" className={variant === key ? "toggle on" : "toggle"} aria-pressed={variant === key}
            onClick={() => { setVariant(key); setData(null); setError(""); }}>
            <i style={{ background: s.color }} />{s.label}
          </button>
        ))}
      </div>
      <div className="form batch">
        <div className="batch-actions">
          <button type="button" className="ghost" onClick={() => downloadCsv(`impact_template_${variant}.csv`, template(true))}>
            Download template
          </button>
          <button type="button" className="ghost" onClick={() => { setText(template(true)); setFileName(""); setData(null); setError(""); }}
            disabled={!examples[variant]}>
            Load the example row
          </button>
          <label className="file">
            <span className="ghost">Upload CSV</span>
            <input type="file" accept=".csv,text/csv" onChange={onFile} />
          </label>
          {fileName && <span className="reading">{fileName}</span>}
        </div>
        <label>
          CSV
          <textarea rows={6} spellCheck={false} value={text} placeholder={template(false).trim()}
            onChange={(e) => { setText(e.target.value); setData(null); }} />
        </label>
        <button className="solid" type="button" onClick={run} disabled={busy || !text.trim()}>
          {busy ? progress || "Scoring…" : `Score with ${variant}`}
        </button>
      </div>
      {error && <pre className="error">{error}</pre>}
      {data && <BatchResults data={data} onDownload={downloadResults} />}
    </section>
  );
}

function BatchResults({ data, onDownload }) {
  const { results, rows } = data;
  const withFormula = results.filter((r) => r.dfo_formula);
  const meanGap = withFormula.length
    ? withFormula.reduce((sum, r) => sum + Math.abs(r.model_minus_formula), 0) / withFormula.length
    : null;
  const flagged = results.filter((r) => r.outside_training_range.length).length;
  const mean = results.reduce((sum, r) => sum + r.flood_impact_index, 0) / results.length;
  return (
    <div className="result">
      <div className="metrics">
        <div className="metric"><span>Rows scored</span><b>{results.length}</b></div>
        <div className="metric"><span>Mean estimate</span><b>{fmt(mean)}</b></div>
        <div className="metric">
          <span>Mean |model − formula|{withFormula.length ? `, ${withFormula.length} rows` : ""}</span>
          <b>{meanGap == null ? "—" : fmt(meanGap, 3)}</b>
        </div>
        <div className="metric"><span>Rows outside the training range</span><b>{flagged}</b></div>
      </div>
      {data.ignored.length > 0 && (
        <p className="reading">Ignored columns the API does not take: {data.ignored.join(", ")}.</p>
      )}
      {withFormula.length === 0 && (
        <p className="reading">No row had end date, area and severity together, so no formula value to compare with.</p>
      )}
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th className="num">Row</th><th>Start</th><th className="num">Estimate</th><th className="num">Formula</th>
              <th className="num">Model − formula</th><th>Top driver</th><th>Outside training range</th>
            </tr>
          </thead>
          <tbody>
            {results.slice(0, SHOWN).map((r, i) => (
              <tr key={i}>
                <td className="num">{i + 1}</td>
                <td>{rows[i].start_date}</td>
                <td className="num"><b>{fmt(r.flood_impact_index)}</b></td>
                <td className="num">{r.dfo_formula ? fmt(r.dfo_formula.value) : "—"}</td>
                <td className="num">{r.model_minus_formula == null ? "—" : `${r.model_minus_formula >= 0 ? "+" : "−"}${fmt(Math.abs(r.model_minus_formula), 3)}`}</td>
                <td>{r.contributions[0]?.feature} <small>{r.contributions[0]?.contribution >= 0 ? "+" : "−"}{fmt(Math.abs(r.contributions[0]?.contribution ?? 0), 3)}</small></td>
                <td>{r.outside_training_range.length ? r.outside_training_range.join(", ") : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="reading">
        Showing {Math.min(SHOWN, results.length)} of {results.length}.{" "}
        <button type="button" className="link" onClick={onDownload}>Download all results as CSV</button>
      </p>
    </div>
  );
}
