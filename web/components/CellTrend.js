"use client";

import { useState } from "react";
import { PREDICTED, RECORDED } from "./Timeline";

const W = 340;
const H = 120;
const PAD = { top: 10, right: 10, bottom: 22, left: 32 };

/**
 * One cell's flood score for every label year, the model threshold, and the years a DFO flood was recorded there.
 * The step into the outlook year is dashed: it is the prediction, with no record yet to check it against.
 */
// Near either edge the tip hangs inward from the point, so it never runs out of the dossier.
function tipPlace(share) {
  const left = `${share * 100}%`;
  if (share < 0.3) return { left, top: -8, transform: "translate(-12px, -100%)" };
  if (share > 0.7) return { left, top: -8, transform: "translate(calc(-100% + 12px), -100%)" };
  return { left, top: -8, transform: "translate(-50%, -100%)" };
}

export default function CellTrend({ years, probability, flooded, threshold, outlookYear, year, onYear }) {
  const [hover, setHover] = useState(null);
  const top = Math.max(0.2, Math.ceil(Math.max(...probability.filter((p) => p != null), threshold) * 10) / 10);
  const x = (i) => PAD.left + (i / (years.length - 1)) * (W - PAD.left - PAD.right);
  const y = (p) => PAD.top + (1 - p / top) * (H - PAD.top - PAD.bottom);
  const outlookAt = years.indexOf(outlookYear);
  const points = probability.map((p, i) => (p == null ? null : [x(i), y(p)]));
  const path = (from, to) => points.slice(from, to + 1).filter(Boolean).map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join("");
  const pastEnd = outlookAt > 0 ? outlookAt - 1 : years.length - 1;
  const ticks = [0, top / 2, top];
  const step = (W - PAD.left - PAD.right) / (years.length - 1);
  const shown = hover ?? years.indexOf(year);

  return (
    <div className="viz trend">
      <svg viewBox={`0 0 ${W} ${H}`} role="img"
        aria-label={`Flood score by year, ${years[0]} to ${years.at(-1)}. Recorded floods in ${years.filter((_, i) => flooded[i] === 1).join(", ") || "none of the labelled years"}.`}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} />
            <text className="tick" x={PAD.left - 6} y={y(t) + 4} textAnchor="end">{t.toFixed(2)}</text>
          </g>
        ))}
        <line className="ref" x1={PAD.left} x2={W - PAD.right} y1={y(threshold)} y2={y(threshold)} strokeDasharray="3 3" />
        {shown >= 0 && <line className="cursor" x1={x(shown)} x2={x(shown)} y1={PAD.top} y2={H - PAD.bottom} />}
        <path d={path(0, pastEnd)} fill="none" stroke={PREDICTED} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {outlookAt > 0 && (
          <path d={path(outlookAt - 1, outlookAt)} fill="none" stroke={PREDICTED} strokeWidth="2" strokeDasharray="4 3" strokeLinecap="round" />
        )}
        {points.map((pt, i) => pt && (
          flooded[i] === 1
            ? <circle key={i} cx={pt[0]} cy={pt[1]} r="5" fill={RECORDED} stroke="#fff" strokeWidth="2" />
            : <circle key={i} cx={pt[0]} cy={pt[1]} r={i === shown ? 4 : 2.5} fill={i === outlookAt ? "#fff" : PREDICTED} stroke={PREDICTED} strokeWidth={i === outlookAt ? 2 : 0} />
        ))}
        {years.map((yr, i) => (
          <text key={yr} className="tick" x={x(i)} y={H - 6} textAnchor="middle" fontWeight={i === shown ? 700 : 400}>
            {`’${String(yr).slice(2)}`}
          </text>
        ))}
        {/* Hit targets: the whole column for each year, wider than the marks. */}
        {years.map((yr, i) => (
          <rect key={yr} x={x(i) - step / 2} y={0} width={step} height={H} fill="transparent" style={{ cursor: "pointer" }}
            onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onClick={() => onYear(yr)}>
            <title>{`${yr}: ${probability[i]?.toFixed(3) ?? "—"}`}</title>
          </rect>
        ))}
      </svg>
      {hover != null && (
        <div className="tip" style={tipPlace(x(hover) / W)}>
          <strong>{probability[hover]?.toFixed(3) ?? "—"}</strong>
          <span>{years[hover]} flood score{probability[hover] >= threshold ? " · at or above threshold" : ""}</span>
          <span>{flooded[hover] == null ? "No flood record yet (outlook)" : flooded[hover] ? "DFO flood recorded" : "No DFO flood recorded"}</span>
        </div>
      )}
      <p className="trend-legend">
        <span><i className="line" style={{ background: PREDICTED }} />Flood score</span>
        <span><i className="dot" style={{ background: RECORDED }} />DFO flood recorded</span>
        <span><i className="dash" />Threshold {threshold}</span>
      </p>
    </div>
  );
}
