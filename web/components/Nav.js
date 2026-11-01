"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  ["/", "Overview"],
  ["/map", "Map"],
  ["/operations", "Ledger"],
  ["/response", "Dispatch"],
  ["/sectors", "Sectors"],
  ["/impact", "Impact"],
  ["/model", "Model card"],
  ["/endpoints", "API"],
];

export default function Nav() {
  const path = usePathname();
  return (
    <nav className="spine" aria-label="Desk">
      {LINKS.map(([href, label]) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link key={href} href={href} className={active ? "on" : ""}>
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
