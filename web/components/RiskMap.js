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

// The one colour ramp: the cells, the heatmap and the flat fallback all read it. Keep the legend bar in the CSS in step.
const STOPS = [
  [0, [0x16, 0x34, 0x3a]],
  [0.08, [0x1f, 0x6f, 0x68]],
  [0.2, [0xd7, 0xa1, 0x5a]],
  [0.4, [0xe2, 0x5b, 0x32]],
  [0.7, [0xf3, 0xd2, 0xc4]],
];
const TOP = STOPS.at(-1)[0];
const hex = ([r, g, b]) => `rgb(${r},${g},${b})`;
const rgba = ([r, g, b], a) => `rgba(${r},${g},${b},${a})`;

function riskColor(p) {
  const value = Math.max(0, Math.min(p ?? 0, TOP));
  const upper = STOPS.findIndex(([at]) => at >= value);
  if (upper <= 0) return hex(STOPS[0][1]);
  const [a, ca] = STOPS[upper - 1];
  const [b, cb] = STOPS[upper];
  const t = (value - a) / (b - a);
  return `rgb(${ca.map((c, i) => Math.round(c + (cb[i] - c) * t)).join(",")})`;
}

// Heatmap tuning. If too much of the map turns hot, lower the intensity numbers; if it looks faint, raise them.
// The radius is in screen pixels per zoom level; a 5 degree cell is about 21 px wide at the starting zoom.
const HEAT = {
  opacity: 0.95,
  intensity: ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 3],
  radius: ["interpolate", ["linear"], ["zoom"], 0, 12, 2, 45, 4, 170, 6, 400],
  // Nothing at density 0, a faint teal wash for low density, then the same ramp as the cells.
  color: [
    "interpolate", ["linear"], ["heatmap-density"],
    0, rgba(STOPS[0][1], 0),
    0.03, rgba(STOPS[1][1], 0.3),
    ...STOPS.slice(1).flatMap(([at, c]) => [at / TOP, rgba(c, 0.92)]),
  ],
};

// Each cell becomes five points one degree apart in latitude. The map stretches latitude towards the poles,
// so one point per cell leaves horizontal gaps there; the extra points close them.
const LAT_STEPS = [-2, -1, 0, 1, 2];

const collection = (features) => ({ type: "FeatureCollection", features: features || [] });

function cellCentre(feature) {
  const ring = feature.geometry.coordinates[0];
  const lons = ring.map((c) => c[0]);
  const lats = ring.map((c) => c[1]);
  return [(Math.min(...lons) + Math.max(...lons)) / 2, (Math.min(...lats) + Math.max(...lats)) / 2];
}

function centres(features) {
  return (features || []).flatMap((feature) => {
    const [lon, lat] = cellCentre(feature);
    const weight = Math.min(feature.properties.probability ?? 0, TOP) / TOP / LAT_STEPS.length;
    return LAT_STEPS.map((step) => ({
      type: "Feature",
      properties: { weight },
      geometry: { type: "Point", coordinates: [lon, Math.max(-85, Math.min(85, lat + step))] },
    }));
  });
}

// The cell polygons stay on the map as an almost invisible layer in heatmap view, so hover and click still work.
function show(map, id, visible) {
  if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visible ? "visible" : "none");
}

function applyView(map, view) {
  if (!map.getLayer("heat")) return;
  const heat = view === "heat";
  map.setPaintProperty("heat", "heatmap-opacity", heat ? HEAT.opacity : 0);
  map.setPaintProperty("cells", "fill-opacity", heat ? 0.01 : 0.78);
  map.setPaintProperty("cells-line", "line-opacity", heat ? 0 : 0.55);
  // A square outline suits the squares; the smooth surface gets a soft ring instead.
  show(map, "picked", !heat);
  show(map, "picked-halo", heat);
  show(map, "picked-ring", heat);
}

// The ring sits on the centre of the selected cell.
function updatePicked(map, features, id) {
  const source = map.getSource("picked-point");
  if (!source) return;
  const feature = (features || []).find((f) => f.properties.cell_id === id);
  source.setData(collection(feature ? [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: cellCentre(feature) } }] : []));
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
  const [view, setView] = useState("heat");
  const viewRef = useRef(view);
  viewRef.current = view;
  const [showReports, setShowReports] = useState(true);
  const reportsRef = useRef(showReports);
  reportsRef.current = showReports;
  const featuresRef = useRef(features);
  featuresRef.current = features;

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
        // Fit the inhabited latitudes to whatever size the window is, instead of a fixed zoom.
        bounds: [[-170, -58], [180, 78]],
        fitBoundsOptions: { padding: 12 },
        renderWorldCopies: false,
        attributionControl: true,
      });
    } catch (err) {
      setFallback(`the interactive map could not start (${err.message}).`);
      return undefined;
    }
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-left");
    const tip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10 });
    let ready = false;
    map.on("error", (event) => {
      // Without its style the map never loads and would show no cells. The flat view still does.
      if (!ready && String(event.error?.url || "").includes("style.json")) {
        setFallback(`the basemap style did not load (${event.error.status || event.error.message}).`);
      }
    });
    map.on("load", () => {
      ready = true;
      // Everything goes under the style's place labels, so country and city names stay readable.
      const labels = map.getStyle().layers.find((layer) => layer.type === "symbol")?.id;
      map.addSource("risk", { type: "geojson", data: collection(features) });
      map.addSource("risk-points", { type: "geojson", data: collection(centres(features)) });
      map.addLayer({
        id: "heat",
        type: "heatmap",
        source: "risk-points",
        paint: {
          "heatmap-weight": ["get", "weight"],
          "heatmap-intensity": HEAT.intensity,
          "heatmap-radius": HEAT.radius,
          "heatmap-color": HEAT.color,
          "heatmap-opacity": HEAT.opacity,
        },
      }, labels);
      map.addLayer({
        id: "cells",
        type: "fill",
        source: "risk",
        paint: {
          "fill-color": ["interpolate", ["linear"], ["get", "probability"], ...STOPS.flatMap(([at, c]) => [at, hex(c)])],
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
      map.addSource("picked-point", { type: "geojson", data: collection([]) });
      map.addLayer({
        id: "picked-halo",
        type: "circle",
        source: "picked-point",
        paint: { "circle-radius": 20, "circle-color": "#ffffff", "circle-opacity": 0.16, "circle-blur": 0.7 },
      }, labels);
      map.addLayer({
        id: "picked-ring",
        type: "circle",
        source: "picked-point",
        paint: {
          "circle-radius": 7,
          "circle-color": "rgba(255,255,255,0)",
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2.5,
        },
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
      applyView(map, viewRef.current);
      show(map, "reports", reportsRef.current);
      updatePicked(map, featuresRef.current, selectedRef.current);
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
      map.on("mousemove", "cells", (event) => {
        const props = event.features?.[0]?.properties;
        if (props) tip.setLngLat(event.lngLat).setText(`${props.cell_id} · ${Number(props.probability).toFixed(2)}`).addTo(map);
      });
      map.on("mouseleave", "cells", () => tip.remove());
    });
    mapRef.current = map;
    return () => {
      tip.remove();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      map.getSource("risk")?.setData(collection(features));
      map.getSource("risk-points")?.setData(collection(centres(features)));
      updatePicked(map, features, selectedRef.current);
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
    updatePicked(map, featuresRef.current, selectedId);
  }, [selectedId]);

  useEffect(() => {
    const map = mapRef.current;
    if (map) applyView(map, view);
  }, [view]);

  useEffect(() => {
    const map = mapRef.current;
    if (map) show(map, "reports", showReports);
  }, [showReports]);

  if (fallback) return <FlatMap features={features} marks={marks} selectedId={selectedId} onSelect={onSelect} reason={fallback} />;
  return (
    <>
      <div ref={holder} className="map" />
      <div className="mapbar">
        <div className="viewswitch" role="group" aria-label="Map view">
          <button type="button" aria-pressed={view === "heat"} onClick={() => setView("heat")}
            title="Smooths neighbouring cells into one surface. Colours show relative concentration, not exact probability.">
            Heatmap
          </button>
          <button type="button" aria-pressed={view === "cells"} onClick={() => setView("cells")}
            title="One square per 5° cell, coloured by its exact probability">
            Cells
          </button>
        </div>
        <button type="button" className="reportswitch" aria-pressed={showReports} onClick={() => setShowReports((on) => !on)}
          title="Cells with open blocked-drain reports. A bigger dot means more open reports.">
          <i /> Drain reports
        </button>
      </div>
    </>
  );
}