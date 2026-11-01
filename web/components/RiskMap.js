"use client";

import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

// CARTO Dark Matter as a vector style. Since 25 September 2026 CARTO serves its raster tiles only with a key;
// the vector styles still load without one, but CARTO may extend the requirement. A free key from
// carto.com/basemaps/apikey, set as NEXT_PUBLIC_CARTO_KEY at build time, is added to every CARTO request.
// It ships to the browser, so restrict it to the desk's domain in CARTO's dashboard.
const STYLE_URL = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
const CARTO_KEY = process.env.NEXT_PUBLIC_CARTO_KEY || "";

function withCartoKey(url) {
  if (!CARTO_KEY || !url.includes("basemaps.cartocdn.com") || /[?&]key=/.test(url)) return { url };
  return { url: `${url}${url.includes("?") ? "&" : "?"}key=${encodeURIComponent(CARTO_KEY)}` };
}

// Same stops as the fill-color expression below, for the flat fallback.
const STOPS = [
  [0, [0x16, 0x34, 0x3a]],
  [0.08, [0x1f, 0x6f, 0x68]],
  [0.2, [0xd7, 0xa1, 0x5a]],
  [0.4, [0xe2, 0x5b, 0x32]],
  [0.7, [0xf3, 0xd2, 0xc4]],
];

function riskColor(p) {
  const value = Math.max(0, Math.min(p ?? 0, STOPS.at(-1)[0]));
  const upper = STOPS.findIndex(([at]) => at >= value);
  if (upper <= 0) return `rgb(${STOPS[0][1].join(",")})`;
  const [a, ca] = STOPS[upper - 1];
  const [b, cb] = STOPS[upper];
  const t = (value - a) / (b - a);
  return `rgb(${ca.map((c, i) => Math.round(c + (cb[i] - c) * t)).join(",")})`;
}

// Open drain reports as points at the cell centers. marks: [{cell_id, lon, lat, open}]
function points(marks) {
  return {
    type: "FeatureCollection",
    features: (marks || []).map((mark) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [mark.lon, mark.lat] },
      properties: { cell_id: mark.cell_id, open: mark.open },
    })),
  };
}

function webglAvailable() {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

/** The risk cells on a plain lon/lat grid, for browsers where MapLibre cannot start. */
function FlatMap({ features, marks, selectedId, onSelect, reason }) {
  return (
    <div className="map flatmap">
      <svg viewBox="0 0 720 300" role="img" aria-label="Flood risk cells on a flat longitude-latitude grid">
        {(features || []).map((feature) => {
          const ring = feature.geometry.coordinates[0];
          const lons = ring.map((c) => c[0]);
          const lats = ring.map((c) => c[1]);
          const x = (Math.min(...lons) + 180) * 2;
          const y = (75 - Math.max(...lats)) * 2;
          const id = feature.properties.cell_id;
          const picked = id === selectedId;
          return (
            <rect key={id} x={x} y={y} width={(Math.max(...lons) - Math.min(...lons)) * 2}
              height={(Math.max(...lats) - Math.min(...lats)) * 2} fill={riskColor(feature.properties.probability)}
              stroke={picked ? "#f4efe4" : "#081014"} strokeWidth={picked ? 1.6 : 0.4}
              onClick={() => onSelect(id)} style={{ cursor: "pointer" }}>
              <title>{id} · {Number(feature.properties.probability).toFixed(2)}</title>
            </rect>
          );
        })}
        {(marks || []).map((mark) => (
          <circle key={mark.cell_id} cx={(mark.lon + 180) * 2} cy={(75 - mark.lat) * 2} r={2.5 + Math.min(mark.open, 4)}
            fill="#ffffff" stroke="#081014" strokeWidth={1.2} onClick={() => onSelect(mark.cell_id)} style={{ cursor: "pointer" }}>
            <title>{mark.cell_id} · {mark.open} open drain {mark.open === 1 ? "report" : "reports"}</title>
          </circle>
        ))}
      </svg>
      <p className="flatmap-note">Flat view: {reason} The cells and the dossier work as usual.</p>
    </div>
  );
}

export default function RiskMap({ features, marks, selectedId, onSelect }) {
  const holder = useRef(null);
  const mapRef = useRef(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  // A cell picked before the map loads (a ?cell= link) still gets its outline.
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  const [fallback, setFallback] = useState("");

  useEffect(() => {
    if (!webglAvailable()) {
      setFallback("this browser has WebGL turned off, so the interactive basemap cannot load.");
      return undefined;
    }
    let map;
    try {
      map = new maplibregl.Map({
        container: holder.current,
        style: STYLE_URL,
        transformRequest: withCartoKey,
        center: [20, 8],
        zoom: 1.55,
        attributionControl: true,
      });
    } catch (err) {
      setFallback(`the interactive map could not start (${err.message}).`);
      return undefined;
    }
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-left");
    let ready = false;
    map.on("error", (event) => {
      // Without its style the map never loads and would show no cells. The flat view still does.
      if (!ready && String(event.error?.url || "").includes("style.json")) {
        setFallback(`the basemap style did not load (${event.error.status || event.error.message}).`);
      }
    });
    map.on("load", () => {
      ready = true;
      // Cells go under the style's place labels, so country and city names stay readable.
      const labels = map.getStyle().layers.find((layer) => layer.type === "symbol")?.id;
      map.addSource("risk", {
        type: "geojson",
        data: { type: "FeatureCollection", features: features || [] },
      });
      map.addLayer({
        id: "cells",
        type: "fill",
        source: "risk",
        paint: {
          "fill-color": [
            "interpolate",
            ["linear"],
            ["get", "probability"],
            0, "#16343a",
            0.08, "#1f6f68",
            0.2, "#d7a15a",
            0.4, "#e25b32",
            0.7, "#f3d2c4",
          ],
          "fill-opacity": 0.78,
        },
      }, labels);
      map.addLayer({
        id: "cells-line",
        type: "line",
        source: "risk",
        paint: { "line-color": "#081014", "line-width": 0.6, "line-opacity": 0.55 },
      }, labels);
      map.addLayer({
        id: "picked",
        type: "line",
        source: "risk",
        filter: ["==", ["get", "cell_id"], selectedRef.current || ""],
        paint: { "line-color": "#f4efe4", "line-width": 2.4 },
      }, labels);
      map.addSource("reports", { type: "geojson", data: points(marks) });
      map.addLayer({
        id: "reports",
        type: "circle",
        source: "reports",
        paint: {
          "circle-radius": ["+", 4, ["*", 2, ["min", ["get", "open"], 4]]],
          "circle-color": "#ffffff",
          "circle-stroke-color": "#081014",
          "circle-stroke-width": 2,
        },
      });
      for (const layer of ["cells", "reports"]) {
        map.on("click", layer, (event) => {
          const id = event.features?.[0]?.properties?.cell_id;
          if (id) onSelectRef.current(id);
        });
        map.on("mouseenter", layer, () => {
          map.getCanvas().style.cursor = "pointer";
        });
        map.on("mouseleave", layer, () => {
          map.getCanvas().style.cursor = "";
        });
      }
    });
    mapRef.current = map;
    return () => map.remove();
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const data = { type: "FeatureCollection", features: features || [] };
    const apply = () => {
      const source = map.getSource("risk");
      if (source) source.setData(data);
    };
    if (map.getSource("risk")) apply();
    else map.once("load", apply);
  }, [features]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      const source = map.getSource("reports");
      if (source) source.setData(points(marks));
    };
    if (map.getSource("reports")) apply();
    else map.once("load", apply);
  }, [marks]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer("picked")) return;
    map.setFilter("picked", ["==", ["get", "cell_id"], selectedId || ""]);
  }, [selectedId]);

  if (fallback) return <FlatMap features={features} marks={marks} selectedId={selectedId} onSelect={onSelect} reason={fallback} />;
  return <div ref={holder} className="map" />;
}
