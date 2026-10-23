"use client";

import { useEffect, useRef } from "react";
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

export default function RiskMap({ features, selectedId, onSelect }) {
  const holder = useRef(null);
  const mapRef = useRef(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    const map = new maplibregl.Map({
      container: holder.current,
      style: STYLE,
      center: [20, 8],
      zoom: 1.55,
      attributionControl: true,
    });
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

  return <div ref={holder} className="map" />;
}
