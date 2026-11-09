"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const LINKS = [
  ["/", "Overview"],
  ["/map", "Map"],
  ["/operations", "Ledger"],
  ["/response", "Dispatch"],
  ["/sectors", "Sectors"],
  // ["/impact", "Impact"],
  // ["/model", "Model card"],
  // ["/endpoints", "API"],
];

export default function Nav() {
  const path = usePathname();
  const [open, setOpen] = useState(false);

  // Close the menu after navigating, and on Escape.
  useEffect(() => {
    setOpen(false);
  }, [path]);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => event.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <button
        type="button"
        className={open ? "burger open" : "burger"}
        aria-label={open ? "Close menu" : "Open menu"}
        aria-expanded={open}
        aria-controls="desk-nav"
        onClick={() => setOpen((on) => !on)}
      >
        <span />
        <span />
        <span />
      </button>
      <nav id="desk-nav" className={open ? "spine open" : "spine"} aria-label="Desk">
        {LINKS.map(([href, label]) => {
          const active = href === "/" ? path === "/" : path.startsWith(href);
          return (
            <Link key={href} href={href} className={active ? "on" : ""} onClick={() => setOpen(false)}>
              {label}
            </Link>
          );
        })}
      </nav>
    </>
  );
}