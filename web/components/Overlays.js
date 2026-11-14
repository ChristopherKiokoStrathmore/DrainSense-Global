"use client";

import { useEffect, useRef, useState } from "react";
import { SHEETS, useDesk } from "./desk";
// Impact model, Model card and API are hidden for now; see SHEETS in desk.js.
// import ApiSection from "./sections/ApiSection";
// import ImpactSection from "./sections/ImpactSection";
// import ModelSection from "./sections/ModelSection";
import OverviewSection from "./sections/OverviewSection";

const SHEET_BODY = {
  overview: OverviewSection,
  // impact: ImpactSection,
  // model: ModelSection,
  // api: ApiSection,
};
const INTRO_KEY = "drainsense:intro-seen";

/**
 * The reference pages, opened over the map from the header. A sheet stays mounted once opened, so a half-filled
 * impact form or a loaded schema is still there the next time it is opened.
 */
export function Sheets() {
  const { sheet, closeSheet } = useDesk();
  const [opened, setOpened] = useState([]);
  const closeRef = useRef(null);

  useEffect(() => {
    if (!sheet) return undefined;
    setOpened((list) => (list.includes(sheet) ? list : [...list, sheet]));
    const onKey = (event) => {
      if (event.key === "Escape") closeSheet();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sheet, closeSheet]);

  // The first time, the box only exists after `opened` updates, so focus waits for it.
  useEffect(() => {
    if (sheet) closeRef.current?.focus();
  }, [sheet, opened.length]);

  if (!opened.length) return null;
  const title = SHEETS.find(([id]) => id === sheet)?.[1];
  return (
    <div className="sheet-modal" hidden={!sheet} onMouseDown={(event) => event.target === event.currentTarget && closeSheet()}>
      <div className="sheet-box" role="dialog" aria-modal="true" aria-label={title}>
        <div className="sheet-bar">
          <b>{title}</b>
          <button ref={closeRef} type="button" className="act ghost small" onClick={closeSheet}>Close</button>
        </div>
        <div className="sheet-body">
          {opened.map((id) => {
            const Body = SHEET_BODY[id];
            return <div key={id} hidden={id !== sheet}><Body /></div>;
          })}
        </div>
      </div>
    </div>
  );
}

/** What the desk is for, shown once per browser session before the map. A shared link skips it. */
export function Intro() {
  const [open, setOpen] = useState(false);
  const { go } = useDesk();

  useEffect(() => {
    let seen = false;
    try {
      seen = Boolean(window.sessionStorage.getItem(INTRO_KEY));
    } catch {
      // Storage blocked: show it, it closes with one click.
    }
    const linked = window.location.hash || new URLSearchParams(window.location.search).has("cell");
    if (!seen && !linked) setOpen(true);
  }, []);

  function close() {
    try {
      window.sessionStorage.setItem(INTRO_KEY, "1");
    } catch {
      // Storage blocked: it shows again next load, nothing else.
    }
    setOpen(false);
  }

  if (!open) return null;
  return (
    <div className="intro">
      <div className="intro-box" role="dialog" aria-modal="true" aria-labelledby="intro-title">
        <h1 id="intro-title">Drain<span>Sense</span></h1>
        <p className="tag">See where a large flood is most likely, why, and which drains to send a crew to first.</p>
        <div className="pt"><b>Problem</b><span>Flood records, rainfall and terrain, and residents' drain reports sit in different places, so crews go where the last complaint came from.</span></div>
        <div className="pt"><b>Idea</b><span>Score flood risk per 5° cell from last year's rainfall and terrain, explain each score, and rank cells by risk, open reports and repeat incidents together.</span></div>
        <div className="pt"><b>Value</b><span>Inspections where risk and reports overlap, repairs checked against the incidents that follow, and one flood picture for farm suppliers, health teams, insurers and logistics planners.</span></div>
        <button type="button" className="act" onClick={close} autoFocus>Open the desk</button>
        <p className="mute">
          Scores are outlooks, not warnings.{" "}
          <button type="button" className="link" onClick={() => { close(); go("overview"); }}>How it works</button>
        </p>
      </div>
    </div>
  );
}
