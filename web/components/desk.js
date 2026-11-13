"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";

// The one-page desk: what one section asks of another. The map's selected cell, a ledger form opened from the map
// or the dispatch list, and a revision number that goes up after every ledger save so the map dots, the dispatch
// plan and the overview figures reload.
const DeskContext = createContext(null);

export const SECTIONS = [
  ["overview", "Overview"],
  ["map", "Map"],
  ["dispatch", "Dispatch"],
  ["ledger", "Ledger"],
  ["sectors", "Sectors"],
  // ["impact", "Impact"],
  // ["model", "Model card"],
  // ["api", "API"],
];

export function scrollToSection(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function DeskProvider({ children }) {
  const [revision, setRevision] = useState(0);
  // { id, at }: `at` makes a second request for the same cell still count as a new one.
  const [mapCell, setMapCell] = useState(null);
  const [formRequest, setFormRequest] = useState(null);

  const bump = useCallback(() => setRevision((n) => n + 1), []);
  const showOnMap = useCallback((id) => {
    setMapCell({ id, at: Date.now() });
    scrollToSection("map");
  }, []);
  const openForm = useCallback((kind, cellId) => {
    setFormRequest({ kind, cellId, at: Date.now() });
    scrollToSection("record");
  }, []);

  const value = useMemo(
    () => ({ revision, bump, mapCell, showOnMap, formRequest, openForm }),
    [revision, bump, mapCell, showOnMap, formRequest, openForm],
  );
  return <DeskContext.Provider value={value}>{children}</DeskContext.Provider>;
}

export function useDesk() {
  return useContext(DeskContext);
}
