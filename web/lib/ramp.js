// One place for the map's colours, shared by the map, the flat fallback and the legend.
// The ramp always runs from quiet to loud: on a light basemap low risk is pale and high risk is deep red,
// on a dark basemap low risk is dark and high risk is bright. Set NEXT_PUBLIC_BASEMAP=dark for the dark one.
const THEMES = {
  light: {
    tiles: "light_all", // CARTO Positron. Try "voyager" for a more colourful map.
    ocean: "#dfe6e8",
    edge: "#ffffff",
    picked: "#1b1612",
    opacity: 0.84,
    stops: [
      [0, "#d3e2de"],
      [0.08, "#8fc2b4"],
      [0.2, "#f0c168"],
      [0.4, "#e8742f"],
      [0.7, "#b0102a"],
    ],
  },
  dark: {
    tiles: "dark_all",
    ocean: "#0c171c",
    edge: "#0c171c",
    picked: "#f4efe4",
    opacity: 0.92,
    stops: [
      [0, "#16343a"],
      [0.08, "#1f6f68"],
      [0.2, "#d7a15a"],
      [0.4, "#ee6a2f"],
      [0.7, "#ff2e4d"],
    ],
  },
};

export const THEME = THEMES[process.env.NEXT_PUBLIC_BASEMAP === "dark" ? "dark" : "light"];
export const STOPS = THEME.stops;

const top = STOPS[STOPS.length - 1][0];
export const TOP = top;
export const gradient = `linear-gradient(90deg, ${STOPS.map(([at, colour]) => `${colour} ${((at / top) * 100).toFixed(1)}%`).join(", ")})`;

export const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
const withAlpha = (hex, alpha) => `rgba(${rgb(hex).join(",")},${alpha})`;

// Heatmap colour by density: transparent where there is nothing, then the same ramp as the cells.
// Density 1 is the colour of the highest stop.
export const heatmapColor = [
  "interpolate",
  ["linear"],
  ["heatmap-density"],
  0, withAlpha(STOPS[0][1], 0),
  0.02, withAlpha(STOPS[0][1], 0.3),
  ...STOPS.slice(1).flatMap(([at, colour]) => [at / top, withAlpha(colour, 0.92)]),
];