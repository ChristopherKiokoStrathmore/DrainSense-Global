"use client";

import { useEffect, useState } from "react";
import { SECTIONS, scrollToSection } from "./desk";

// Where a section counts as "being read": its top has passed this line, just under the masthead.
const READ_LINE = 120;
// How long a deep link (/#map) keeps its section aligned while the sections above load their data.
const SETTLE_MS = 5000;

// In-page links. The last section whose top has passed the reading line is marked as the current one.
export default function Nav() {
  const [current, setCurrent] = useState("overview");

  useEffect(() => {
    let frame = 0;
    const update = () => {
      frame = 0;
      let found = SECTIONS[0][0];
      for (const [id] of SECTIONS) {
        const node = document.getElementById(id);
        if (node && node.getBoundingClientRect().top <= READ_LINE) found = id;
      }
      // At the very bottom the last section may never reach the line; it is still the one on screen.
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2) found = SECTIONS.at(-1)[0];
      setCurrent(found);
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    // Sections grow as their data arrives, which moves the others without a scroll event.
    const grow = new ResizeObserver(schedule);
    const main = document.querySelector("main");
    if (main) grow.observe(main);
    update();
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      grow.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  // On a narrow screen the links scroll sideways; keep the current one in view.
  useEffect(() => {
    const nav = document.querySelector("nav.spine");
    const link = nav?.querySelector(`a[href="#${current}"]`);
    if (!link || nav.scrollWidth <= nav.clientWidth) return;
    nav.scrollTo({ left: link.offsetLeft - (nav.clientWidth - link.offsetWidth) / 2, behavior: "smooth" });
  }, [current]);

  // A deep link lands before the sections above it have loaded, so it drifts as they grow.
  // Keep it aligned until the page settles or the reader scrolls on their own.
  useEffect(() => {
    const target = window.location.hash && document.getElementById(window.location.hash.slice(1));
    const main = document.querySelector("main");
    if (!target || !main) return undefined;
    const align = () => target.scrollIntoView({ behavior: "instant", block: "start" });
    const hold = new ResizeObserver(align);
    const release = () => {
      hold.disconnect();
      for (const type of ["wheel", "touchstart", "keydown", "mousedown"]) window.removeEventListener(type, release);
    };
    hold.observe(main);
    for (const type of ["wheel", "touchstart", "keydown", "mousedown"]) window.addEventListener(type, release, { passive: true });
    const timer = setTimeout(release, SETTLE_MS);
    align();
    return () => {
      clearTimeout(timer);
      release();
    };
  }, []);

  return (
    <nav className="spine" aria-label="Desk">
      {SECTIONS.map(([id, label]) => (
        <a key={id} href={`#${id}`} className={current === id ? "on" : ""} aria-current={current === id ? "location" : undefined}
          onClick={(event) => {
            event.preventDefault();
            window.history.replaceState(null, "", `#${id}`);
            scrollToSection(id);
          }}>
          {label}
        </a>
      ))}
    </nav>
  );
}
