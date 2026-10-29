"use client";

import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

const STYLE = {
  version: 8,
  sources: {
    carto: {
      type: "raster",
      tiles: ["https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors © CARTO",
    },
  },
  layers: [{ id: "carto", type: "raster", source: "carto" }],
};

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

function webglAvailable() {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

/** The risk cells on a plain lon/lat grid, for browsers where MapLibre cannot start. */
function FlatMap({ features, selectedId, onSelect, reason }) {
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
      </svg>
      <p className="flatmap-note">Flat view: {reason} The cells and the dossier work as usual.</p>
    </div>
  );
}

export default function RiskMap({ features, selectedId, onSelect }) {
  const holder = useRef(null);
  const mapRef = useRef(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
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
        style: STYLE,
        center: [20, 8],
        zoom: 1.55,
        attributionControl: true,
      });
    } catch (err) {
      setFallback(`the interactive map could not start (${err.message}).`);
      return undefined;
    }
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-left");
    map.on("load", () => {
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
      });
      map.addLayer({
        id: "cells-line",
        type: "line",
        source: "risk",
        paint: { "line-color": "#081014", "line-width": 0.6, "line-opacity": 0.55 },
      });
      map.addLayer({
        id: "picked",
        type: "line",
        source: "risk",
        filter: ["==", ["get", "cell_id"], ""],
        paint: { "line-color": "#f4efe4", "line-width": 2.4 },
      });
      map.on("click", "cells", (event) => {
        const id = event.features?.[0]?.properties?.cell_id;
        if (id) onSelectRef.current(id);
      });
      map.on("mouseenter", "cells", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "cells", () => {
        map.getCanvas().style.cursor = "";
      });
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
    if (map.isStyleLoaded() && map.getSource("risk")) apply();
    else map.once("load", apply);
  }, [features]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.getLayer("picked")) return;
    map.setFilter("picked", ["==", ["get", "cell_id"], selectedId || ""]);
  }, [selectedId]);

  if (fallback) return <FlatMap features={features} selectedId={selectedId} onSelect={onSelect} reason={fallback} />;
  return <div ref={holder} className="map" />;
}
