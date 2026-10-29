"use client";

import { useEffect, useRef, useState } from "react";

// One colour per model, everywhere on the page. Validated against the paper surface
// (dataviz validator, all pairs); orange is under 3:1, so every mark also has a text label.
export const SERIES = {
  brief: { label: "CatBoost brief", color: "#2a78d6" },
  no_size: { label: "CatBoost no_size", color: "#eb6834" },
};
export const NEUTRAL = "#9a8f80";

const fmt = (value, places = 3) => (value == null ? "—" : Number(value).toFixed(places));

// A bar from the baseline at x with a 4px rounded data end.
function barPath(x, y, w, h) {
  const r = Math.min(4, w / 2, h / 2);
  return `M${x} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h - r}Q${x + w} ${y + h} ${x + w - r} ${y + h}H${x}Z`;
}

// At most five steps of 0.05, 0.1, 0.2, 0.25, 0.5, 1, 2 or 5 from zero past the largest value.
function niceTicks(value) {
  const step = [0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5].find((s) => Math.ceil(value / s) <= 5) ?? Math.ceil(value / 5);
  const count = Math.max(1, Math.ceil(value / step - 1e-9));
  const places = step < 0.1 || step === 0.25 ? 2 : step < 1 ? 1 : 0;
  return { max: count * step, places, ticks: Array.from({ length: count + 1 }, (_, i) => i * step) };
}

// The container's width in px, so one SVG unit is one pixel and text keeps its size on a phone.
function useWidth(fallback) {
  const ref = useRef(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    if (!ref.current) return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

function clip(text, chars) {
  return text.length <= chars ? text : `${text.slice(0, Math.max(1, chars - 1))}…`;
}

/** Horizontal bars, one row per item. rows: [{key, label, value, color, detail: [[name, value]]}] */
export function BarList({ rows, ariaLabel, places = 3, labelWidth = 200 }) {
  const [hover, setHover] = useState(null);
  const [box, width] = useWidth(640);
  const W = Math.max(280, width);
  const left = Math.min(labelWidth, Math.round(W * 0.42));
  const chars = Math.floor((left - 14) / 6.6);
  const right = 56;
  const rowH = 32;
  const top = 6;
  const bottom = 30;
  const H = top + rows.length * rowH + bottom;
  const { max, ticks, places: tickPlaces } = niceTicks(Math.max(...rows.map((r) => r.value)));
  const x = (v) => left + (v / max) * (W - left - right);
  return (
    <div className="viz" ref={box} onMouseLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={x(t)} x2={x(t)} y1={top} y2={H - bottom} />
            <text className="tick" x={x(t)} y={H - bottom + 18} textAnchor="middle">{fmt(t, tickPlaces)}</text>
          </g>
        ))}
        {rows.map((row, i) => {
          const y = top + i * rowH + rowH / 2;
          const active = hover?.row.key === row.key;
          return (
            <g key={row.key} onMouseEnter={() => setHover({ row, y })}>
              <rect x="0" y={y - rowH / 2} width={W} height={rowH} fill={active ? "rgba(27,22,18,0.05)" : "transparent"} />
              <text className="cat" x={left - 12} y={y + 4} textAnchor="end">{clip(row.label, chars)}<title>{row.label}</title></text>
              <path d={barPath(left, y - 9, Math.max(x(row.value) - left, 1), 18)} fill={row.color} />
              <text className="val" x={x(row.value) + 6} y={y + 4}>{fmt(row.value, places)}</text>
            </g>
          );
        })}
      </svg>
      {hover && hover.row.detail && (
        <div className="tip" style={{ left: Math.min(x(hover.row.value), W - 100), top: hover.y + 14 }}>
          <strong>{hover.row.label}</strong>
          {hover.row.detail.map(([name, value]) => (
            <span key={name}>{name} <b>{value}</b></span>
          ))}
        </div>
      )}
    </div>
  );
}

/** Predicted against recorded index for the held-out events, one dot per event per model. */
export function ParityScatter({ events, rmse }) {
  const ref = useRef(null);
  const [shown, setShown] = useState({ brief: true, no_size: true });
  const [hover, setHover] = useState(null);
  const [box, width] = useWidth(560);
  const W = Math.max(280, Math.min(width, 600));
  const left = 52;
  const right = 14;
  const top = 14;
  const size = W - left - right;
  const H = top + size + 46;
  const lo = 0;
  const hi = 9;
  const x = (v) => left + ((v - lo) / (hi - lo)) * size;
  const y = (v) => top + size - ((v - lo) / (hi - lo)) * size;
  const ticks = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];
  // no_size first, so brief draws on top.
  const order = ["no_size", "brief"].filter((key) => shown[key]);
  const points = order.flatMap((key) =>
    events.map((event) => ({ key, event, px: x(event.flood_impact_index), py: y(event.predictions[key]) })),
  );

  function onMove(e) {
    const box = ref.current.getBoundingClientRect();
    const scale = W / box.width;
    const mx = (e.clientX - box.left) * scale;
    const my = (e.clientY - box.top) * scale;
    let best = null;
    let bestD = 14 * 14;
    for (const p of points) {
      const d = (p.px - mx) ** 2 + (p.py - my) ** 2;
      if (d < bestD) {
        bestD = d;
        best = p;
      }
    }
    setHover(best);
  }

  return (
    <div>
      <div className="toggles" role="group" aria-label="Models shown">
        {Object.entries(SERIES).map(([key, s]) => (
          <button
            key={key}
            type="button"
            className={shown[key] ? "toggle on" : "toggle"}
            aria-pressed={shown[key]}
            onClick={() => setShown((cur) => ({ ...cur, [key]: !cur[key] }))}
          >
            <i style={{ background: s.color }} />
            {s.label} · RMSE {fmt(rmse[key])}
          </button>
        ))}
        <span className="toggle ref-key"><i /> prediction = recorded</span>
      </div>
      <div className="viz" ref={box}>
        <svg
          ref={ref}
          viewBox={`0 0 ${W} ${H}`}
          style={{ maxWidth: W }}
          role="img"
          aria-label="Predicted against recorded flood impact index for held-out 2019 to 2024 events"
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line className="grid" x1={x(t)} x2={x(t)} y1={top} y2={top + size} />
              <line className="grid" x1={left} x2={left + size} y1={y(t)} y2={y(t)} />
              <text className="tick" x={x(t)} y={top + size + 18} textAnchor="middle">{t}</text>
              <text className="tick" x={left - 10} y={y(t) + 4} textAnchor="end">{t}</text>
            </g>
          ))}
          <line className="ref" x1={x(lo)} y1={y(lo)} x2={x(hi)} y2={y(hi)} />
          {points.map((p) => (
            <circle key={`${p.key}-${p.event.event_id}`} cx={p.px} cy={p.py} r="4" fill={SERIES[p.key].color} fillOpacity="0.5" />
          ))}
          {hover && (
            <circle cx={hover.px} cy={hover.py} r="6.5" fill={SERIES[hover.key].color} stroke="#f7f1e6" strokeWidth="2" />
          )}
          <text className="axis" x={left + size / 2} y={H - 6} textAnchor="middle">Recorded flood impact index</text>
          <text className="axis" transform={`translate(14 ${top + size / 2}) rotate(-90)`} textAnchor="middle">Predicted index</text>
        </svg>
        {hover && (
          <div className="tip" style={{ left: Math.min(Math.max(hover.px, 110), W - 110), top: hover.py + 12 }}>
            <strong>{hover.event.country}</strong>
            <span>{hover.event.start_date} → {hover.event.end_date} · event {hover.event.event_id}</span>
            <span>Recorded <b>{fmt(hover.event.flood_impact_index, 1)}</b> · formula <b>{fmt(hover.event.dfo_formula, 2)}</b></span>
            <span>{SERIES[hover.key].label} <b>{fmt(hover.event.predictions[hover.key], 2)}</b></span>
          </div>
        )}
      </div>
    </div>
  );
}

/** SHAP contributions for one prediction, pushing up (flood) or down (reed) from the base value. */
export function Contributions({ items, limit = 8 }) {
  const top = items.slice(0, limit);
  const span = Math.max(...top.map((item) => Math.abs(item.contribution)), 0.01);
  return (
    <ul className="shap">
      {top.map((item) => {
        const width = `${(Math.abs(item.contribution) / span) * 50}%`;
        return (
          <li key={item.feature}>
            <span>
              {item.feature} <small>= {typeof item.input === "number" ? fmt(item.input, 2) : item.input}</small>
            </span>
            <span className="track" title={`${item.contribution >= 0 ? "+" : ""}${fmt(item.contribution, 3)}`}>
              <i />
              <span className={`bar ${item.contribution >= 0 ? "up" : "down"}`} style={{ width }} />
            </span>
            <b className="num">{item.contribution >= 0 ? "+" : "−"}{fmt(Math.abs(item.contribution), 3)}</b>
          </li>
        );
      })}
    </ul>
  );
}
