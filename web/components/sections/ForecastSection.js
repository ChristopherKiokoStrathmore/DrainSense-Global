"use client";

import { useEffect, useMemo, useState } from "react";
import { api, errorDetail } from "../../lib/api";
import { useDesk } from "../desk";
import { useRiskMap } from "./MapSection";

// The furthest the forecast may reach, in days from today. The API refuses anything later; this keeps the picker in step.
const MAX_DAYS = 14;
const QUICK = [[3, "3 days"], [7, "1 week"], [14, "2 weeks"]];
// Level names and the teammates' band colours (globals.css .band).
export const LEVEL = {
  likely: { label: "Likely", band: "red" },
  watch: { label: "Watch", band: "amber" },
  quiet: { label: "Quiet", band: "green" },
};
// How many watch regions show before "Show all".
const FIRST_WATCH = 8;

const iso = (d) => d.toISOString().slice(0, 10);
const addDays = (isoDate, n) => {
  const d = new Date(`${isoDate}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return iso(d);
};
const nice = (isoDate) => new Date(`${isoDate}T00:00:00Z`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });

/**
 * The Forecast tab: pick a day up to two weeks ahead, see which regions are likely to flood between today and then.
 * The map repaints with the forecast while this tab is open.
 */
export default function ForecastSection() {
  const { tab } = useDesk();
  const { forecast, setForecast, selectedId, focusCell } = useRiskMap();
  // Today and the last allowed day come from the API (UTC). Until it answers, the browser's own date stands in.
  const issued = forecast?.issued ?? iso(new Date());
  const latest = forecast?.latest_until ?? addDays(issued, MAX_DAYS);
  const [until, setUntil] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [allWatch, setAllWatch] = useState(false);
  const [opened, setOpened] = useState(false);

  // Nothing is fetched until the tab is first opened: the forecast reads rainfall for every cell.
  useEffect(() => {
    if (tab === "forecast") setOpened(true);
  }, [tab]);

  useEffect(() => {
    if (!opened) return undefined;
    let cancel = false;
    setBusy(true);
    setError("");
    api(`/api/v1/risk/forecast${until ? `?until=${until}` : ""}`)
      .then((body) => {
        if (!cancel) setForecast(body);
      })
      .catch((err) => {
        if (!cancel) setError(errorDetail(err));
      })
      .finally(() => {
        if (!cancel) setBusy(false);
      });
    return () => {
      cancel = true;
    };
  }, [opened, until, setForecast]);

  // A typed date outside today … +14 days is pulled back inside, with a note; the picker itself stops at the bounds.
  function pick(value) {
    if (!value) return;
    if (value > latest) {
      setNotice(`Forecasts go at most ${MAX_DAYS} days ahead, so the window ends on ${nice(latest)}.`);
      setUntil(latest);
    } else if (value < issued) {
      setNotice("The window starts today, so it cannot end before today.");
      setUntil(issued);
    } else {
      setNotice("");
      setUntil(value);
    }
  }

  const shownUntil = until ?? forecast?.until ?? latest;
  const cells = forecast?.cells || [];
  const likely = cells.filter((c) => c.level === "likely");
  const watch = cells.filter((c) => c.level === "watch");
  const picked = useMemo(() => cells.find((c) => c.cell_id === selectedId), [cells, selectedId]);

  return (
    <div className="sheet forecast">
      <h1>Flood forecast · next two weeks</h1>
      <p className="mute">
        Regions likely to be affected by flooding between today and the day you pick, at most {MAX_DAYS} days ahead.
        The flood model's score for each region is combined with the rainfall forecast for the window. The map shows
        the forecast while this tab is open.
      </p>

      <div className="datefilter">
        <label>
          Forecast up to
          <input type="date" value={shownUntil} min={issued} max={latest} onChange={(event) => pick(event.target.value)}
            aria-describedby="forecast-range" />
        </label>
        <div className="chips" role="group" aria-label="Quick windows">
          {QUICK.map(([days, label]) => {
            const value = addDays(issued, days);
            return (
              <button key={days} type="button" className={`toggle${shownUntil === value ? " on" : ""}`} aria-pressed={shownUntil === value}
                onClick={() => pick(value)}>
                {label}
              </button>
            );
          })}
        </div>
        <p id="forecast-range" className="mute">
          {nice(issued)} to {nice(shownUntil)} · {forecast ? `${forecast.days} days` : "…"} · latest allowed {nice(latest)}
        </p>
        {notice && <p className="notice">{notice}</p>}
      </div>

      {error && <p className="error">{error}</p>}
      {busy && !forecast && <p className="mute">Loading the rainfall forecast for every region…</p>}

      {forecast && (
        <>
          <div className={`kpis${busy ? " busy" : ""}`}>
            <div className="kpi hot"><b>{forecast.counts.likely}</b><span>Likely affected</span></div>
            <div className="kpi"><b>{forecast.counts.watch}</b><span>On watch</span></div>
            <div className="kpi"><b>{forecast.counts.quiet}</b><span>Quiet</span></div>
          </div>

          {picked && <Picked cell={picked} heavy={forecast.heavy_mm} featureYear={forecast.feature_year} />}

          <h2 className="sub">Likely to be affected by {nice(forecast.until)}</h2>
          {likely.length ? <Regions rows={likely} selectedId={selectedId} onPick={focusCell} />
            : <p className="mute">No region meets the “likely” rule in this window.</p>}

          <h2 className="sub">On watch</h2>
          {watch.length ? (
            <>
              <Regions rows={allWatch ? watch : watch.slice(0, FIRST_WATCH)} selectedId={selectedId} onPick={focusCell} />
              {watch.length > FIRST_WATCH && (
                <button type="button" className="act ghost small" onClick={() => setAllWatch((on) => !on)}>
                  {allWatch ? "Show fewer" : `Show all ${watch.length}`}
                </button>
              )}
            </>
          ) : <p className="mute">No region is on watch in this window.</p>}

          <h2 className="sub">How the levels are set</h2>
          <p className="mute">{forecast.rule}</p>
          <p className="mute">Rainfall: {forecast.source}. {forecast.disclaimer}</p>
        </>
      )}
    </div>
  );
}

function LevelPill({ level }) {
  const info = LEVEL[level];
  return <span className={`band ${info.band}`}>{info.label}</span>;
}

function Regions({ rows, selectedId, onPick }) {
  return (
    <ol className="regions">
      {rows.map((c) => (
        <li key={c.cell_id}>
          <button type="button" aria-current={c.cell_id === selectedId ? "true" : undefined} onClick={() => onPick(c.cell_id)}>
            <span className="name" title={c.cell_id}>{c.place_name}</span>
            <LevelPill level={c.level} />
            <span className="mm">{Math.round(c.window_total_mm)} mm</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

function Picked({ cell, heavy, featureYear }) {
  return (
    <div className="card cell">
      <div className="row">
        <h3>{cell.place_name}</h3>
        <LevelPill level={cell.level} />
      </div>
      <p className="mute">Flood model score {cell.probability.toFixed(2)} · picked on the map or below</p>
      <RainBars daily={cell.daily} heavy={heavy} />
      <ul className="facts">
        <li><b>{cell.window_total_mm} mm</b> forecast in the window</li>
        <li><b>{cell.wettest_7day_mm} mm</b> in the wettest 7 days, against <b>{cell.history_max_7day_mm} mm</b> in the wettest 7 days of {featureYear} ({Math.round(cell.rain_ratio * 100)}%)</li>
        <li><b>{cell.heavy_days}</b> {cell.heavy_days === 1 ? "day" : "days"} with {heavy} mm or more{cell.wettest_day ? ` · wettest ${nice(cell.wettest_day)}, ${cell.wettest_day_mm} mm` : ""}</li>
      </ul>
    </div>
  );
}

/** Daily forecast rain for the window, with the heavy-day line. */
function RainBars({ daily, heavy }) {
  const W = 340;
  const H = 70;
  const top = Math.max(heavy * 1.5, ...daily.map((d) => d.mm), 1);
  const step = W / Math.max(daily.length, 1);
  const y = (mm) => H - (mm / top) * H;
  return (
    <figure className="rainbars">
      <svg viewBox={`0 0 ${W} ${H + 14}`} role="img" aria-label={`Daily forecast rain: ${daily.map((d) => `${d.date} ${d.mm} mm`).join(", ")}`}>
        <line x1="0" x2={W} y1={y(heavy)} y2={y(heavy)} className="heavy" />
        {daily.map((d, i) => (
          <rect key={d.date} x={i * step + 1.5} width={Math.max(step - 3, 2)} y={y(d.mm)} height={Math.max(H - y(d.mm), d.mm > 0 ? 1 : 0)}
            className={d.mm >= heavy ? "wet" : ""}>
            <title>{nice(d.date)} · {d.mm} mm</title>
          </rect>
        ))}
        {daily.length > 0 && <text x="0" y={H + 12}>{nice(daily[0].date)}</text>}
        {daily.length > 1 && <text x={W} y={H + 12} textAnchor="end">{nice(daily.at(-1).date)}</text>}
      </svg>
      <figcaption className="mute">Daily rain · dashed line {heavy} mm (a heavy day)</figcaption>
    </figure>
  );
}
