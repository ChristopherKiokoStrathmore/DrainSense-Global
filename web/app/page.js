"use client";

import { DeskProvider, TABS, useDesk } from "../components/desk";
import { SheetLinks, Tabs } from "../components/Nav";
import { Intro, Sheets } from "../components/Overlays";
import DispatchSection from "../components/sections/DispatchSection";
import ForecastSection from "../components/sections/ForecastSection";
import LedgerSection from "../components/sections/LedgerSection";
import { MapCanvas, MapPanel, MapProvider } from "../components/sections/MapSection";
import SectorsSection from "../components/sections/SectorsSection";

// One screen: the map fills it, the side panel on the left holds the working tabs, the header pill on the right
// opens the reference sheets. Each tab loads its own data, so one failing endpoint empties one tab only.
// Tabs stay mounted when hidden, so a half-filled form or a scrolled list is kept.
const PANELS = {
  map: MapPanel,
  forecast: ForecastSection,
  report: () => <LedgerSection part="form" />,
  dispatch: DispatchSection,
  sectors: SectorsSection,
  tracker: () => <LedgerSection part="tracker" />,
};

export default function Desk() {
  return (
    <DeskProvider>
      <MapProvider>
        <main className="app">
          <MapCanvas />
          <header className="pillbar">
            <a className="mark" href="#map" aria-label="DrainSense, risk map">
              <Grate />
              <span>Drain<span>Sense</span></span>
            </a>
            <span className="badge" title="Scores are for 5° cells, label years 2014–2024. They are outlooks, not warnings.">5° cells · not a warning</span>
            <SheetLinks />
          </header>
          <SidePanel />
        </main>
        <Sheets />
        <Intro />
      </MapProvider>
    </DeskProvider>
  );
}

function SidePanel() {
  const { tab } = useDesk();
  return (
    <aside className="side" aria-label="Flood desk">
      <div className="brand">
        <b>Drain<span>Sense</span></b>
        <div>Flood-risk and drainage desk</div>
      </div>
      <Tabs />
      {TABS.map(([id]) => {
        const Panel = PANELS[id];
        return (
          <section key={id} id={`panel-${id}`} role="tabpanel" aria-labelledby={`tab-${id}`} className="tab" hidden={tab !== id}>
            <Panel />
          </section>
        );
      })}
    </aside>
  );
}

function Grate() {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true" className="grate">
      <rect x="3" y="3" width="42" height="42" rx="3" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <path d="M12 10v28M24 10v28M36 10v28M8 16h32M8 24h32M8 32h32" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="36" cy="36" r="3.2" fill="#ff6a3d" stroke="none" />
    </svg>
  );
}
