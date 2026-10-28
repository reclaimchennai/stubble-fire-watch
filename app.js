/* Stubble Fire Watch: farm fires and smoke over the northern subcontinent.
 * Data: NASA FIRMS (MODIS, VIIRS, Landsat), NASA GMAO GEOS-FP aerosols and winds, ICAR-IARI CREAMS bulletins.
 * Everything is precomputed by scripts/ into data/; this file only loads and renders it. */
(() => {
"use strict";

// ---------------------------------------------------------------- constants
const IST = 330 * 60e3;
const HOUR = 3600e3, DAY = 864e5;
const GROUPS = ["Punjab", "Haryana", "Uttar Pradesh", "Madhya Pradesh", "Rajasthan", "Punjab (PK)", "Other"];
const GROUP_COLORS = {
  "Punjab": "#e8604c", "Haryana": "#f2b33d", "Uttar Pradesh": "#4ea8de", "Madhya Pradesh": "#57c785",
  "Rajasthan": "#b48ef0", "Punjab (PK)": "#f08fb0", "Other": "#7d8592",
};
const SENSOR_COLORS = ["#f6a623", "#ffd166", "#ff6b35", "#ff3d6e", "#d36cf2", "#4ade80", "#46c08a"];
const SENSOR_SHORT = ["MODIS Terra", "MODIS Aqua", "VIIRS S-NPP", "VIIRS NOAA-20", "VIIRS NOAA-21", "Landsat", "CREAMS events"];
const SEASONS = { rice: { label: "Paddy residue (Sep 15 - Nov 30)", m0: 8, d0: 15, m1: 10, d1: 30 },
                  wheat: { label: "Wheat residue (Apr 1 - May 31)", m0: 3, d0: 1, m1: 4, d1: 31 } };
const BASIS = { modis: [0, 1], snpp: [2] };
const BASELINE_YEARS = [2016, 2025];
// smoke ramp: AOD stops (value, r, g, b, a); light grey-pink haze to deep violet, like CAMS OM AOD
const SMOKE_STOPS = [
  [0.00, 255, 255, 255, 0.00], [0.06, 238, 232, 238, 0.00], [0.12, 236, 222, 232, 0.32],
  [0.25, 240, 190, 220, 0.55], [0.40, 236, 130, 200, 0.70], [0.60, 214, 72, 178, 0.80],
  [0.90, 160, 34, 160, 0.86], [1.30, 98, 16, 128, 0.90], [2.00, 52, 6, 84, 0.94],
];
const SMOKE_TICKS = [0, 0.1, 0.25, 0.5, 1, 2];
const CHORO = ["#fff3b0", "#ffd166", "#fca048", "#f2683c", "#d63b48", "#a1205e", "#5e1260"];
const CHORO_BREAKS = [1, 10, 50, 150, 400, 1000, 2500];

const ESRI = "https://services.arcgisonline.com/arcgis/rest/services";
const CANVAS_TILES = {
  dark: `${ESRI}/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
  light: `${ESRI}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
};
const REF_TILES = {
  dark: `${ESRI}/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`,
  light: `${ESRI}/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`,
  imagery: `${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`,
};
const IMAGERY_TILES = `${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`;
const DEM_TILES = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";

// ---------------------------------------------------------------- helpers
const $ = (s) => document.querySelector(s);
const icon = (n) => `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${window.ICONS[n] || ""}</svg>`;
const paintIcons = (root = document) => root.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = icon(el.dataset.icon); });
const fmt = d3.format(",");
const fmtK = (v) => (v >= 1e4 ? d3.format(".3~s")(v).replace("G", "B") : fmt(Math.round(v)));
const istDate = (ms) => new Date(ms + IST);
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmtDay = (ms) => { const d = istDate(ms); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
const fmtDM = (ms) => { const d = new Date(ms); return `${String(d.getUTCDate()).padStart(2, "0")}/${String(d.getUTCMonth() + 1).padStart(2, "0")}`; };
const fmtClock = (ms) => { const d = istDate(ms); return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")} IST`; };
const isoDay = (ms) => new Date(ms).toISOString().slice(0, 10);
const dayUTC = (y, m, d) => Date.UTC(y, m, d);
const doyOf = (ms) => Math.floor((ms - Date.UTC(new Date(ms).getUTCFullYear(), 0, 1)) / DAY);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const groupOf = (state) => (GROUPS.includes(state) ? state : "Other");
// summary and smoke metadata change every hour: always revalidate them (the server answers 304 when unchanged)
// A stalled connection must not hang the page: each try is cut off after 20 s and retried (3 tries).
async function getJSON(u) {
  for (let i = 0; ; i++) {
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 20000);
    try {
      const r = await fetch(u, { signal: ctl.signal, ...(/summary\.json|smoke_meta\.json/.test(u) ? { cache: "no-cache" } : {}) });
      if (!r.ok) throw new Error(`${u}: ${r.status}`);
      return await r.json();
    } catch (e) {
      if (i >= 2) throw e;
      const b = document.getElementById("map-badge"); if (b && !S.summary) b.textContent = "Slow connection, retrying...";
      await new Promise((res) => setTimeout(res, 1000 * (i + 1)));
    } finally { clearTimeout(timer); }
  }
}
function loadImage(u) { return new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = u; }); }
const mercY = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));

// ---------------------------------------------------------------- state
const S = {
  summary: null, districtDaily: null, geo: {}, creams: null, smokeMeta: null,
  year: null, season: "rice",
  d1: 0, d2: 0,             // period, UTC ms at 00:00 of first and last day (inclusive)
  t: 0, t0: 0, t1: 0,       // current time and timeline range (UTC ms)
  playing: false, speed: 2,
  sensors: new Set([0, 1, 2, 3, 4, 5]),   // CREAMS (6) is off by default: it re-lists FIRMS detections
  metric: "n", basis: "modis",
  smokeVar: "smoke", fireMode: "points", base: "canvas", proj: "globe",
  points: null,             // decoded detections for S.year
  hiddenGroups: new Set(),
};
const pointCache = new Map();

// ---------------------------------------------------------------- data
async function loadPoints(year, season) {
  const key = `${year}|${season}`;
  if (pointCache.has(key)) return pointCache.get(key);
  const r = await fetch(`data/fires/points_${year}_${season}.bin${S.bust ? `?v=${encodeURIComponent(S.bust)}` : ""}`);
  const buf = r.ok ? await r.arrayBuffer() : new ArrayBuffer(0);
  const [W, Sb, E, N] = S.summary.bbox;
  const n = buf.byteLength / 14, dv = new DataView(buf);
  const y0 = Date.UTC(year, 0, 1);
  const raw = [];
  for (let i = 0; i < n; i++) {
    const o = i * 14, hhmm = dv.getUint16(o + 6, true);
    raw.push({
      lon: W + (dv.getUint16(o, true) / 65535) * (E - W),
      lat: Sb + (dv.getUint16(o + 2, true) / 65535) * (N - Sb),
      t: y0 + (dv.getUint16(o + 4, true) - 1) * DAY + (Math.floor(hhmm / 100) * 60 + (hhmm % 100)) * 60e3,
      frp: dv.getUint16(o + 8, true) / 10,
      d: dv.getUint16(o + 10, true) - 1,
      s: dv.getUint8(o + 12), c: dv.getUint8(o + 13),
    });
  }
  raw.sort((a, b) => a.t - b.t);
  const pts = { list: raw, times: Float64Array.from(raw, (p) => p.t) };
  pointCache.set(key, pts);
  return pts;
}
function lowerBound(arr, v) { let lo = 0, hi = arr.length; while (lo < hi) { const m = (lo + hi) >> 1; if (arr[m] < v) lo = m + 1; else hi = m; } return lo; }
function pointsBetween(a, b) {
  const P = S.points; if (!P) return [];
  const out = [];
  for (let i = lowerBound(P.times, a), j = lowerBound(P.times, b); i < j; i++) if (S.sensors.has(P.list[i].s)) out.push(P.list[i]);
  return out;
}
const periodEnd = () => S.d2 + DAY;

function seasonRange(year, season) {
  const s = SEASONS[season];
  return [dayUTC(year, s.m0, s.d0), dayUTC(year, s.m1, s.d1) + DAY];
}
function lastDataDay() { return Date.parse(S.summary.last_detection_date + "T00:00:00Z"); }

// daily series by group for a year and sensor set, from summary
function dailyByGroup(year, sensors, metric, from, to) {
  const yr = S.summary.daily[year] || {};
  const nd = Math.round((to - from) / DAY) + 1, d0 = doyOf(from);
  const out = GROUPS.map(() => new Float64Array(nd));
  for (const si of sensors) {
    const sd = yr[S.summary.sensors[si]]; if (!sd) continue;
    for (const [state, v] of Object.entries(sd)) {
      const gi = GROUPS.indexOf(groupOf(state)), arr = v[metric];
      for (let k = 0; k < nd; k++) out[gi][k] += arr[d0 + k] || 0;
    }
  }
  return out;
}

// ---------------------------------------------------------------- map
let map;
const smoke = { gl: null, canvas: null, prog: null, tex: {}, ready: false, lastKey: "" };
const wind = { canvas: null, ctx: null, field: null, particles: [], ready: false };
const theme = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

// Elevation tints: the plains (below ~300 m) get their own colour, so the Indo-Gangetic Plain reads as one
// flat basin walled in by the Himalaya, the Aravallis and the Vindhyas.
const RELIEF = {
  dark: [-100, "#0b1116", 0, "#13201a", 120, "#183021", 250, "#20361f", 350, "#33381f", 600, "#433b29", 1200, "#544838",
         2500, "#6b625a", 4000, "#9a9ca2", 5500, "#d9dce2", 8000, "#ffffff"],
  light: [-100, "#c9dbe6", 0, "#d4e6c6", 120, "#c6e0b0", 250, "#d6e2aa", 350, "#e6dba6", 600, "#dfc897", 1200, "#c9a982",
          2500, "#ad9c8d", 4000, "#cfd0d3", 5500, "#eeeff1", 8000, "#ffffff"],
};
const MAP_THEME = {
  dark: { bg: "#05070a", mask: "#000000", maskOp: 0.5, line: "#ffffff", outline: "#e8eaee", hover: "#5794f2", wind: "rgba(235,240,248,0.78)",
          hs: { "hillshade-shadow-color": "#000000", "hillshade-highlight-color": "#59667a", "hillshade-accent-color": "#11141a" },
          sky: { "sky-color": "#0b1020", "horizon-color": "#1b2333", "fog-color": "#0b0d12" } },
  light: { bg: "#e8ebef", mask: "#ffffff", maskOp: 0.55, line: "#1f2329", outline: "#24292e", hover: "#1f60c4", wind: "rgba(30,40,60,0.55)",
           hs: { "hillshade-shadow-color": "#4a4f57", "hillshade-highlight-color": "#ffffff", "hillshade-accent-color": "#7d828b" },
           sky: { "sky-color": "#bcd3ec", "horizon-color": "#e9eef3", "fog-color": "#f4f5f5" } },
};

function initMap() {
  const T = MAP_THEME[theme()];
  map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {
        "base-canvas": { type: "raster", tiles: [CANVAS_TILES[theme()]], tileSize: 256, maxzoom: 16, attribution: "Basemap &copy; Esri, HERE, Garmin, USGS" },
        "base-imagery": { type: "raster", tiles: [IMAGERY_TILES], tileSize: 256, maxzoom: 18, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics" },
        dem: { type: "raster-dem", encoding: "terrarium", tiles: [DEM_TILES], tileSize: 256, maxzoom: 12, attribution: "Terrain: AWS Terrain Tiles (SRTM, GMTED)" },
      },
      layers: [
        { id: "bg", type: "background", paint: { "background-color": T.bg } },
        { id: "base-canvas", type: "raster", source: "base-canvas" },
        { id: "base-imagery", type: "raster", source: "base-imagery", layout: { visibility: "none" }, paint: { "raster-saturation": -0.3, "raster-brightness-max": 0.8 } },
        { id: "relief", type: "color-relief", source: "dem", layout: { visibility: "none" },
          paint: { "color-relief-color": reliefExpr(), "color-relief-opacity": 1 } },
        { id: "hillshade", type: "hillshade", source: "dem",
          paint: { "hillshade-exaggeration": 0.35, "hillshade-illumination-direction": 315, ...T.hs } },
      ],
      sky: { ...T.sky, "sky-horizon-blend": 0.6, "horizon-fog-blend": 0.5, "fog-ground-blend": 0.4,
             "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 1, 7, 0] },
      projection: { type: "globe" },
    },
    center: [77.2, 28.6], zoom: 4.9, minZoom: 2.5, maxZoom: 13, maxPitch: 75,
    attributionControl: { compact: true, customAttribution: "Fires: NASA FIRMS | Aerosol: NASA GMAO GEOS-FP | Boundaries: Natural Earth (India view), geoBoundaries" },
    fadeDuration: 0,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: true }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 90 }), "bottom-left");
  // only the style is needed to add sources and layers; waiting for "load" would also wait for every first tile
  return new Promise((res) => (map.isStyleLoaded() ? res() : map.once("style.load", res)));
}

function reliefExpr(t = theme()) {
  return ["interpolate", ["linear"], ["elevation"], ...RELIEF[t]];
}

function addOverlayLayers() {
  const g = S.geo, T = MAP_THEME[theme()];
  map.addSource("countries", { type: "geojson", data: g.countries });
  map.addSource("states", { type: "geojson", data: g.states });
  map.addSource("districts", { type: "geojson", data: g.districts, promoteId: "id" });
  map.addSource("fires", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addSource("period-pts", { type: "geojson", data: { type: "FeatureCollection", features: [] } });

  // burn scars: NASA MODIS burned area (MCD64A1, 500 m), one XYZ tile set per season (scripts/burnscar.py), on the ground
  map.addSource("burnscar", { type: "raster", tiles: [new URL("data/burnscar/2021/", location.href).href + "{z}/{x}/{y}.png"], tileSize: 256, minzoom: 5, maxzoom: 11,
    bounds: [73.8, 27.6, 77.7, 32.6], attribution: "Burned area: NASA MODIS MCD64A1 via Microsoft Planetary Computer" });
  map.addLayer({ id: "burnscar", type: "raster", source: "burnscar", layout: { visibility: "none" },
    paint: { "raster-opacity": ["interpolate", ["linear"], ["zoom"], 5, 0.75, 9, 0.95], "raster-resampling": "nearest", "raster-fade-duration": 0 } });

  // Sentinel-2A night hot spots (ESA campaign, 9 and 12 Dec 2025; scripts/s2night.py): a one-off layer, off the timeline
  map.addSource("s2night", { type: "geojson", data: { type: "FeatureCollection", features: [] }, attribution: "Night hot spots: ESA Copernicus Sentinel-2 night campaign (Dec 2025)" });

  // dim everything outside the subcontinent
  map.addLayer({ id: "mask", type: "fill", source: "countries", filter: ["!", ["get", "sub"]], paint: { "fill-color": T.mask, "fill-opacity": T.maskOp } });
  map.addLayer({ id: "district-fill", type: "fill", source: "districts", layout: { visibility: "none" },
    paint: { "fill-color": choroExpr(), "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.95, 0.78] } });

  // smoke + wind canvases
  const b = S.smokeMeta ? S.smokeMeta.bounds : [64, 18, 94, 38];
  const coords = [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]];
  if (smoke.canvas) {
    map.addSource("smoke", { type: "canvas", canvas: smoke.canvas, coordinates: coords, animate: true });
    map.addLayer({ id: "smoke", type: "raster", source: "smoke", paint: { "raster-opacity": 0.92, "raster-fade-duration": 0, "raster-resampling": "linear" } });
  }
  if (wind.canvas) {
    map.addSource("wind", { type: "canvas", canvas: wind.canvas, coordinates: coords, animate: true });
    map.addLayer({ id: "wind", type: "raster", source: "wind", paint: { "raster-opacity": 0.55, "raster-fade-duration": 0 } });
  }

  map.addLayer({ id: "heat", type: "heatmap", source: "period-pts", layout: { visibility: "none" }, paint: {
    "heatmap-weight": ["interpolate", ["linear"], ["get", "frp"], 0, 0.3, 50, 1],
    "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 3, 0.5, 9, 2],
    "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 3, 4, 6, 10, 10, 24],
    "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"], 0, "rgba(0,0,0,0)", 0.1, "rgba(255,209,102,0.35)", 0.3, "#fca048", 0.55, "#f2683c", 0.8, "#c2185b", 1, "#fff3b0"],
    "heatmap-opacity": 0.85,
  } });

  map.addLayer({ id: "district-line", type: "line", source: "districts", minzoom: 5.2,
    paint: { "line-color": T.line, "line-opacity": ["interpolate", ["linear"], ["zoom"], 5.2, 0.07, 8, 0.24], "line-width": 0.6 } });
  map.addLayer({ id: "district-hover", type: "line", source: "districts",
    paint: { "line-color": T.hover, "line-width": 2, "line-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 1, 0] } });
  map.addLayer({ id: "state-line", type: "line", source: "states",
    paint: { "line-color": T.line, "line-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.2, 7, 0.42], "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.5, 8, 1.2] } });
  map.addLayer({ id: "outline", type: "line", source: "countries", filter: ["get", "sub"],
    paint: { "line-color": T.outline, "line-opacity": 0.75, "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.9, 8, 1.8] } });

  // fires: soft glow under a hot core; colour and fade by age within the 24 h window
  const ageColor = ["interpolate", ["linear"], ["get", "age"], 0, "#fff6c2", 2, "#ffd166", 8, "#ff8a2b", 24, "#c2185b"];
  const radius = (base) => ["interpolate", ["linear"], ["zoom"],
    3, ["*", base, ["interpolate", ["linear"], ["get", "frp"], 0, 0.8, 30, 1.4, 150, 2.4]],
    9, ["*", base * 3.2, ["interpolate", ["linear"], ["get", "frp"], 0, 0.8, 30, 1.4, 150, 2.4]]];
  const fade = ["interpolate", ["linear"], ["get", "age"], 0, 1, 6, 0.75, 24, 0.18];
  map.addLayer({ id: "fires-glow", type: "circle", source: "fires", paint: {
    "circle-color": ageColor, "circle-radius": radius(2.6), "circle-blur": 1, "circle-opacity": ["*", 0.55, fade] } });
  map.addLayer({ id: "fires-core", type: "circle", source: "fires", paint: {
    "circle-color": ageColor, "circle-radius": radius(0.9), "circle-opacity": fade,
    "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 6, 0, 9, 0.6], "circle-stroke-color": "rgba(0,0,0,0.5)" } });

  const s2c = ["match", ["get", "class"], "industrial", "#e8e8e8", "built-up", "#b68cff", "#ff7a1a"];
  const s2r = (k) => ["interpolate", ["linear"], ["zoom"], 5, ["*", k, ["interpolate", ["linear"], ["get", "l12_sum"], 0, 2, 20, 3.2, 100, 5]],
    11, ["*", k * 2.6, ["interpolate", ["linear"], ["get", "l12_sum"], 0, 2, 20, 3.2, 100, 5]]];
  map.addLayer({ id: "s2night-glow", type: "circle", source: "s2night", layout: { visibility: "none" },
    paint: { "circle-color": s2c, "circle-radius": s2r(2.2), "circle-blur": 1, "circle-opacity": 0.45 } });
  map.addLayer({ id: "s2night", type: "circle", source: "s2night", layout: { visibility: "none" },
    paint: { "circle-color": s2c, "circle-radius": s2r(0.8), "circle-stroke-width": 1.4, "circle-stroke-color": "#ffffff" } });

  // cyclone tracks of the selected year (scripts/build_cyclones.py): IBTrACS best track, the current storm from IMD advisories
  map.addSource("cyclones", { type: "geojson", data: { type: "FeatureCollection", features: [] }, attribution: "Cyclones: IMD via IBTrACS (NOAA NCEI), IMD RSMC New Delhi" });
  const cyColor = theme() === "dark" ? "#dfe3ea" : "#2b2f36";
  map.addLayer({ id: "cyclone-line", type: "line", source: "cyclones", filter: ["all", ["==", ["geometry-type"], "LineString"], ["!=", ["get", "kind"], "forecast"]],
    layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": cyColor, "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1.2, 8, 2.4], "line-dasharray": [2, 1.6], "line-opacity": 0.85 } });
  map.addLayer({ id: "cyclone-pt", type: "circle", source: "cyclones", filter: ["all", ["==", ["geometry-type"], "Point"], ["in", ["get", "feature"], ["literal", ["observed", "landfall"]]]],
    paint: { "circle-radius": ["match", ["get", "feature"], "landfall", 5, 2.6], "circle-color": ["match", ["get", "feature"], "landfall", "#ff5a4e", cyColor], "circle-stroke-width": 1, "circle-stroke-color": "rgba(0,0,0,0.6)" } });
  updateCyclones();

  map.addSource("labels", { type: "raster", tiles: [REF_TILES[theme()]], tileSize: 256, maxzoom: 16 });
  map.addLayer({ id: "labels", type: "raster", source: "labels", paint: { "raster-opacity": 0.9 } });
  if ($("#l-s2night").checked) setS2night(true);   // ticked before the map was ready

  bindMapEvents();
}

function choroExpr() {
  const e = ["step", ["coalesce", ["feature-state", "n"], 0], "rgba(0,0,0,0)"];
  CHORO_BREAKS.forEach((b, i) => e.push(b, CHORO[i]));
  return e;
}

const press = (sel, v) => document.querySelectorAll(`${sel} button`).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === v)));

function setBasemap(k) {
  S.base = k;
  press("#seg-base", k);
  const vis = (id, on) => map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  vis("base-canvas", k === "canvas");
  vis("base-imagery", k === "imagery");
  vis("relief", k === "relief");
  map.setPaintProperty("hillshade", "hillshade-exaggeration", k === "relief" ? 0.55 : k === "imagery" ? 0.15 : 0.35);
  const src = map.getSource("labels");
  if (src && src.setTiles) src.setTiles([k === "imagery" ? REF_TILES.imagery : REF_TILES[theme()]]);
}

// The 3D view looks north-north-east across the Punjab and Haryana plains at the Himalayan wall, with the
// relief exaggerated so the flat basin and the mountains that trap its smoke read at a glance.
function setProjection(v) {
  S.proj = v;
  press("#seg-proj", v);
  if (v === "terrain3d") {
    map.setProjection({ type: "mercator" });
    if (!map.getSource("dem3d")) map.addSource("dem3d", { type: "raster-dem", encoding: "terrarium", tiles: [DEM_TILES], tileSize: 256, maxzoom: 12 });
    map.setTerrain({ source: "dem3d", exaggeration: 4.5 });
    if (S.base === "canvas") setBasemap("relief");
    map.easeTo({ center: [76.4, 29.2], zoom: 5.7, pitch: 68, bearing: 18, duration: 1800 });
  } else {
    map.setTerrain(null);
    map.setProjection({ type: v });
    map.easeTo({ pitch: 0, bearing: 0, duration: 700 });
  }
}

function applyMapTheme() {
  if (!map || !map.getLayer("bg")) return;
  const t = theme(), T = MAP_THEME[t];
  map.setPaintProperty("bg", "background-color", T.bg);
  const base = map.getSource("base-canvas"); if (base && base.setTiles) base.setTiles([CANVAS_TILES[t]]);
  map.setPaintProperty("relief", "color-relief-color", reliefExpr(t));
  for (const [k, v] of Object.entries(T.hs)) map.setPaintProperty("hillshade", k, v);
  map.setSky({ ...T.sky, "sky-horizon-blend": 0.6, "horizon-fog-blend": 0.5, "fog-ground-blend": 0.4, "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 1, 7, 0] });
  if (map.getLayer("mask")) {
    map.setPaintProperty("mask", "fill-color", T.mask); map.setPaintProperty("mask", "fill-opacity", T.maskOp);
    map.setPaintProperty("district-line", "line-color", T.line);
    map.setPaintProperty("state-line", "line-color", T.line);
    map.setPaintProperty("outline", "line-color", T.outline);
    map.setPaintProperty("district-hover", "line-color", T.hover);
  }
  setBasemap(S.base);
}

function bindLayerToggles() {
  const btn = $("#btn-layers"), menu = $("#layers-menu");
  const close = () => { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); };
  btn.onclick = (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; btn.setAttribute("aria-expanded", String(!menu.hidden)); };
  menu.querySelector(".menu-close").onclick = close;
  document.addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target)) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  const vis = (ids, on) => ids.forEach((id) => map.getLayer(id) && map.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
  $("#l-hillshade").onchange = (e) => vis(["hillshade"], e.target.checked);
  $("#l-burnscar").onchange = () => updateBurnscar();
  $("#l-s2night").onchange = (e) => setS2night(e.target.checked);
  $("#l-cyclones").onchange = () => updateCyclones();
  $("#l-wind").onchange = (e) => { S.showWind = e.target.checked; vis(["wind"], e.target.checked); };
  $("#l-districts").onchange = (e) => vis(["district-line"], e.target.checked);
  $("#l-labels").onchange = (e) => vis(["labels"], e.target.checked);
  $("#l-mask").onchange = (e) => vis(["mask"], e.target.checked);
}

// ---------------------------------------------------------------- smoke renderer (WebGL, mercator-warped, bicubic, time-interpolated)
const VS = `attribute vec2 p; varying vec2 uv; void main(){ uv = p * 0.5 + 0.5; gl_Position = vec4(p, 0.0, 1.0); }`;
const FS = `precision highp float;
varying vec2 uv;
uniform sampler2D atlas; uniform sampler2D ramp;
uniform vec2 atlasSize; uniform vec2 tile; uniform float cols;
uniform float fA; uniform float fB; uniform float mixT;
uniform vec4 bounds;   // lon0 lat0 lon1 lat1 (outer edges)
uniform vec2 merc;     // mercY(lat0), mercY(lat1)
uniform float aodMax; uniform float rampMax;
float px(float f, vec2 c) {           // nearest texel of frame f at integer grid coords c (row 0 = north)
  c = clamp(c, vec2(0.0), tile - 1.0);
  float r = floor(f / cols), k = f - r * cols;
  vec2 a = vec2(k * tile.x + c.x + 0.5, r * tile.y + c.y + 0.5) / atlasSize;
  float v = texture2D(atlas, a).r;
  return v * v * aodMax;
}
vec4 cubic(float v) {                 // cubic B-spline weights
  vec4 n = vec4(1.0, 2.0, 3.0, 4.0) - v; vec4 s = n * n * n;
  float x = s.x, y = s.y - 4.0 * s.x, z = s.z - 4.0 * s.y + 6.0 * s.x, w = 6.0 - x - y - z;
  return vec4(x, y, z, w) / 6.0;
}
float sampleF(float f, vec2 g) {
  vec2 i = floor(g - 0.5), fr = g - 0.5 - i;
  vec4 wx = cubic(fr.x), wy = cubic(fr.y);
  float acc = 0.0;
  for (int y = 0; y < 4; y++) {
    float row = 0.0;
    for (int x = 0; x < 4; x++) row += px(f, i + vec2(float(x) - 1.0, float(y) - 1.0)) * wx[x];
    acc += row * wy[y];
  }
  return acc;
}
void main() {
  float lon = mix(bounds.x, bounds.z, uv.x);
  float m = mix(merc.x, merc.y, uv.y);
  float lat = degrees(2.0 * atan(exp(m)) - 1.5707963);
  vec2 g = vec2((lon - bounds.x) / (bounds.z - bounds.x) * tile.x, (bounds.w - lat) / (bounds.w - bounds.y) * tile.y);
  float aod = mix(sampleF(fA, g), sampleF(fB, g), mixT);
  vec4 c = texture2D(ramp, vec2(sqrt(clamp(aod / rampMax, 0.0, 1.0)), 0.5));
  gl_FragColor = vec4(c.rgb * c.a, c.a);
}`;

function rampPixels(n = 256) {
  const out = new Uint8Array(n * 4), max = SMOKE_STOPS[SMOKE_STOPS.length - 1][0];
  for (let i = 0; i < n; i++) {
    const v = (i / (n - 1)) ** 2 * max;
    let k = 0; while (k < SMOKE_STOPS.length - 2 && SMOKE_STOPS[k + 1][0] < v) k++;
    const a = SMOKE_STOPS[k], b = SMOKE_STOPS[k + 1], t = clamp((v - a[0]) / (b[0] - a[0]), 0, 1);
    for (let c = 0; c < 4; c++) out[i * 4 + c] = Math.round((a[c + 1] + (b[c + 1] - a[c + 1]) * t) * (c === 3 ? 255 : 1));
  }
  return out;
}

async function initSmoke() {
  try { S.smokeMeta = await getJSON("data/smoke/smoke_meta.json"); } catch { S.smokeMeta = null; return; }
  const M = S.smokeMeta;
  const W = 1200, H = Math.round(W * (mercY(M.bounds[3]) - mercY(M.bounds[1])) / ((M.bounds[2] - M.bounds[0]) * Math.PI / 180));
  const cv = document.createElement("canvas"); cv.width = W; cv.height = H;
  const gl = cv.getContext("webgl", { premultipliedAlpha: true, preserveDrawingBuffer: true, antialias: false });
  if (!gl) return;
  const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
  const prog = gl.createProgram();
  gl.attachShader(prog, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(prog);
  gl.useProgram(prog);
  const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const mkTex = (w, h, data) => {
    const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    if (data) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, data);
    const lin = !!data;
    for (const [p, v] of [[gl.TEXTURE_MIN_FILTER, lin ? gl.LINEAR : gl.NEAREST], [gl.TEXTURE_MAG_FILTER, lin ? gl.LINEAR : gl.NEAREST], [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, p, v);
    return t;
  };
  smoke.tex.smoke = mkTex(); smoke.tex.total = mkTex();
  smoke.tex.ramp = mkTex(256, 1, rampPixels());
  const u = (n) => gl.getUniformLocation(prog, n);
  smoke.u = Object.fromEntries(["atlas", "ramp", "atlasSize", "tile", "cols", "fA", "fB", "mixT", "bounds", "merc", "aodMax", "rampMax"].map((n) => [n, u(n)]));
  gl.uniform1i(smoke.u.atlas, 0); gl.uniform1i(smoke.u.ramp, 1);
  gl.uniform4f(smoke.u.bounds, ...M.bounds); gl.uniform2f(smoke.u.merc, mercY(M.bounds[1]), mercY(M.bounds[3]));
  gl.uniform1f(smoke.u.rampMax, SMOKE_STOPS[SMOKE_STOPS.length - 1][0]);
  gl.viewport(0, 0, W, H);
  Object.assign(smoke, { gl, canvas: cv, prog });
  wind.canvas = document.createElement("canvas"); wind.canvas.width = W; wind.canvas.height = H;
  wind.ctx = wind.canvas.getContext("2d");
  wind.W = W; wind.H = H;
  await loadSmokeData(M, "");
  for (let i = 0; i < 2600; i++) wind.particles.push(newParticle(true));
  wind.ready = true;
}

// (Re)load a GEOS-FP run into the existing textures: used at start-up and whenever a newer run is published.
async function loadSmokeData(M, bust) {
  const q = bust ? `?v=${encodeURIComponent(bust)}` : "";
  const [smokeImg, totalImg, windImg] = await Promise.all([
    loadImage(`data/smoke/smoke_atlas.png${q}`), loadImage(`data/smoke/total_aod_atlas.png${q}`), loadImage(`data/smoke/wind_atlas.png${q}`)]);
  const gl = smoke.gl;
  gl.useProgram(smoke.prog);
  for (const [tex, img] of [[smoke.tex.smoke, smokeImg], [smoke.tex.total, totalImg]]) {
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, gl.LUMINANCE, gl.UNSIGNED_BYTE, img);
  }
  smoke.atlasSize = [smokeImg.width, smokeImg.height];
  gl.uniform2f(smoke.u.atlasSize, ...smoke.atlasSize); gl.uniform2f(smoke.u.tile, M.tile_w, M.tile_h); gl.uniform1f(smoke.u.cols, M.cols);
  gl.uniform1f(smoke.u.aodMax, M.aod_max);
  S.smokeMeta = M;
  S.smokeTimes = M.times.map((s) => Date.parse(s.replace("Z", ":00Z")));
  // decode the wind atlas into Float32 frames for particle advection
  const wc = document.createElement("canvas"); wc.width = windImg.width; wc.height = windImg.height;
  const wctx = wc.getContext("2d"); wctx.drawImage(windImg, 0, 0);
  const wd = wctx.getImageData(0, 0, wc.width, wc.height).data;
  const tw = M.tile_w, th = M.tile_h, frames = [];
  for (let f = 0; f < M.times.length; f++) {
    const r = Math.floor(f / M.cols), k = f % M.cols, U = new Float32Array(tw * th), V = new Float32Array(tw * th);
    for (let y = 0; y < th; y++) for (let x = 0; x < tw; x++) {
      const o = ((r * th + y) * wc.width + k * tw + x) * 4, i = y * tw + x;
      U[i] = (wd[o] / 255) * 2 * M.wind_max - M.wind_max; V[i] = (wd[o + 1] / 255) * 2 * M.wind_max - M.wind_max;
    }
    frames.push([U, V]);
  }
  wind.field = frames;
  smoke.ready = true;
  smoke.lastKey = "";
}

function smokeFrameAt(t) {
  const T = S.smokeTimes; if (!T || t < T[0] - 3 * HOUR || t > T[T.length - 1] + 3 * HOUR) return null;
  const f = clamp((t - T[0]) / (3 * HOUR), 0, T.length - 1);
  return { a: Math.floor(f), b: Math.min(T.length - 1, Math.floor(f) + 1), m: f - Math.floor(f) };
}

function drawSmoke() {
  if (!smoke.ready) return;
  const gl = smoke.gl, fr = S.smokeVar === "none" ? null : smokeFrameAt(S.t);
  const key = fr ? `${S.smokeVar}|${fr.a}|${fr.m.toFixed(3)}` : "off";
  if (key === smoke.lastKey) return;
  smoke.lastKey = key;
  gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
  if (!fr) return;
  gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, smoke.tex[S.smokeVar]);
  gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, smoke.tex.ramp);
  gl.uniform1f(smoke.u.fA, fr.a); gl.uniform1f(smoke.u.fB, fr.b); gl.uniform1f(smoke.u.mixT, fr.m);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
}

// ---------------------------------------------------------------- wind particles
function newParticle(randomAge) {
  const b = S.smokeMeta.bounds;
  return { lon: b[0] + Math.random() * (b[2] - b[0]), lat: b[1] + Math.random() * (b[3] - b[1]), age: randomAge ? Math.floor(Math.random() * 80) : 0, max: 50 + Math.floor(Math.random() * 50) };
}
function windAt(lon, lat, fr) {
  const M = S.smokeMeta, b = M.bounds, tw = M.tile_w, th = M.tile_h;
  const gx = ((lon - b[0]) / (b[2] - b[0])) * tw - 0.5, gy = ((b[3] - lat) / (b[3] - b[1])) * th - 0.5;
  const x0 = clamp(Math.floor(gx), 0, tw - 2), y0 = clamp(Math.floor(gy), 0, th - 2), fx = clamp(gx - x0, 0, 1), fy = clamp(gy - y0, 0, 1);
  const bil = (A) => { const i = y0 * tw + x0; return (A[i] * (1 - fx) + A[i + 1] * fx) * (1 - fy) + (A[i + tw] * (1 - fx) + A[i + tw + 1] * fx) * fy; };
  const FA = wind.field[fr.a], FB = wind.field[fr.b];
  return [bil(FA[0]) * (1 - fr.m) + bil(FB[0]) * fr.m, bil(FA[1]) * (1 - fr.m) + bil(FB[1]) * fr.m];
}
function stepWind() {
  if (!wind.ready) return;
  const ctx = wind.ctx, M = S.smokeMeta, b = M.bounds, fr = S.showWind ? smokeFrameAt(S.t) : null;
  ctx.globalCompositeOperation = "destination-in"; ctx.fillStyle = "rgba(0,0,0,0.9)"; ctx.fillRect(0, 0, wind.W, wind.H);
  ctx.globalCompositeOperation = "source-over";
  if (!fr) { ctx.clearRect(0, 0, wind.W, wind.H); return; }
  const my0 = mercY(b[1]), my1 = mercY(b[3]);
  const toXY = (lon, lat) => [((lon - b[0]) / (b[2] - b[0])) * wind.W, ((my1 - mercY(lat)) / (my1 - my0)) * wind.H];
  ctx.lineWidth = 1.1; ctx.strokeStyle = MAP_THEME[theme()].wind; ctx.beginPath();
  const k = 0.0045; // degrees per (m/s) per animation frame, a visual scale only
  for (const p of wind.particles) {
    const [u, v] = windAt(p.lon, p.lat, fr);
    const [x0, y0] = toXY(p.lon, p.lat);
    p.lon += (u * k) / Math.cos((p.lat * Math.PI) / 180); p.lat += v * k; p.age++;
    if (p.age > p.max || p.lon < b[0] || p.lon > b[2] || p.lat < b[1] || p.lat > b[3] || Math.hypot(u, v) < 0.3) { Object.assign(p, newParticle(false)); continue; }
    const [x1, y1] = toXY(p.lon, p.lat);
    ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
  }
  ctx.stroke();
}

// ---------------------------------------------------------------- fire layers
let lastFireKey = "", lastFireUpdate = 0;
function updateFires(force) {
  if (S.fireMode !== "points") return;
  const now = performance.now(), key = `${S.year}|${Math.floor(S.t / (15 * 60e3))}|${[...S.sensors].join()}`;
  if (!force && (key === lastFireKey || now - lastFireUpdate < 90)) return;
  lastFireKey = key; lastFireUpdate = now;
  const pts = pointsBetween(S.t - 24 * HOUR, S.t + 1);
  map.getSource("fires").setData({ type: "FeatureCollection", features: pts.map((p) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
    properties: { age: (S.t - p.t) / HOUR, frp: p.frp, s: p.s, c: p.c, t: p.t, d: p.d } })) });
  S.windowCount = pts.length;
  updateBadge();
}

function updatePeriodLayers() {
  const pts = pointsBetween(S.d1, periodEnd());
  S.periodPts = pts;
  if (S.fireMode === "heat") {
    map.getSource("period-pts").setData({ type: "FeatureCollection", features: pts.map((p) => ({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] }, properties: { frp: p.frp } })) });
  }
  // district counts for choropleth + table
  const nd = S.summary.districts.length;
  const cnt = new Float64Array(nd), frp = new Float64Array(nd), days = Math.round((periodEnd() - S.d1) / DAY);
  const spark = Array.from({ length: nd }, () => null);
  for (const p of pts) {
    if (p.d < 0) continue;
    cnt[p.d]++; frp[p.d] += p.frp;
    (spark[p.d] ||= new Float64Array(days))[Math.floor((p.t - S.d1) / DAY)]++;
  }
  S.distCount = cnt; S.distFrp = frp; S.distSpark = spark;
  if (map.getSource("districts")) for (let i = 0; i < nd; i++) map.setFeatureState({ source: "districts", id: i }, { n: cnt[i] });
  // sensor chip counts
  const all = new Array(SENSOR_SHORT.length).fill(0);
  if (S.points) { const P = S.points; for (let i = lowerBound(P.times, S.d1), j = lowerBound(P.times, periodEnd()); i < j; i++) all[P.list[i].s]++; }
  S.sensorPeriodCount = all;
  renderChips();
}

function setFireMode(m) {
  S.fireMode = m;
  const vis = (id, on) => map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
  vis("fires-glow", m === "points"); vis("fires-core", m === "points");
  vis("district-fill", m === "districts"); vis("heat", m === "heat");
  if (m === "points") updateFires(true);
  updatePeriodLayers();
  renderFireLegend();
  updateBadge();
}

// ---------------------------------------------------------------- map interaction
let hoverId = null, popup = null;
function bindMapEvents() {
  const tip = $("#tooltip");
  map.on("mousemove", (e) => {
    const f = map.queryRenderedFeatures(e.point, { layers: ["s2night", "fires-core", "district-hover"].filter((l) => map.getLayer(l)) });
    const fire = f.find((x) => x.layer.id === "fires-core");
    const s2 = f.find((x) => x.layer.id === "s2night");
    const dist = map.queryRenderedFeatures(e.point, { layers: ["district-hover"] })[0];
    const id = dist ? dist.id : null;
    if (id !== hoverId) {
      if (hoverId !== null) map.setFeatureState({ source: "districts", id: hoverId }, { hover: false });
      if (id !== null) map.setFeatureState({ source: "districts", id }, { hover: true });
      hoverId = id;
    }
    map.getCanvas().style.cursor = fire ? "pointer" : "";
    if (s2) { tip.innerHTML = s2nightTip(s2.properties); }
    else if (fire) { tip.innerHTML = fireTip(fire.properties); }
    else if (dist) { tip.innerHTML = districtTip(id); }
    else { tip.hidden = true; return; }
    tip.hidden = false;
    placeTip(tip, e.originalEvent.clientX, e.originalEvent.clientY);
  });
  map.on("mouseout", () => { tip.hidden = true; });
  map.on("click", "fires-core", (e) => {
    const p = e.features[0].properties;
    if (popup) popup.remove();
    popup = new maplibregl.Popup({ closeButton: true, maxWidth: "280px" }).setLngLat(e.lngLat).setHTML(fireTip(p, true)).addTo(map);
  });
}
function placeTip(tip, x, y) {
  const r = tip.getBoundingClientRect(), pad = 14;
  let left = x + pad, top = y + pad;
  if (left + r.width > innerWidth - 8) left = x - r.width - pad;
  if (top + r.height > innerHeight - 8) top = y - r.height - pad;
  tip.style.left = `${Math.max(8, left)}px`; tip.style.top = `${Math.max(8, top)}px`;
}
function fireTip(p, full) {
  const d = S.summary.districts[p.d];
  const conf = ["low", "nominal", "high"][p.c];
  return `<div class="tt-title">${SENSOR_SHORT[p.s]} detection</div>
    <div class="tt-row"><span>Time</span><span>${fmtDay(+p.t)} ${fmtClock(+p.t)}</span></div>
    ${+p.s === 6 ? `<div class="tt-row"><span>Listed in</span><span>CREAMS bulletin</span></div>` : `<div class="tt-row"><span>FRP</span><span>${(+p.frp).toFixed(1)} MW</span></div>
    <div class="tt-row"><span>Confidence</span><span>${conf}</span></div>`}
    ${d ? `<div class="tt-row"><span>District</span><span>${d.name}, ${d.state}</span></div>` : ""}
    ${full ? `<div class="tt-row"><span>Age at view time</span><span>${(+p.age).toFixed(1)} h</span></div>` : ""}`;
}
const S2N_CLASS = { open: "open land: likely an open fire", "built-up": "built-up: factory, furnace or waste fire", industrial: "known industrial source (FIRMS static)" };
function s2nightTip(p) {
  const m = (+p.firms_km) <= 1 && p.firms_sat !== "null" && p.firms_sat;
  return `<div class="tt-title">Sentinel-2 night hot spot</div>
    <div class="tt-row"><span>Seen</span><span>${p.date === "2025-12-09" ? "9 Dec 2025, 22:32" : "12 Dec 2025, 22:42"} IST</span></div>
    <div class="tt-row"><span>Setting</span><span>${S2N_CLASS[p.class] || p.class}</span></div>
    <div class="tt-row"><span>Land cover</span><span>${p.landcover}, ${Math.round(100 * p.built_share)}% built</span></div>
    <div class="tt-row"><span>Size</span><span>${p.npix} px of 20 m</span></div>
    <div class="tt-row"><span>Peak radiance B12 / B11</span><span>${(+p.l12_peak).toFixed(1)} / ${(+p.l11_peak).toFixed(1)}</span></div>
    ${p.t_k && p.t_k !== "null" ? `<div class="tt-row"><span>Fitted temperature</span><span>~${p.t_k} K</span></div>` : ""}
    <div class="tt-row"><span>FIRMS within 1 km (±4 days)</span><span>${m ? `${p.firms_sat.replace("VIIRS:", "VIIRS ").replace("MODIS:", "MODIS ")}, ${(+p.firms_dt_h) > 0 ? "+" : ""}${p.firms_dt_h} h` : "none"}</span></div>
    ${p.district && p.district !== "null" ? `<div class="tt-row"><span>District</span><span>${p.district}, ${p.state}${p.country === "Pakistan" ? " (Pak.)" : ""}</span></div>` : ""}`;
}
function districtTip(id) {
  const d = S.summary.districts[id]; if (!d) return "";
  const n = S.distCount ? S.distCount[id] : 0, f = S.distFrp ? S.distFrp[id] : 0;
  return `<div class="tt-title">${d.name}</div><div class="tt-row"><span>State</span><span>${d.state}</span></div>
    <div class="tt-row"><span>Detections ${fmtDM(S.d1)}-${fmtDM(S.d2)}</span><span>${fmt(n)}</span></div>
    <div class="tt-row"><span>Total FRP</span><span>${fmt(Math.round(f))} MW</span></div>`;
}

// ---------------------------------------------------------------- UI: chips, badge, legends
function renderChips() {
  const el = $("#sensor-chips"), c = S.sensorPeriodCount || [];
  el.innerHTML = SENSOR_SHORT.map((n, i) => c[i] ? `<span class="chip ${S.sensors.has(i) ? "" : "off"}" data-s="${i}" title="Toggle ${n}"><span class="dot" style="background:${SENSOR_COLORS[i]}"></span>${n}<span class="cnt">${fmtK(c[i])}</span></span>` : "").join("");
  el.querySelectorAll(".chip").forEach((ch) => ch.onclick = () => {
    const s = +ch.dataset.s; S.sensors.has(s) ? S.sensors.delete(s) : S.sensors.add(s);
    refreshAll();
  });
}
function updateBadge() {
  const fr = smokeFrameAt(S.t);
  let mode = "";
  if (fr && S.smokeMeta && S.smokeVar !== "none") mode = S.smokeMeta.forecast[fr.a] ? `<span class="mode fc">smoke forecast</span>` : `<span class="mode">smoke analysis</span>`;
  const plural = (n) => `<b>${fmt(n)}</b> detection${n === 1 ? "" : "s"}`;
  const fires = S.fireMode === "points" ? ` | ${plural(S.windowCount || 0)} in previous 24 h`
    : S.fireMode !== "none" ? ` | ${plural((S.periodPts || []).length)} ${fmtDM(S.d1)}-${fmtDM(S.d2)}` : "";
  $("#map-badge").innerHTML = `<b>${fmtDay(S.t)}, ${fmtClock(S.t)}</b>${fires}${mode}`;
}
// Sentinel-2 night hot spots: loaded on first use; the view moves to the two scenes if they are off screen
function setS2night(on) {
  if (!map || !map.getSource("s2night")) return;
  ["s2night-glow", "s2night"].forEach((id) => map.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
  $("#legend-s2n").hidden = !on;
  if (!on) return;
  if (!S.s2night) getJSON("data/s2night/s2night.geojson").then((g) => {
    S.s2night = g; map.getSource("s2night").setData(g);
    const n = (c) => g.features.filter((f) => f.properties.class === c).length;
    $("#legend-s2n-note").textContent = `${g.features.length} hot spots seen in both SWIR bands on 9 and 12 Dec 2025 (22:32 / 22:42 IST): ` +
      `${n("open")} on open land, ${n("built-up")} built-up, ${n("industrial")} known industrial. Heat sources, not counted as crop fires; most matched fires were in the hills.`;
  }).catch(() => { $("#legend-s2n-note").textContent = "Night hot spots are not available."; });
  const b = map.getBounds(), box = [72.6, 29.2, 78.5, 32.3];
  if (b.getWest() > box[2] || b.getEast() < box[0] || b.getSouth() > box[3] || b.getNorth() < box[1] || map.getZoom() < 5.5)
    map.easeTo({ center: [75.6, 30.8], zoom: 6.3, duration: 1200 });   // fitBounds throws on the globe in small viewports
}

// cyclone tracks: loaded once, filtered to the selected year
function updateCyclones() {
  if (!map || !map.getSource("cyclones")) return;
  const on = $("#l-cyclones").checked;
  ["cyclone-line", "cyclone-pt"].forEach((id) => map.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
  if (!on) return;
  if (!S.cyclones) { S.cyclones = getJSON("data/geo/cyclone_tracks.geojson").catch(() => null); }
  S.cyclones.then((g) => {
    if (!g || !map.getSource("cyclones")) return;
    map.getSource("cyclones").setData({ type: "FeatureCollection", features: g.features.filter((f) => f.properties.season === S.year) });
  });
}

// burn scars exist per paddy season; show the selected season's tiles when they are built
function updateBurnscar() {
  if (!map || !map.getSource("burnscar")) return;
  const B = S.burn && S.burn[S.year], on = !!B && S.season === "rice" && $("#l-burnscar").checked;
  if (B) map.getSource("burnscar").setTiles([new URL(`data/burnscar/${S.year}/`, location.href).href + "{z}/{x}/{y}.png"]);
  map.setLayoutProperty("burnscar", "visibility", on ? "visible" : "none");
  const want = $("#l-burnscar").checked && S.season === "rice" && !!B;   // no legend for a season NASA has not published yet
  $("#legend-burn").hidden = !want;
  if (!want) return;
  if (B) {
    const pb = B.states.Punjab.burned_ha, hb = B.states.Haryana.burned_ha;
    $("#legend-burn-title").textContent = `Burned area ${S.year}, MODIS 500 m`;
    $("#legend-burn-note").textContent = `Coloured by burn date. Punjab ${fmt(Math.round(pb / 1000))}k ha, Haryana ${fmt(Math.round(hb / 1000))}k ha` +
      (B.missing_months && B.missing_months.length ? `; incomplete, no data for ${B.missing_months.map((m) => MONTHS[+m.slice(5) - 1]).join(", ")}.` : ".") +
      " Coarse: misses many small fields.";
  } else {
    $("#legend-burn-title").textContent = `Burned area ${S.year}, MODIS 500 m`;
    $("#legend-burn-note").textContent = "NASA publishes the monthly burned-area map about two months after the fact; this season's will appear then.";
  }
}

function renderSmokeLegend() {
  const show = S.smokeVar !== "none" && S.smokeMeta;
  $("#legend-smoke").hidden = !show;
  if (!show) return;
  const cv = $("#smoke-ramp"), ctx = cv.getContext("2d"), px = rampPixels(256);
  const img = ctx.createImageData(256, 10);
  for (let x = 0; x < 256; x++) for (let y = 0; y < 10; y++) {
    const o = (y * 256 + x) * 4, a = px[x * 4 + 3] / 255, bg = 30;
    for (let c = 0; c < 3; c++) img.data[o + c] = Math.round(px[x * 4 + c] * a + bg * (1 - a));
    img.data[o + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  const max = SMOKE_STOPS[SMOKE_STOPS.length - 1][0];
  $("#smoke-ticks").innerHTML = SMOKE_TICKS.map((v) => `<span style="left:${Math.sqrt(v / max) * 100}%">${v}</span>`).join("");
  $("#legend-smoke-title").textContent = S.smokeVar === "smoke" ? "Smoke AOD 550 nm (OC + BC + brown carbon)" : "Total aerosol optical depth, 550 nm";
  const inRange = smokeFrameAt(S.t);
  $("#legend-smoke-note").textContent = inRange ? "NASA GMAO GEOS-FP, 3-hourly, time-interpolated. Lines: 10 m wind." : `GEOS-FP fields loaded for ${fmtDay(S.smokeTimes[0])} - ${fmtDay(S.smokeTimes.at(-1))} only.`;
}
function renderFireLegend() {
  const body = $("#legend-fire-body"), title = $("#legend-fire-title");
  $("#legend-fire").hidden = S.fireMode === "none";
  if (S.fireMode === "points") {
    title.textContent = "Fire detections, previous 24 h";
    body.innerHTML = `<div class="fire-scale">${[[5, 4], [30, 6], [150, 9]].map(([f, r]) => `<div class="fs"><div class="fd" style="width:${r * 2}px;height:${r * 2}px"></div>${f} MW</div>`).join("")}
      <div class="fs" style="flex:1;align-items:stretch"><div class="choro-ramp" style="background:linear-gradient(90deg,#fff6c2,#ffd166,#ff8a2b,#c2185b)"></div><div style="display:flex;justify-content:space-between"><span>new</span><span>24 h old</span></div></div></div>`;
  } else if (S.fireMode === "districts") {
    title.textContent = `Detections per district, ${fmtDM(S.d1)}-${fmtDM(S.d2)}`;
    body.innerHTML = `<div style="display:flex">${CHORO.map((c) => `<div style="flex:1;height:10px;background:${c}"></div>`).join("")}</div>
      <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-3);margin-top:3px">${CHORO_BREAKS.map((b) => `<span>${b >= 1000 ? b / 1000 + "k" : b}</span>`).join("")}</div>`;
  } else if (S.fireMode === "heat") {
    title.textContent = `Detection density, ${fmtDM(S.d1)}-${fmtDM(S.d2)}`;
    body.innerHTML = `<div class="choro-ramp" style="background:linear-gradient(90deg,rgba(255,209,102,0.35),#fca048,#f2683c,#c2185b,#fff3b0)"></div><div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-3);margin-top:3px"><span>sparse</span><span>dense (FRP weighted)</span></div>`;
  }
}

// ---------------------------------------------------------------- charts
const chartW = (el) => Math.max(240, el.clientWidth);
const M_ = { top: 8, right: 8, bottom: 20, left: 40 };

function chartTip(evt, html) { const tip = $("#tooltip"); tip.innerHTML = html; tip.hidden = false; placeTip(tip, evt.clientX, evt.clientY); }
function hideTip() { $("#tooltip").hidden = true; }

function renderStateLegend() {
  $("#legend-states").innerHTML = GROUPS.map((g) => `<span class="li ${S.hiddenGroups.has(g) ? "off" : ""}" data-g="${g}"><span class="sw" style="background:${GROUP_COLORS[g]}"></span>${g}</span>`).join("");
  document.querySelectorAll("#legend-states .li").forEach((el) => el.onclick = () => {
    const g = el.dataset.g; S.hiddenGroups.has(g) ? S.hiddenGroups.delete(g) : S.hiddenGroups.add(g);
    renderStateLegend(); renderDaily(); renderHist();
  });
}

function renderDaily() {
  const el = $("#chart-daily"), w = chartW(el), h = 150;
  const series = dailyByGroup(S.year, [...S.sensors], S.metric, S.d1, S.d2);
  const nd = series[0].length, days = d3.range(nd).map((k) => S.d1 + k * DAY);
  const keys = GROUPS.filter((g) => !S.hiddenGroups.has(g));
  const rows = days.map((d, k) => Object.fromEntries([["t", d], ...GROUPS.map((g, gi) => [g, series[gi][k]])]));
  const stack = d3.stack().keys(keys)(rows);
  const x = d3.scaleUtc().domain([S.d1, S.d2]).range([M_.left, w - M_.right]);
  const ymax = d3.max(stack.length ? stack.at(-1) : [], (d) => d[1]) || 0;
  const y = d3.scaleLinear().domain([0, Math.max(ymax, 4)]).nice(4).range([h - M_.bottom, M_.top]);
  const unit = S.metric === "n" ? "detections" : "MW (sum of FRP)";
  $("#daily-sub").textContent = `${S.sensors.size} source${S.sensors.size === 1 ? "" : "s"}, ${unit} per UTC day, ${fmtDay(S.d1)} to ${fmtDay(S.d2)}`;
  const svg = d3.select(el).html("").append("svg").attr("width", w).attr("height", h);
  svg.append("g").attr("class", "grid").attr("transform", `translate(${M_.left},0)`).call(d3.axisLeft(y).ticks(4).tickSize(-(w - M_.left - M_.right)).tickFormat(""));
  const area = nd > 1 ? d3.area().x((d) => x(d.data.t)).y0((d) => y(d[0])).y1((d) => y(d[1])).curve(d3.curveMonotoneX) : null;
  if (area) svg.append("g").selectAll("path").data(stack).join("path").attr("fill", (d) => GROUP_COLORS[d.key]).attr("fill-opacity", 0.85).attr("d", area);
  else svg.append("g").selectAll("rect").data(stack).join("rect").attr("x", w / 2 - 20).attr("width", 40).attr("y", (d) => y(d[0][1])).attr("height", (d) => y(d[0][0]) - y(d[0][1])).attr("fill", (d) => GROUP_COLORS[d.key]);
  const xt = Math.max(2, Math.min(nd, Math.floor((w - 60) / 52)));
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - M_.bottom})`).call(d3.axisBottom(x).ticks(xt).tickFormat(d3.utcFormat("%-d %b")).tickSizeOuter(0));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${M_.left},0)`).call(d3.axisLeft(y).ticks(4).tickFormat(fmtK).tickSizeOuter(0));
  if (!ymax) svg.append("text").attr("class", "empty").attr("x", w / 2).attr("y", h / 2).attr("text-anchor", "middle").text("No detections for this period and sensor selection");
  const nowX = x(Math.floor((S.t) / DAY) * DAY);
  svg.append("line").attr("class", "now-line").attr("x1", nowX).attr("x2", nowX).attr("y1", M_.top).attr("y2", h - M_.bottom).attr("opacity", S.t >= S.d1 && S.t < periodEnd() ? 1 : 0);
  const hl = svg.append("line").attr("class", "hover-line").attr("y1", M_.top).attr("y2", h - M_.bottom).attr("opacity", 0);
  svg.append("rect").attr("x", M_.left).attr("y", 0).attr("width", w - M_.left - M_.right).attr("height", h - M_.bottom).attr("fill", "transparent").style("cursor", "pointer")
    .on("mousemove", (e) => {
      const k = clamp(Math.round((x.invert(d3.pointer(e)[0]) - S.d1) / DAY), 0, nd - 1), r = rows[k];
      hl.attr("x1", x(r.t)).attr("x2", x(r.t)).attr("opacity", 1);
      const tot = d3.sum(keys, (g) => r[g]);
      chartTip(e, `<div class="tt-title">${fmtDay(r.t)}</div>${keys.slice().reverse().map((g) => `<div class="tt-row"><span><span style="color:${GROUP_COLORS[g]}">&#9632;</span> ${g}</span><span>${fmtK(r[g])}</span></div>`).join("")}<div class="tt-row"><span><b>Total</b></span><span><b>${fmtK(tot)}</b></span></div>`);
    })
    .on("mouseleave", () => { hl.attr("opacity", 0); hideTip(); })
    .on("click", (e) => { const k = clamp(Math.round((x.invert(d3.pointer(e)[0]) - S.d1) / DAY), 0, nd - 1); setFollow(false); setTime(S.d1 + k * DAY + 8 * HOUR); });
}

function histData() {
  const years = Object.keys(S.summary.daily).map(Number).filter((y) => y >= 2003).sort();
  const sensors = BASIS[S.basis];
  const md1 = new Date(S.d1), md2 = new Date(S.d2);
  return years.filter((y) => S.basis !== "snpp" || y >= 2012).map((y) => {
    const a = dayUTC(y, md1.getUTCMonth(), md1.getUTCDate()), b = dayUTC(y, md2.getUTCMonth(), md2.getUTCDate());
    const ser = dailyByGroup(y, sensors, S.metric, a, b);
    const row = { year: y, partial: y === new Date(lastDataDay()).getUTCFullYear() && b > lastDataDay() };
    GROUPS.forEach((g, gi) => { row[g] = d3.sum(ser[gi]); });
    return row;
  });
}
function renderHist() {
  const el = $("#chart-hist"), w = chartW(el), h = 170;
  const data = histData(), keys = GROUPS.filter((g) => !S.hiddenGroups.has(g));
  $("#hist-title").textContent = `Historical totals for ${fmtDM(S.d1)} - ${fmtDM(S.d2)}`;
  $("#hist-sub").textContent = `${S.basis === "modis" ? "MODIS Terra + Aqua (consistent since 2003)" : "VIIRS S-NPP 375 m (since 2012)"}, ${S.metric === "n" ? "detections" : "FRP sum, MW"}. Click a bar to open that season.`;
  const stack = d3.stack().keys(keys)(data);
  const x = d3.scaleBand().domain(data.map((d) => d.year)).range([M_.left, w - M_.right]).padding(0.18);
  const y = d3.scaleLinear().domain([0, d3.max(data, (d) => d3.sum(keys, (k) => d[k])) || 1]).nice(4).range([h - M_.bottom, M_.top]);
  const svg = d3.select(el).html("").append("svg").attr("width", w).attr("height", h);
  svg.append("g").attr("class", "grid").attr("transform", `translate(${M_.left},0)`).call(d3.axisLeft(y).ticks(4).tickSize(-(w - M_.left - M_.right)).tickFormat(""));
  svg.append("g").selectAll("g").data(stack).join("g").attr("fill", (d) => GROUP_COLORS[d.key])
    .selectAll("rect").data((d) => d).join("rect")
    .attr("x", (d) => x(d.data.year)).attr("width", x.bandwidth()).attr("y", (d) => y(d[1])).attr("height", (d) => Math.max(0, y(d[0]) - y(d[1])))
    .attr("fill-opacity", (d) => (d.data.year === S.year ? 1 : 0.62));
  const sel = data.find((d) => d.year === S.year);
  if (sel) svg.append("rect").attr("class", "bar-sel").attr("x", x(sel.year) - 1.5).attr("width", x.bandwidth() + 3).attr("y", y(d3.sum(keys, (k) => sel[k])) - 1.5).attr("height", h - M_.bottom - y(d3.sum(keys, (k) => sel[k])) + 1.5);
  const every = Math.ceil(data.length / Math.floor((w - 50) / 34));
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - M_.bottom})`).call(d3.axisBottom(x).tickValues(data.map((d) => d.year).filter((yv, i) => (data.length - 1 - i) % every === 0)).tickFormat((v) => `'${String(v).slice(2)}`).tickSizeOuter(0));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${M_.left},0)`).call(d3.axisLeft(y).ticks(4).tickFormat(fmtK).tickSizeOuter(0));
  svg.append("g").selectAll("rect").data(data).join("rect").attr("x", (d) => x(d.year)).attr("width", x.bandwidth()).attr("y", M_.top).attr("height", h - M_.bottom - M_.top).attr("fill", "transparent").style("cursor", "pointer")
    .on("mousemove", (e, d) => chartTip(e, `<div class="tt-title">${d.year}, ${fmtDM(S.d1)}-${fmtDM(S.d2)}${d.partial ? " (data to date)" : ""}</div>${keys.slice().reverse().map((g) => `<div class="tt-row"><span><span style="color:${GROUP_COLORS[g]}">&#9632;</span> ${g}</span><span>${fmtK(d[g])}</span></div>`).join("")}<div class="tt-row"><span><b>Total</b></span><span><b>${fmtK(d3.sum(keys, (k) => d[k]))}</b></span></div>`))
    .on("mouseleave", hideTip)
    .on("click", (e, d) => { hideTip(); setYear(d.year, true); });
}

function baselineForDistrict(id) {
  // mean over BASELINE_YEARS of the same month-day window, from rice-season district series
  const DD = S.districtDaily; if (!DD || S.season !== "rice") return null;
  const md1 = new Date(S.d1), md2 = new Date(S.d2);
  const idx = (y, md) => Math.round((dayUTC(y, md.getUTCMonth(), md.getUTCDate()) - dayUTC(y, 8, 15)) / DAY);
  const sumYear = (y) => {
    const ser = DD[y] && DD[y].snpp && DD[y].snpp[id]; if (!ser) return 0;
    const a = idx(y, md1), b = idx(y, md2); let s = 0;
    for (const [j, v] of ser) if (j >= a && j <= b) s += v;
    return s;
  };
  const yrs = d3.range(BASELINE_YEARS[0], BASELINE_YEARS[1] + 1).filter((y) => DD[y]);
  return { mean: d3.mean(yrs, sumYear), cur: sumYear(S.year) };
}
function renderTable() {
  const tb = $("#dtable tbody"), cnt = S.distCount;
  if (!cnt) return;
  const ids = d3.range(cnt.length).filter((i) => cnt[i] > 0).sort((a, b) => cnt[b] - cnt[a]).slice(0, 30);
  $("#table-sub").textContent = `${fmtDM(S.d1)}-${fmtDM(S.d2)} ${S.year}, selected sensors. Last column: VIIRS S-NPP count against its ${BASELINE_YEARS[0]}-${BASELINE_YEARS[1]} mean for the same dates.`;
  if (!ids.length) { tb.innerHTML = `<tr><td colspan="5" class="name" style="color:var(--dim);padding:10px 6px">No detections in this period.</td></tr>`; return; }
  const maxSpark = d3.max(ids, (i) => d3.max(S.distSpark[i] || [0])) || 1;
  tb.innerHTML = ids.map((i) => {
    const d = S.summary.districts[i], sp = S.distSpark[i] || [];
    const bl = baselineForDistrict(i);
    let anom = `<span class="pill flat">n/a</span>`;
    if (bl && bl.mean > 0) {
      const r = bl.cur / bl.mean;
      const cls = r >= 1.5 ? "up" : r <= 0.67 ? "down" : "flat";
      anom = `<span class="pill ${cls}" title="VIIRS S-NPP: ${fmt(bl.cur)} this year vs ${BASELINE_YEARS[0]}-${BASELINE_YEARS[1]} mean ${bl.mean.toFixed(1)}">${r >= 10 ? Math.round(r) : r.toFixed(1)}x</span>`;
    } else if (bl && bl.cur > 0) anom = `<span class="pill up" title="No VIIRS S-NPP detections here in the baseline years">new</span>`;
    const pts = sp.length > 1 ? Array.from(sp, (v, k) => `${(k / (sp.length - 1)) * 70},${16 - (v / maxSpark) * 14}`).join(" ") : "";
    return `<tr data-id="${i}"><td class="name"><div class="cell-name">${d.name}</div><div class="cell-sub">${d.state}</div></td>
      <td><svg width="56" height="18" viewBox="0 0 72 18" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${GROUP_COLORS[groupOf(d.state)]}" stroke-width="1.4"/></svg></td>
      <td>${fmt(cnt[i])}</td><td>${fmtK(S.distFrp[i])}</td><td>${anom}</td></tr>`;
  }).join("");
  tb.querySelectorAll("tr[data-id]").forEach((tr) => {
    const id = +tr.dataset.id;
    tr.onmouseenter = () => { if (hoverId !== null) map.setFeatureState({ source: "districts", id: hoverId }, { hover: false }); hoverId = id; map.setFeatureState({ source: "districts", id }, { hover: true }); };
    tr.onclick = () => {
      const f = S.geo.districts.features[id], b = d3.geoBounds(f);
      map.fitBounds([[b[0][0], b[0][1]], [b[1][0], b[1][1]]], { padding: 80, duration: 1200, maxZoom: 9 });
    };
  });
}

function renderSmokeChart() {
  const el = $("#chart-smoke"), M = S.smokeMeta;
  if (!M) { el.innerHTML = `<p class="card-sub">Smoke fields not loaded. Run scripts/fetch_smoke.py.</p>`; return; }
  const w = chartW(el), h = 120, T = S.smokeTimes;
  const x = d3.scaleUtc().domain([T[0], T.at(-1)]).range([M_.left, w - M_.right]);
  const y = d3.scaleLinear().domain([0, d3.max(M.box_total_mean.concat(M.box_smoke_mean)) || 1]).nice(3).range([h - M_.bottom, M_.top]);
  const svg = d3.select(el).html("").append("svg").attr("width", w).attr("height", h);
  const fi = M.forecast.indexOf(true);
  if (fi > 0) svg.append("rect").attr("x", x(T[fi])).attr("width", w - M_.right - x(T[fi])).attr("y", M_.top).attr("height", h - M_.bottom - M_.top).attr("fill", "color-mix(in srgb, var(--warn) 8%, transparent)");
  svg.append("g").attr("class", "grid").attr("transform", `translate(${M_.left},0)`).call(d3.axisLeft(y).ticks(3).tickSize(-(w - M_.left - M_.right)).tickFormat(""));
  const line = (arr) => d3.line().x((d, i) => x(T[i])).y((d) => y(d)).curve(d3.curveMonotoneX)(arr);
  svg.append("path").attr("d", line(M.box_total_mean)).attr("fill", "none").attr("stroke", cssVar("--dim")).attr("stroke-width", 1.2).attr("stroke-dasharray", "3 3");
  svg.append("path").attr("d", line(M.box_smoke_mean)).attr("fill", "none").attr("stroke", "#d63fa6").attr("stroke-width", 1.7);
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - M_.bottom})`).call(d3.axisBottom(x).ticks(Math.floor((w - 60) / 60)).tickFormat(d3.utcFormat("%-d %b")).tickSizeOuter(0));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${M_.left},0)`).call(d3.axisLeft(y).ticks(3).tickSizeOuter(0));
  const nx = x(clamp(S.t, T[0], T.at(-1)));
  svg.append("line").attr("class", "now-line").attr("x1", nx).attr("x2", nx).attr("y1", M_.top).attr("y2", h - M_.bottom).attr("opacity", smokeFrameAt(S.t) ? 1 : 0);
  if (fi > 0) svg.append("text").attr("x", w - M_.right - 4).attr("y", M_.top + 10).attr("text-anchor", "end").attr("fill", cssVar("--warn")).attr("font-size", 10.5).text("forecast");
  svg.append("g").attr("transform", `translate(${M_.left + 6},${M_.top + 6})`).html(`<line x2="14" stroke="#d63fa6" stroke-width="1.7"/><text x="18" y="3.5" class="lbl">smoke AOD</text><line x1="84" x2="98" stroke="${cssVar("--dim")}" stroke-dasharray="3 3"/><text x="102" y="3.5" class="lbl">total AOD</text>`);
  svg.append("rect").attr("x", M_.left).attr("width", w - M_.left - M_.right).attr("height", h - M_.bottom).attr("fill", "transparent").style("cursor", "pointer")
    .on("mousemove", (e) => {
      const i = clamp(Math.round((x.invert(d3.pointer(e)[0]) - T[0]) / (3 * HOUR)), 0, T.length - 1);
      chartTip(e, `<div class="tt-title">${fmtDay(T[i])} ${fmtClock(T[i])}${M.forecast[i] ? " (forecast)" : ""}</div><div class="tt-row"><span>Smoke AOD</span><span>${M.box_smoke_mean[i].toFixed(3)}</span></div><div class="tt-row"><span>Total AOD</span><span>${M.box_total_mean[i].toFixed(3)}</span></div>`);
    })
    .on("mouseleave", hideTip)
    .on("click", (e) => { if (S.year === new Date(T[0]).getUTCFullYear() && S.season === "rice") { setFollow(false); setTime(x.invert(d3.pointer(e)[0]).getTime()); } });
}

function renderHourChart() {
  const el = $("#chart-hour"), w = chartW(el), h = 110;
  const hr = (S.summary.hourly_ist_rice || {})[S.year] || {};
  const sens = S.summary.sensors.map((n, i) => i).filter((i) => hr[S.summary.sensors[i]] && S.sensors.has(i));
  const rows = d3.range(24).map((hh) => Object.fromEntries([["h", hh], ...sens.map((i) => [i, hr[S.summary.sensors[i]][hh]])]));
  const stack = d3.stack().keys(sens)(rows);
  const x = d3.scaleBand().domain(d3.range(24)).range([M_.left, w - M_.right]).padding(0.15);
  const y = d3.scaleLinear().domain([0, d3.max(rows, (r) => d3.sum(sens, (i) => r[i])) || 1]).nice(3).range([h - M_.bottom, M_.top]);
  $("#hour-sub").textContent = `${S.year} paddy season detections by IST hour of satellite overpass. Fires lit between overpasses are missed.`;
  const svg = d3.select(el).html("").append("svg").attr("width", w).attr("height", h);
  svg.append("g").selectAll("g").data(stack).join("g").attr("fill", (d) => SENSOR_COLORS[d.key]).selectAll("rect").data((d) => d).join("rect")
    .attr("x", (d) => x(d.data.h)).attr("width", x.bandwidth()).attr("y", (d) => y(d[1])).attr("height", (d) => Math.max(0, y(d[0]) - y(d[1])));
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - M_.bottom})`).call(d3.axisBottom(x).tickValues([0, 3, 6, 9, 12, 15, 18, 21]).tickFormat((v) => `${String(v).padStart(2, "0")}h`).tickSizeOuter(0));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${M_.left},0)`).call(d3.axisLeft(y).ticks(3).tickFormat(fmtK).tickSizeOuter(0));
  svg.append("g").selectAll("rect").data(rows).join("rect").attr("x", (d) => x(d.h)).attr("width", x.bandwidth()).attr("y", M_.top).attr("height", h - M_.bottom - M_.top).attr("fill", "transparent")
    .on("mousemove", (e, r) => chartTip(e, `<div class="tt-title">${String(r.h).padStart(2, "0")}:00-${String(r.h).padStart(2, "0")}:59 IST</div>${sens.map((i) => `<div class="tt-row"><span><span style="color:${SENSOR_COLORS[i]}">&#9632;</span> ${SENSOR_SHORT[i]}</span><span>${fmt(r[i])}</span></div>`).join("")}`))
    .on("mouseleave", hideTip);
}

// CREAMS official bulletin counts (state cumulative by date, this year vs earlier years)
function renderCreams() {
  const card = $("#card-creams"), C = S.creams;
  if (!C || !C.cumulative) { card.hidden = true; return; }
  card.hidden = false;
  const sel = $("#sel-creams-state");
  const states = Object.keys(C.cumulative);
  if (!sel.options.length) {
    sel.innerHTML = states.map((s) => `<option>${s}</option>`).join("");
    sel.value = states.includes("Punjab") ? "Punjab" : states[0];
    sel.onchange = renderCreams;
  }
  const st = sel.value, byYear = C.cumulative[st] || {};
  const years = Object.keys(byYear).map(Number).sort();
  const el = $("#chart-creams"), w = chartW(el), h = 150;
  // x axis: day of season from Sep 15
  const pts = years.map((yv) => ({ year: yv, vals: byYear[yv].map(([md, v]) => [Math.round((dayUTC(2001, +md.slice(0, 2) - 1, +md.slice(3)) - dayUTC(2001, 8, 15)) / DAY), v]).filter(([dx]) => dx >= 0 && dx <= 76) }));
  const x = d3.scaleLinear().domain([0, 76]).range([M_.left, w - M_.right]);
  const y = d3.scaleLinear().domain([0, d3.max(pts, (p) => d3.max(p.vals, (v) => v[1])) || 1]).nice(4).range([h - M_.bottom, M_.top]);
  const ycol = d3.scaleSequential(d3.interpolateGnBu).domain([years[0] - 2, years.at(-1)]);
  const svg = d3.select(el).html("").append("svg").attr("width", w).attr("height", h);
  svg.append("g").attr("class", "grid").attr("transform", `translate(${M_.left},0)`).call(d3.axisLeft(y).ticks(4).tickSize(-(w - M_.left - M_.right)).tickFormat(""));
  const cur = years.at(-1);
  for (const p of pts) {
    if (!p.vals.length) continue;
    svg.append("path").attr("d", d3.line().x((d) => x(d[0])).y((d) => y(d[1]))(p.vals)).attr("fill", "none")
      .attr("stroke", p.year === cur ? "#ff6b35" : ycol(p.year)).attr("stroke-width", p.year === cur ? 2.2 : 1.2).attr("opacity", p.year === cur ? 1 : 0.85);
  }
  const ticks = [[0, "15 Sep"], [16, "1 Oct"], [31, "16 Oct"], [47, "1 Nov"], [62, "16 Nov"], [76, "30 Nov"]];
  svg.append("g").attr("class", "axis").attr("transform", `translate(0,${h - M_.bottom})`).call(d3.axisBottom(x).tickValues(ticks.map((t) => t[0])).tickFormat((v) => ticks.find((t) => t[0] === v)[1]).tickSizeOuter(0));
  svg.append("g").attr("class", "axis").attr("transform", `translate(${M_.left},0)`).call(d3.axisLeft(y).ticks(4).tickFormat(fmtK).tickSizeOuter(0));
  svg.append("rect").attr("x", M_.left).attr("width", w - M_.left - M_.right).attr("height", h - M_.bottom).attr("fill", "transparent")
    .on("mousemove", (e) => {
      const dx = Math.round(x.invert(d3.pointer(e)[0]));
      const rowsH = pts.map((p) => { const v = p.vals.filter((q) => q[0] <= dx).at(-1); return v ? `<div class="tt-row"><span>${p.year}</span><span>${fmt(v[1])}</span></div>` : ""; }).reverse().join("");
      chartTip(e, `<div class="tt-title">${st}, cumulative to ${fmtDM(dayUTC(2001, 8, 15) + dx * DAY)}</div>${rowsH}`);
    })
    .on("mouseleave", hideTip);
  $("#legend-creams").innerHTML = years.map((yv) => `<span class="li"><span class="sw line" style="background:${yv === cur ? "#ff6b35" : ycol(yv)}"></span>${yv}</span>`).join("") +
    (C.latest ? `<span class="li" style="margin-left:auto;color:var(--text-3)">Latest bulletin: ${C.latest}</span>` : "");
  $("#creams-sub").textContent = C.subtitle || "Cumulative paddy residue burning events, CREAMS daily bulletins";
}

// ---------------------------------------------------------------- timeline
const TL = { svg: null, x: null };
function renderTimeline() {
  const el = $("#tl-track"), w = el.clientWidth, h = 40;
  const x = d3.scaleUtc().domain([S.t0, S.t1]).range([8, w - 8]);
  TL.x = x;
  const svg = d3.select(el).html("").append("svg").attr("width", w).attr("height", h);
  // daily detection histogram (selected sensors) as the track background
  const nd = Math.ceil((S.t1 - S.t0) / DAY);
  const counts = new Array(nd).fill(0);
  if (S.points) { const P = S.points; for (let i = lowerBound(P.times, S.t0), j = lowerBound(P.times, S.t1); i < j; i++) if (S.sensors.has(P.list[i].s)) { const k = Math.floor((P.list[i].t - S.t0) / DAY); if (k >= 0 && k < nd) counts[k]++; } }
  const cy = d3.scaleLinear().domain([0, d3.max(counts) || 1]).range([0, 16]);
  svg.append("rect").attr("x", 8).attr("y", 6).attr("width", w - 16).attr("height", 18).attr("rx", 3).attr("fill", "var(--grid)");
  svg.append("g").selectAll("rect").data(counts).join("rect").attr("x", (d, k) => x(S.t0 + k * DAY) + 0.5).attr("width", Math.max(1, x(S.t0 + DAY) - x(S.t0) - 1))
    .attr("y", (d) => 24 - cy(d)).attr("height", (d) => cy(d)).attr("fill", "rgba(255,138,43,0.55)");
  svg.append("rect").attr("x", x(S.d1)).attr("width", Math.max(2, x(periodEnd()) - x(S.d1))).attr("y", 5).attr("height", 20).attr("fill", "none").attr("stroke", "var(--accent-2)").attr("stroke-dasharray", "3 2").attr("rx", 3);
  if (S.smokeMeta && S.smokeTimes) {
    const fi = S.smokeMeta.forecast.indexOf(true);
    if (fi > 0 && S.smokeTimes[fi] < S.t1) svg.append("rect").attr("x", x(S.smokeTimes[fi])).attr("width", Math.max(0, x(Math.min(S.t1, S.smokeTimes.at(-1))) - x(S.smokeTimes[fi]))).attr("y", 5).attr("height", 20).attr("fill", "color-mix(in srgb, var(--warn) 9%, transparent)");
  }
  const tickN = Math.max(2, Math.floor(w / 70));
  svg.append("g").attr("class", "tick").attr("transform", "translate(0,26)").call(d3.axisBottom(x).ticks(tickN).tickFormat(d3.utcFormat("%-d %b")).tickSize(3)).call((g) => g.select(".domain").remove())
    .call((g) => g.selectAll(".tick").filter((d) => x(d) < 22 || x(d) > w - 22).remove()); // keep labels inside the track
  TL.head = svg.append("g");
  TL.head.append("line").attr("y1", 2).attr("y2", 28).attr("stroke", "var(--accent-2)").attr("stroke-width", 2);
  TL.head.append("circle").attr("cy", 15).attr("r", 5).attr("fill", "var(--accent-2)").attr("stroke", "var(--panel)").attr("stroke-width", 1.5);
  moveHead();
  const scrub = (e) => setTime(x.invert(clamp(d3.pointer(e, svg.node())[0], 8, w - 8)).getTime());
  svg.call(d3.drag().on("start", (e) => { setFollow(false); S.wasPlaying = S.playing; setPlaying(false); scrub(e.sourceEvent); }).on("drag", (e) => scrub(e.sourceEvent)).on("end", () => { if (S.wasPlaying) setPlaying(true); }));
}
function moveHead() { if (TL.head && TL.x) TL.head.attr("transform", `translate(${TL.x(S.t)},0)`); }

function setTime(t, fromPlay) {
  S.t = clamp(t, S.t0, S.t1);
  $("#tl-date").textContent = fmtDay(S.t);
  $("#tl-time").textContent = fmtClock(S.t);
  moveHead();
  drawSmoke();
  updateFires(!fromPlay);
  updateBadge();
  // cheap redraws of now-lines only when the day changes
  const day = Math.floor((S.t + IST) / DAY);
  if (day !== S._lastDay) { S._lastDay = day; renderDaily(); renderSmokeChart(); renderSmokeLegend(); }
}
// ---------------------------------------------------------------- live data: latest pass, polling, follow mode
const latestDetection = () => Date.parse(S.summary.last_detection_utc || `${S.summary.last_detection_date}T12:00:00Z`);
const liveYear = () => new Date(latestDetection()).getUTCFullYear();
// show the newest pass with its fires still fresh: 45 minutes after the last detection
const liveTime = () => clamp(latestDetection() + 45 * 60e3, S.t0, S.t1);
const ago = (ms) => (ms < 90e3 ? "just now" : ms < 3600e3 ? `${Math.round(ms / 60e3)} min ago` : `${Math.round(ms / 3600e3)} h ago`);
function setFollow(on) {
  S.follow = on;
  const b = $("#btn-now"); if (b) b.setAttribute("aria-pressed", String(on));
  $("#live").classList.toggle("following", on);
}
async function goLive() {
  if (S.year !== liveYear() || S.season !== "rice") { S.season = "rice"; await setYear(liveYear(), false); }
  setFollow(true);
  setTime(liveTime());
}
function renderLive() {
  if (!S.summary) return;
  const ld = latestDetection(), M = S.smokeMeta;
  $("#live-main").textContent = `Fires to ${fmtDay(ld)}, ${fmtClock(ld)}`;
  const smokeAt = M ? Date.parse(M.analysis_until.replace("Z", ":00Z")) : null;
  $("#live-sub").textContent = [smokeAt ? `smoke run ${fmtDay(smokeAt)} ${fmtClock(smokeAt)}` : "", S.lastCheck ? `checked ${ago(Date.now() - S.lastCheck)}` : ""].filter(Boolean).join(" · ");
  $("#live").classList.toggle("stale", Date.now() - ld > 20 * HOUR);
  $("#live").title = `Newest satellite fire detection: ${fmtDay(ld)} ${fmtClock(ld)} (${ago(Date.now() - ld)}). Data rebuilt ${S.summary.generated}.` +
    (M ? ` Smoke: GEOS-FP run ${M.aer_run}, analysis to ${M.analysis_until}, forecast to ${M.times.at(-1)}.` : "") + " Click to jump to the latest data.";
}
async function pollLive() {
  try {
    // live.json is a few bytes; the multi-megabyte summary is refetched only when it has changed
    const live = await fetch(`data/fires/live.json?ts=${Date.now()}`, { cache: "no-store" }).then((r) => r.json()).catch(() => null);
    const sum = live && live.generated === S.summary.generated ? S.summary
      : await fetch(`data/fires/summary.json?ts=${Date.now()}`, { cache: "no-store" }).then((r) => r.json());
    const meta = await fetch(`data/smoke/smoke_meta.json?ts=${Date.now()}`, { cache: "no-store" }).then((r) => r.json()).catch(() => null);
    S.lastCheck = Date.now();
    let reload = false;
    if (sum.generated !== S.summary.generated) {
      const followedPeriod = S.d2 >= lastDataDay();          // the period was "the latest week": keep it current
      S.summary = sum; S.bust = sum.generated;
      for (const k of [...pointCache.keys()]) if (k.startsWith(`${liveYear()}|`)) pointCache.delete(k);
      reload = followedPeriod ? "period" : "keep";
    }
    if (meta && S.smokeMeta && (meta.aer_run !== S.smokeMeta.aer_run || meta.times.at(-1) !== S.smokeMeta.times.at(-1))) {
      await loadSmokeData(meta, `${meta.aer_run}-${meta.times.at(-1)}`);
      reload = reload || "keep";
    }
    if (reload && S.year === liveYear()) {
      const t = S.t;
      await setYear(S.year, reload === "keep");
      setTime(S.follow ? liveTime() : t);
      renderSmokeChart();
    }
  } catch (e) { /* offline or mid-publish: the next poll retries */ }
  renderLive();
}

function setPlaying(p) {
  S.playing = p;
  const b = $("#btn-play"); b.innerHTML = icon(p ? "pause" : "play");
}

let lastFrame = 0;
function loop(ts) {
  const dt = lastFrame ? Math.min(100, ts - lastFrame) : 16; lastFrame = ts;
  if (S.playing) {
    let t = S.t + dt * S.speed * (HOUR / 250);
    if (t >= S.t1) t = S.t0;
    setTime(t, true);
  }
  stepWind();
  requestAnimationFrame(loop);
}

// ---------------------------------------------------------------- orchestration
function refreshAll() {
  updatePeriodLayers();
  updateFires(true);
  renderDaily(); renderHist(); renderTable(); renderHourChart(); renderTimeline();
  renderFireLegend(); renderSmokeLegend(); updateBadge();
}

function setPeriod(d1, d2) {
  const [a, b] = seasonRange(S.year, S.season);
  d1 = clamp(d1, a, b - DAY); d2 = clamp(d2, d1, b - DAY);
  S.d1 = d1; S.d2 = d2;
  $("#d1").value = isoDay(d1); $("#d2").value = isoDay(d2);
  $("#d1").min = $("#d2").min = isoDay(a); $("#d1").max = $("#d2").max = isoDay(b - DAY);
  refreshAll();
}

async function setYear(year, keepWindow) {
  const prevD1 = S.d1, prevD2 = S.d2;
  S.year = year;
  $("#sel-year").value = `${year}|${S.season}`;
  S.points = await loadPoints(year, S.season);
  updateBurnscar();
  updateCyclones();
  const [a, b] = seasonRange(year, S.season);
  const last = lastDataDay() + DAY;
  S.t0 = a;
  S.t1 = Math.min(b, last);
  if (S.smokeTimes && new Date(S.smokeTimes[0]).getUTCFullYear() === year && S.season === "rice") S.t1 = Math.max(S.t1, Math.min(b, S.smokeTimes.at(-1)));
  if (S.t1 <= S.t0) S.t1 = b;
  let d1, d2;
  if (keepWindow && prevD1) {
    const m1 = new Date(prevD1), m2 = new Date(prevD2);
    d1 = dayUTC(year, m1.getUTCMonth(), m1.getUTCDate()); d2 = dayUTC(year, m2.getUTCMonth(), m2.getUTCDate());
  } else {
    // last 7 days that have fire data (the timeline itself may run on into the smoke forecast)
    d2 = Math.min(lastDataDay(), b - DAY, Math.floor((S.t1 - 1) / DAY) * DAY);
    d1 = d2 - 6 * DAY;
  }
  S._lastDay = null;
  setPeriod(d1, d2);
  setTime(Math.min(S.t1, periodEnd() - 3 * HOUR));
}

function buildYearSelect() {
  const years = Object.keys(S.summary.daily).map(Number).sort((a, b) => b - a);
  const sel = $("#sel-year");
  sel.innerHTML = years.map((y) => `<option value="${y}|rice">${y} paddy (Sep-Nov)</option>`).join("") +
    `<optgroup label="Wheat residue (Apr-May)">${years.map((y) => `<option value="${y}|wheat">${y} wheat (Apr-May)</option>`).join("")}</optgroup>`;
  sel.onchange = () => { setFollow(false); const [y, s] = sel.value.split("|"); const same = s === S.season; S.season = s; setYear(+y, same); };
}

function bindUI() {
  paintIcons();
  $("#d1").onchange = () => setPeriod(Date.parse($("#d1").value), Math.max(Date.parse($("#d1").value), S.d2));
  $("#d2").onchange = () => setPeriod(Math.min(S.d1, Date.parse($("#d2").value)), Date.parse($("#d2").value));
  $("#sel-smoke").onchange = (e) => { S.smokeVar = e.target.value; S.showWind = S.smokeVar !== "none" && $("#l-wind").checked; smoke.lastKey = ""; drawSmoke(); renderSmokeLegend(); };
  $("#sel-fires").onchange = (e) => setFireMode(e.target.value);
  document.querySelectorAll("#seg-base button").forEach((b) => b.onclick = () => setBasemap(b.dataset.v));
  $("#sel-speed").onchange = (e) => { S.speed = +e.target.value; };
  document.querySelectorAll("#seg-proj button").forEach((b) => b.onclick = () => setProjection(b.dataset.v));
  document.querySelectorAll("#seg-metric button").forEach((b) => b.onclick = () => {
    S.metric = b.dataset.v; press("#seg-metric", S.metric); renderDaily(); renderHist();
  });
  document.querySelectorAll("#seg-basis button").forEach((b) => b.onclick = () => {
    S.basis = b.dataset.v; press("#seg-basis", S.basis); renderHist();
  });
  $("#btn-play").onclick = () => { setFollow(false); setPlaying(!S.playing); };
  $("#btn-prev").onclick = () => { setFollow(false); setPlaying(false); setTime(Math.ceil(S.t / (3 * HOUR) - 1) * 3 * HOUR); };
  $("#btn-next").onclick = () => { setFollow(false); setPlaying(false); setTime(Math.floor(S.t / (3 * HOUR) + 1) * 3 * HOUR); };
  $("#btn-now").onclick = () => { setPlaying(false); goLive(); };
  $("#live").onclick = () => { setPlaying(false); goLive(); };
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select")) return;
    if (e.code === "Space") { e.preventDefault(); setPlaying(!S.playing); }
    if (e.code === "ArrowRight") $("#btn-next").click();
    if (e.code === "ArrowLeft") $("#btn-prev").click();
  });
  $("#btn-sidebar").onclick = () => {
    const l = document.querySelector(".layout"); l.classList.toggle("collapsed");
    $("#btn-sidebar").innerHTML = icon(l.classList.contains("collapsed") ? "panel-left-open" : "panel-left-close");
    setTimeout(() => { map.resize(); renderTimeline(); }, 50);
  };
  $("#btn-about").onclick = () => { $("#about-body").innerHTML = aboutHTML(); $("#modal-about").showModal(); };
  document.querySelectorAll("[data-close]").forEach((b) => b.onclick = () => b.closest("dialog").close());
  $("#btn-export").onclick = exportCSV;
  syncThemeIcon();
  $("#theme-btn").onclick = () => {
    const t = theme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem("fw-theme", t); } catch (e) { /* private mode */ }
    syncThemeIcon(); applyMapTheme(); renderAllCharts();
  };
  let rt; new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(renderAllCharts, 120); }).observe(document.body);
}
function syncThemeIcon() { $("#theme-icon").innerHTML = icon(theme() === "dark" ? "sun" : "moon"); }
function renderAllCharts() {
  if (!S.summary || !S.year) return;
  renderDaily(); renderHist(); renderTable(); renderSmokeChart(); renderHourChart(); renderCreams(); renderTimeline(); renderSmokeLegend();
}

function exportCSV() {
  const rows = [["latitude", "longitude", "acq_datetime_utc", "sensor", "frp_mw", "confidence", "district", "state"]];
  for (const p of pointsBetween(S.d1, periodEnd())) {
    const d = S.summary.districts[p.d] || { name: "", state: "" };
    rows.push([p.lat.toFixed(5), p.lon.toFixed(5), new Date(p.t).toISOString().slice(0, 16) + "Z", SENSOR_SHORT[p.s], p.frp.toFixed(1), ["low", "nominal", "high"][p.c], d.name, d.state]);
  }
  const blob = new Blob([rows.map((r) => r.map((v) => (/[",]/.test(v) ? `"${String(v).replace(/"/g, '""')}"` : v)).join(",")).join("\n")], { type: "text/csv" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `fires_${isoDay(S.d1)}_${isoDay(S.d2)}.csv`; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function aboutHTML() {
  const f = S.summary.files, yrs = Object.keys(f);
  const M = S.smokeMeta;
  return `
  <h3>Fire detections: NASA FIRMS</h3>
  <p>MODIS Terra/Aqua (1 km, 2003 onward), VIIRS S-NPP (375 m, 2012 onward), VIIRS NOAA-20 (2018 onward), and VIIRS NOAA-21 (2023 onward, via the API).
  Archive years come from the public FIRMS yearly country files for India and Pakistan; the current year from the FIRMS area API (standard processing to June, near-real-time after).
  Static industrial sources (FIRMS type 2) and offshore detections are removed, including from near-real-time data where the archive flagged the same 1 km cell.
  ${fmt(d3.sum(yrs, (y) => f[y].points))} records in ${yrs[0]}-${yrs.at(-1)} within ${S.summary.bbox[0]}-${S.summary.bbox[2]} E, ${S.summary.bbox[1]}-${S.summary.bbox[3]} N, India and Pakistan (FIRMS detections plus CREAMS events). Latest detection: ${S.summary.last_detection_date}.</p>
  <p>Detections are not de-duplicated across sensors: the same field can be seen by several satellites. Compare years with a single sensor family (the MODIS / VIIRS switch on the historical chart). Satellites only see fires burning at overpass time (roughly 10:30, 13:30 and 01:30 IST), and smoke or cloud can hide them, so counts are an index, not a field census.</p>
  <h3>Smoke: NASA GMAO GEOS-FP</h3>
  <p>${M ? `Run ${M.aer_run}: 3-hourly aerosol optical depth at 550 nm from organic carbon, black carbon and brown carbon (the smoke-dominated species), plus total AOD and 10 m wind, at 0.3125 x 0.25 degrees. Analysis until ${M.analysis_until}, forecast after. ${M.times.length} frames from ${M.times[0]}.` : "Not loaded."}
  Frames are time-interpolated and bicubic-smoothed on the GPU. GEOS-FP assimilates MODIS/VIIRS AOD and uses QFED fire emissions; it is the NASA counterpart of the Copernicus CAMS organic matter AOD field.</p>
  <h3>Official counts: ICAR-IARI CREAMS</h3>
  <p>Daily paddy (and wheat) residue burning bulletins from the Consortium for Research on Agroecosystem Monitoring and Modeling from Space, IARI New Delhi, parsed from the bulletin PDFs at <a href="https://creams.iari.res.in/?page_id=1123" target="_blank" rel="noopener">creams.iari.res.in</a>. CREAMS uses its own VIIRS-based protocol, so its numbers differ from the raw FIRMS counts here. The geolocated events listed in the bulletin annexures (2018 onward) can be shown on the map with the "CREAMS events" chip; it is off by default because those events are themselves satellite detections and would double count.</p>
  <h3>Night hot spots: Sentinel-2A at night (one-off, December 2025)</h3>
  <p>In December 2025 ESA switched Sentinel-2A on during its night pass for a test campaign; two scenes cover Punjab
  (9 Dec, 22:32 IST, east Punjab, Haryana and the hills; 12 Dec, 22:42 IST, west Punjab and Pakistani Punjab). With no sunlight,
  anything bright in the short-wave infrared is giving off heat. The layer shows 20 m clusters bright in both B12 (2.2 µm) and
  B11 (1.6 µm); the same rule finds nothing on the negated image, so they are not noise. On 9 Dec they recovered 32 of the
  34 VIIRS night detections made three hours later, but all 32 were forest and scrub fires in Himachal and Uttarakhand;
  the two VIIRS points in the Punjab and Haryana plains were not seen. In the plains many hot spots sit on factories and
  furnaces (Mandi Gobindgarh, Ludhiana, Mohali, Lahore), so they are labelled by setting (ESA WorldCover) and are never
  added to fire counts. December is after the paddy season, and night lights could not be mapped from these images.</p>
  <h3>Boundaries and basemaps</h3>
  <p>Country outlines: Natural Earth 1:10m, India point of view. States and districts: geoBoundaries (India districts from LGD 2021, Pakistan from geoBoundaries). Basemaps: Esri World Dark and Light Gray Canvas and World Imagery. Terrain: AWS Terrain Tiles (SRTM and GMTED elevation), drawn as hillshade, as an elevation tint in the relief basemap, and as 3D terrain (exaggerated 4.5 times) in the 3D view.</p>
  <h3>Open data and code</h3>
  <p>Every dataset behind this map, in CSV, Parquet, GeoParquet, GeoTIFF, NetCDF and GeoPackage, with the pipeline
  and a full method write-up: <a href="https://github.com/reclaimchennai/stubble-fire-watch" target="_blank" rel="noopener">github.com/reclaimchennai/stubble-fire-watch</a>.</p>
  <h3>Refreshing the data</h3>
  <p><code>./scripts/update.sh</code> pulls new FIRMS detections, the latest GEOS-FP run and new CREAMS bulletins, then rebuilds <code>data/</code>.</p>`;
}

// ---------------------------------------------------------------- boot
async function boot() {
  bindUI();
  const [summary, countries, states, districts] = await Promise.all([
    getJSON("data/fires/summary.json"), getJSON("data/geo/countries.geojson"), getJSON("data/geo/states.geojson"), getJSON("data/geo/districts.geojson")]);
  S.summary = summary; S.geo = { countries, states, districts };
  getJSON("data/fires/district_daily.json").then((d) => { S.districtDaily = d; renderTable(); }).catch(() => {});
  getJSON("data/burnscar/burnscar.json").then((b) => { S.burn = b; updateBurnscar(); }).catch(() => {});
  getJSON("data/creams/creams_dashboard.json").then((c) => { S.creams = c; renderCreams(); }).catch(() => renderCreams());
  await Promise.all([initMap(), initSmoke()]);
  S.showWind = true;
  addOverlayLayers();
  bindLayerToggles();
  buildYearSelect(); renderStateLegend();
  const cur = new Date(lastDataDay()).getUTCFullYear();
  S.season = "rice";
  await setYear(cur, false);
  setFireMode("points");
  setFollow(true);
  setTime(liveTime());
  S.lastCheck = Date.now();
  renderLive();
  setInterval(pollLive, 5 * 60e3);      // new FIRMS passes are published hourly; the server rebuilds every hour
  setInterval(renderLive, 30e3);
  renderSmokeChart();
  requestAnimationFrame(loop);
}
// console / automation handle (used to script the README screenshots)
window.__fw = { S, smoke, wind, get map() { return map; }, setTime, setYear, setFollow, setBasemap, setProjection, setFireMode, goLive };
boot().catch((e) => { console.error(e); $("#map-badge").textContent = `Failed to load data: ${e.message}`; });
})();
