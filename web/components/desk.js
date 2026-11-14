"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

// The desk is one screen: the map fills it, a side panel holds the working tabs, and the longer reference pages
// (overview, impact model, model card, API) open as sheets over the map. This context is what one part asks of
// another: which tab and sheet are open, the map's selected cell, a ledger form opened from elsewhere, and a
// revision number that goes up after every ledger save so the map dots, the dispatch plan and the tracker reload.
const DeskContext = createContext(null);

// Tabs in the side panel, in order.
export const TABS = [
  ["map", "Risk map"],
  ["forecast", "Forecast"],
  ["report", "Report"],
  ["dispatch", "Dispatch"],
  ["sectors", "Sectors"],
  ["tracker", "Tracker"],
];

// Sheets that open over the map.
export const SHEETS = [
  ["overview", "About"],
  // Hidden from the frontend for now. Uncomment here and in Overlays.js (and next.config.js) to bring them back.
  // ["impact", "Impact model"],
  // ["model", "Model card"],
  // ["api", "API"],
];

// Links from the old one-page desk (/#ledger, /#record) still land somewhere sensible.
const ALIASES = { ledger: "tracker", record: "report" };

const isTab = (id) => TABS.some(([key]) => key === id);
const isSheet = (id) => SHEETS.some(([key]) => key === id);

export function DeskProvider({ children }) {
  const [revision, setRevision] = useState(0);
  const [tab, setTabState] = useState("map");
  const tabRef = useRef(tab);
  tabRef.current = tab;
  const [sheet, setSheet] = useState(null);
  // { id, at }: `at` makes a second request for the same cell still count as a new one.
  const [mapCell, setMapCell] = useState(null);
  const [formRequest, setFormRequest] = useState(null);

  // Every place the desk can show is a hash, so a link to a tab or a sheet can be shared.
  const go = useCallback((raw) => {
    const id = ALIASES[raw] || raw;
    if (isTab(id)) {
      setTabState(id);
      setSheet(null);
    } else if (isSheet(id)) {
      setSheet(id);
    } else {
      return false;
    }
    if (window.location.hash !== `#${id}`) window.history.replaceState(null, "", `${window.location.search}#${id}`);
    return true;
  }, []);

  const closeSheet = useCallback(() => {
    setSheet(null);
    window.history.replaceState(null, "", `${window.location.search}#${tabRef.current}`);
  }, []);

  useEffect(() => {
    const fromHash = () => {
      const id = window.location.hash.slice(1);
      if (id) go(id);
      else if (new URLSearchParams(window.location.search).get("form")) go("report");
    };
    fromHash();
    // Plain <a href="#dispatch"> links anywhere on the desk switch tabs instead of jumping.
    const onClick = (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
      const link = event.target.closest?.('a[href^="#"]');
      if (link && go(link.getAttribute("href").slice(1))) event.preventDefault();
    };
    window.addEventListener("hashchange", fromHash);
    document.addEventListener("click", onClick);
    return () => {
      window.removeEventListener("hashchange", fromHash);
      document.removeEventListener("click", onClick);
    };
  }, [go]);

  const bump = useCallback(() => setRevision((n) => n + 1), []);
  const showOnMap = useCallback((id) => {
    setMapCell({ id, at: Date.now() });
    go("map");
  }, [go]);
  // patch: extra fields for the form, such as the report an inspection is linked to.
  const openForm = useCallback((kind, cellId, patch = null) => {
    setFormRequest({ kind, cellId, patch, at: Date.now() });
    go("report");
  }, [go]);

  const value = useMemo(
    () => ({ revision, bump, tab, sheet, go, closeSheet, mapCell, showOnMap, formRequest, openForm }),
    [revision, bump, tab, sheet, go, closeSheet, mapCell, showOnMap, formRequest, openForm],
  );
  return <DeskContext.Provider value={value}>{children}</DeskContext.Provider>;
}

export function useDesk() {
  return useContext(DeskContext);
}
