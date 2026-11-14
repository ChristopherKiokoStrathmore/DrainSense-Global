"use client";

import { SHEETS, TABS, useDesk } from "./desk";

// The side panel's tabs. Arrow keys move between them, as in any tab list.
export function Tabs() {
  const { tab, go } = useDesk();

  function onKey(event) {
    const at = TABS.findIndex(([id]) => id === tab);
    const next = event.key === "ArrowRight" ? at + 1 : event.key === "ArrowLeft" ? at - 1 : null;
    if (next == null) return;
    event.preventDefault();
    const [id] = TABS[(next + TABS.length) % TABS.length];
    go(id);
    event.currentTarget.querySelector(`#tab-${id}`)?.focus();
  }

  return (
    <nav className="tabs" role="tablist" aria-label="Desk" onKeyDown={onKey}>
      {TABS.map(([id, label]) => (
        <button key={id} id={`tab-${id}`} type="button" role="tab" aria-selected={tab === id} aria-controls={`panel-${id}`}
          tabIndex={tab === id ? 0 : -1} className={tab === id ? "on" : ""} onClick={() => go(id)}>
          {label}
        </button>
      ))}
    </nav>
  );
}

// Links in the header pill to the reference sheets that open over the map.
export function SheetLinks() {
  const { sheet, go } = useDesk();
  return (
    <nav className="sheet-links" aria-label="Reference">
      {SHEETS.map(([id, label]) => (
        <button key={id} type="button" aria-pressed={sheet === id} onClick={() => go(id)}>{label}</button>
      ))}
    </nav>
  );
}
