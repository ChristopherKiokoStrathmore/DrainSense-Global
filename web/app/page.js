"use client";

import { DeskProvider } from "../components/desk";
import ApiSection from "../components/sections/ApiSection";
import DispatchSection from "../components/sections/DispatchSection";
import ImpactSection from "../components/sections/ImpactSection";
import LedgerSection from "../components/sections/LedgerSection";
import MapSection from "../components/sections/MapSection";
import ModelSection from "../components/sections/ModelSection";
import OverviewSection from "../components/sections/OverviewSection";
import SectorsSection from "../components/sections/SectorsSection";

// The whole desk on one page. Each section loads its own data, so one failing endpoint empties one section only.
export default function Desk() {
  return (
    <DeskProvider>
      <main>
        <section id="overview" className="slot"><OverviewSection /></section>
        <section id="map" className="slot slot-map" aria-label="Risk map"><MapSection /></section>
        <section id="dispatch" className="slot"><DispatchSection /></section>
        <section id="ledger" className="slot"><LedgerSection /></section>
        <section id="sectors" className="slot"><SectorsSection /></section>
        <section id="impact" className="slot"><ImpactSection /></section>
        <section id="model" className="slot"><ModelSection /></section>
        <section id="api" className="slot"><ApiSection /></section>
      </main>
    </DeskProvider>
  );
}
