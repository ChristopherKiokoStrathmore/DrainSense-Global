"use client";

import { useEffect } from "react";

// A tab left open across a rebuild asks for script chunks that no longer exist. One reload fixes it.
const STALE = /ChunkLoadError|Loading chunk|Failed to fetch dynamically imported module/;

export default function DeskError({ error, reset }) {
  const stale = STALE.test(`${error?.name} ${error?.message}`);

  useEffect(() => {
    console.error(error);
    if (!stale) return;
    try {
      if (sessionStorage.getItem("drainsense-reloaded")) return;
      sessionStorage.setItem("drainsense-reloaded", "1");
    } catch {
      // Storage blocked: reload anyway, the browser stops a loop on its own page error.
    }
    window.location.reload();
  }, [error, stale]);

  return (
    <main className="sheet">
      <p className="kicker">This page hit an error</p>
      <h1>Something broke on this page</h1>
      <p className="error">{stale ? "The desk was updated since this tab opened. Reloading…" : error?.message || "Unknown error"}</p>
      <p className="reading">The rest of the desk still works from the menu on the left.</p>
      <p>
        <button className="solid" type="button" onClick={() => reset()}>Try again</button>{" "}
        <button className="ghost" type="button" onClick={() => window.location.reload()}>Reload the page</button>
      </p>
    </main>
  );
}
