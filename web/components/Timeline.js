"use client";

import { useEffect, useRef, useState } from "react";

// Slots 1 and 2 of the dataviz reference palette, validated together on a light surface.
export const PREDICTED = "#2a78d6";
export const RECORDED = "#eb6834";
const PLAY_MS = 1400;

const PHASE_LABEL = { fit: "Fitted", test: "Held-out test", outlook: "Outlook" };
const span = (years) => (years.length > 1 ? `${years[0]}–${String(years.at(-1)).slice(2)}` : `${years[0]}`);

/**
 * One column per label year: cells the model put at or above its threshold, and cells with a recorded DFO flood.
 * Picking a column repaints the map for that year. The outlook year has no recorded column yet.
 */
export default function Timeline({ timeline, year, onYear }) {
  const [playing, setPlaying] = useState(false);
  const [hover, setHover] = useState(null);
  const yearRef = useRef(year);
  yearRef.current = year;
  const years = timeline.years;

  // Play walks forward one year at a time and stops on the outlook.
  useEffect(() => {
    if (!playing) return undefined;
    const timer = setInterval(() => {
      const at = years.indexOf(yearRef.current);
      if (at >= years.length - 1) {
        setPlaying(false);
        return;
      }
      onYear(years[at + 1]);
    }, PLAY_MS);
    return () => clearInterval(timer);
  }, [playing, years, onYear]);

  function togglePlay() {
    if (!playing && year === years.at(-1)) onYear(years[0]);
    setPlaying((on) => !on);
  }

  function onKey(event) {
    const at = years.indexOf(year);
    const next = event.key === "ArrowRight" ? at + 1 : event.key === "ArrowLeft" ? at - 1 : event.key === "Home" ? 0 : event.key === "End" ? years.length - 1 : null;
    if (next == null || next < 0 || next >= years.length) return;
    event.preventDefault();
    setPlaying(false);
    onYear(years[next]);
    event.currentTarget.querySelectorAll("button")[next]?.focus();
  }

  const max = Math.max(...timeline.summary.flatMap((row) => [row.cells_above_threshold, row.recorded_flood_cells ?? 0]), 1);
  const shown = timeline.summary.find((row) => row.year === (hover ?? year));

  return (
    <div className="timeline">
      <div className="timeline-head">
        <button type="button" className="play" onClick={togglePlay} aria-pressed={playing}
          title={playing ? "Pause" : "Play the years in order"}>
          {playing ? <PauseIcon /> : <PlayIcon />}
          <span>{playing ? "Pause" : "Play"}</span>
        </button>
        <p className="timeline-legend">
          <span><i style={{ background: PREDICTED }} />Cells at or above threshold {timeline.threshold}</span>
          <span><i style={{ background: RECORDED }} />Cells with a recorded DFO flood</span>
        </p>
      </div>

      <div className="phases" aria-hidden="true" style={{ gridTemplateColumns: `repeat(${years.length}, 1fr)` }}>
        {timeline.phases.filter((p) => p.years.length).map((phase) => (
          <span key={phase.phase} className={`phase ${phase.phase}`} title={phase.note}
            style={{ gridColumn: `${years.indexOf(phase.years[0]) + 1} / span ${phase.years.length}` }}>
            {phase.years.length > 1 ? `${PHASE_LABEL[phase.phase]} ${span(phase.years)}` : PHASE_LABEL[phase.phase]}
          </span>
        ))}
      </div>

      <div className="years" role="radiogroup" aria-label="Label year shown on the map" onKeyDown={onKey}
        style={{ gridTemplateColumns: `repeat(${years.length}, 1fr)` }}>
        {timeline.summary.map((row) => {
          const picked = row.year === year;
          return (
            <button key={row.year} type="button" role="radio" aria-checked={picked} tabIndex={picked ? 0 : -1}
              className={`year ${row.phase}${picked ? " on" : ""}`}
              aria-label={`${row.year}: ${row.cells_above_threshold} cells at or above threshold${row.recorded_flood_cells == null ? ", no flood record yet" : `, ${row.recorded_flood_cells} recorded`}`}
              onClick={() => { setPlaying(false); onYear(row.year); }}
              onMouseEnter={() => setHover(row.year)} onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(row.year)} onBlur={() => setHover(null)}>
              <span className="bars">
                <span className="b" style={{ height: `${(row.cells_above_threshold / max) * 100}%`, background: PREDICTED }} />
                {row.recorded_flood_cells == null
                  ? <span className="b pending" />
                  : <span className="b" style={{ height: `${(row.recorded_flood_cells / max) * 100}%`, background: RECORDED }} />}
              </span>
              <span className="y">{row.phase === "outlook" ? row.year : `’${String(row.year).slice(2)}`}</span>
            </button>
          );
        })}
      </div>

      {shown && <YearReadout row={shown} />}
    </div>
  );
}

function YearReadout({ row }) {
  return (
    <p className="readout" aria-live="polite">
      <strong>{row.year}</strong>
      <span className="muted">{PHASE_LABEL[row.phase]} · from {row.feature_year} rainfall</span>
      <span><b>{row.cells_above_threshold}</b> flagged</span>
      {row.recorded_flood_cells == null ? (
        <span className="muted">not yet in the flood catalogue</span>
      ) : (
        <>
          <span><b>{row.recorded_flood_cells}</b> recorded</span>
          <span><b>{row.hits}</b> caught · <b>{row.missed}</b> missed</span>
          <span>ROC-AUC <b>{row.roc_auc?.toFixed(2) ?? "—"}</b></span>
        </>
      )}
    </p>
  );
}

function PlayIcon() {
  return <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor" /></svg>;
}

function PauseIcon() {
  return <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" fill="currentColor" /></svg>;
}
