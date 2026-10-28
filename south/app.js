/* Stubble Fire Watch: north-to-south page. Reads ../data/db/south.json (scripts/build_south.py), the season's fire
 * points (../data/fires/points_YYYY_rice.bin) and draws with MapLibre + ECharts. Every number shown is computed here. */
(() => {
"use strict";

const $ = (s) => document.querySelector(s);
const icon = (n) => `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${(window.ICONS || {})[n] || ""}</svg>`;
const paintIcons = (root = document) => root.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = icon(el.dataset.icon); });
const nf = new Intl.NumberFormat("en-IN");
const fmt = (v) => (v == null || isNaN(v) ? "–" : nf.format(Math.round(v)));
const fmt1 = (v) => (v == null || isNaN(v) ? "–" : v.toFixed(1));
const mean = (a) => { const v = a.filter((x) => x != null && !isNaN(x)); return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null; };
const median = (a) => { const v = a.filter((x) => x != null && !isNaN(x)).sort((p, q) => p - q); if (!v.length) return null; const m = v.length >> 1; return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2; };
const quant = (a, q) => { const v = a.filter((x) => x != null && !isNaN(x)).sort((p, r) => p - r); if (!v.length) return null; const i = (v.length - 1) * q, lo = Math.floor(i); return v[lo] + (v[Math.min(lo + 1, v.length - 1)] - v[lo]) * (i - lo); };
const theme = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");
const cssv = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dayLabel = (iso) => { const d = new Date(iso + "T00:00:00Z"); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
const addDays = (iso, k) => new Date(Date.parse(iso + "T00:00:00Z") + k * 864e5).toISOString().slice(0, 10);
const IST = 5.5 * 3600e3;
const istHour = (utc) => new Date(Date.parse(utc) + IST).toISOString().slice(0, 13);   // "YYYY-MM-DDTHH" in IST

const CLS = {
  nw_fire: { label: "NW fires", color: "#f2683c", tip: "crossed Punjab-Haryana-Delhi while it was burning" },
  igp: { label: "Gangetic plain", color: "#f2b33d", tip: "over the Indo-Gangetic Plain, no live NW fires" },
  land: { label: "Peninsula", color: "#9aa0aa", tip: "mostly over peninsular and east-coast land" },
  marine: { label: "Sea", color: "#4ea8de", tip: "mostly over the Bay of Bengal or Arabian Sea" },
};
const CLS_ORDER = ["nw_fire", "igp", "land", "marine"];
const VARS = {
  pm25: { label: "PM2.5, ground monitors", unit: "µg/m³", short: "PM2.5 (ground)" },
  cams_pm25: { label: "PM2.5, CAMS model", unit: "µg/m³", short: "PM2.5 (CAMS)" },
  cams_co: { label: "CO, CAMS model", unit: "µg/m³", short: "CO (CAMS)" },
};
const ESRI = "https://services.arcgisonline.com/arcgis/rest/services";
const CANVAS_TILES = { dark: `${ESRI}/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`, light: `${ESRI}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}` };
const REF_TILES = { dark: `${ESRI}/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, light: `${ESRI}/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}` };

let D, city = "Chennai", varSel = "pm25", mapYear, mapDay, seasonYear, pathMode = "day", playTimer = null, map = null, mapReady = false, BBOX = null;
const charts = {};
const fireCache = new Map();

// ---------------------------------------------------------------- data helpers
const R = () => D.receptors[city];
function dayRows(name = city) {
  const d = D.receptors[name].days, n = d.date.length, out = [];
  for (let i = 0; i < n; i++) { const r = {}; for (const k in d) r[k] = d[k][i]; out.push(r); }
  return out;
}
const chennaiByDate = () => { if (!D._cbd) { D._cbd = {}; for (const r of dayRows("Chennai")) D._cbd[r.date] = r; } return D._cbd; };
const years = () => [...new Set(dayRows("Chennai").map((r) => +r.date.slice(0, 4)))].sort();
const seasonDates = (y) => { const out = []; for (let d = `${y}-10-01`; d <= `${y}-12-31`; d = addDays(d, 1)) out.push(d); return out; };
function arnab() { return D.cyclones.find((c) => c.name === "ARNAB") || D.cyclones.filter((c) => c.season === Math.max(...D.cyclones.map((x) => x.season))).at(-1); }
const isNorth = (b) => !!b && (b.startsWith("N") || b === "ENE" || b === "WNW");

// ---------------------------------------------------------------- chart scaffolding (same look as /db)
function th() {
  return { text: cssv("--text"), text2: cssv("--text-2"), dim: cssv("--dim"), grid: cssv("--grid"), axis: cssv("--axis"),
    panel: cssv("--panel"), border: cssv("--border-2"), accent: cssv("--accent-2"), good: cssv("--good"), bad: cssv("--bad"), warn: cssv("--warn") };
}
function base(extra = {}) {
  const t = th();
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: "Inter, system-ui, sans-serif", color: t.text2, fontSize: 11.5 },
    animationDuration: 400,
    grid: { left: 48, right: 16, top: 36, bottom: 30 },
    tooltip: { trigger: "axis", backgroundColor: t.panel, borderColor: t.border, borderWidth: 1, padding: [7, 9], confine: true,
      textStyle: { color: t.text, fontSize: 12, fontFamily: "Inter, system-ui, sans-serif" }, axisPointer: { lineStyle: { color: t.dim, type: "dashed" } } },
    legend: { top: 4, left: "center", icon: "roundRect", itemWidth: 10, itemHeight: 10, itemGap: 12, textStyle: { color: t.text2, fontSize: 11.5 }, inactiveColor: t.axis },
    ...extra,
  };
}
const vAxis = (o = {}) => { const t = th(); return { type: "value", axisLabel: { color: t.dim }, splitLine: { lineStyle: { color: t.grid } }, axisLine: { show: false }, axisTick: { show: false }, nameTextStyle: { color: t.dim, fontSize: 11 }, ...o }; };
const cAxis = (data, o = {}) => { const t = th(); return { type: "category", data, axisLabel: { color: t.dim }, axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false }, ...o }; };
const tAxis = (o = {}) => { const t = th(); return { type: "time", axisLabel: { color: t.dim, hideOverlap: true }, axisLine: { lineStyle: { color: t.axis } }, splitLine: { show: false }, ...o }; };
function chart(id) { if (!charts[id]) charts[id] = echarts.init(document.getElementById(id), null, { renderer: "canvas" }); return charts[id]; }
const tipRow = (color, label, value) => `<div class="tt-row"><span><span class="swatch" style="background:${color}"></span>${label}</span><span>${value}</span></div>`;

// ---------------------------------------------------------------- derived numbers used in several places
function contactShare(name) {
  const f = D.receptors[name].contact_by_year; let days = 0, nw = 0;
  for (const y in f) { days += f[y].days; nw += f[y].nw_fire; }
  return days ? { share: (nw / days) * 100, days, nw } : null;
}
function effect(name, v, term = "log1p_fire_nw") {
  const r = (D.receptors[name].regression || {})[v]; if (!r) return null;
  const c = r.coef[term]; const f = (b) => (Math.pow(10, b) - 1) * 100;
  return { pct: f(c.b), lo: f(c.b - 1.96 * c.se), hi: f(c.b + 1.96 * c.se), n: r.n, r2: r.r2 };
}
function classMedian(name, v, month, cls) { return ((((D.receptors[name].class_stats || {})[month] || {})[v] || {})[cls]) || null; }
function octNovMean(name, v) { return mean(dayRows(name).filter((r) => r.date.slice(5, 7) <= "11").map((r) => r[v])); }
function cyclonePeak(c) {   // Arnab: hourly low-cost peak vs the week before closest approach
  const h = D.chennai.hourly.aqi_in_lowcost; if (!h) return null;
  const tc = istHour(c.closest_time), t0 = istHour(new Date(Date.parse(c.closest_time) - 7 * 864e5).toISOString()), t1 = istHour(new Date(Date.parse(c.closest_time) - 2 * 864e5).toISOString());
  const te = istHour(new Date(Date.parse(c.closest_time) + 2 * 864e5).toISOString());
  let peak = null, pt = null; const before = [];
  h.time.forEach((t, i) => { const v = h.pm25[i]; if (v == null) return; if (t >= t0 && t < t1) before.push(v); if (t >= istHour(new Date(Date.parse(c.closest_time) - 2 * 864e5).toISOString()) && t <= te && (peak == null || v > peak)) { peak = v; pt = t; } });
  return { peak, t: pt, before: median(before), tc };
}

// ---------------------------------------------------------------- header
function renderHeader() {
  const cs = contactShare(city), ch = contactShare("Chennai");
  const eG = effect("Chennai", "pm25"), eC = effect("Chennai", "cams_co");
  const nwN = classMedian("Chennai", "pm25", "10", "nw_fire"), maN = classMedian("Chennai", "pm25", "10", "marine"), igN = classMedian("Chennai", "pm25", "10", "igp");
  const coD = octNovMean("Delhi", "cams_co"), coC = octNovMean("Chennai", "cams_co");
  const near = D.cyclones.filter((c) => c.closest_km <= 500);
  const a = arnab(), ap = a ? cyclonePeak(a) : null;
  const sig = (e) => e && (e.lo > 0 || e.hi < 0);
  const N = compositeOf(D.cyclones.filter((c) => isNorth(c.bearing))), So = compositeOf(D.cyclones.filter((c) => !isNorth(c.bearing)));
  const after = (C) => median(C.med.slice(8, 12));                  // days +1 to +4
  $("#lede").innerHTML = `Rarely, but it shows when it does. On <b>${fmt1(ch.share)}%</b> of October-November days since 2018 (${fmt(ch.nw)} of ${fmt(ch.days)}),
    the air arriving over Chennai had crossed Punjab, Haryana or Delhi while fields there were burning, within the previous five days; on most days it came
    off the Bay of Bengal or from the peninsula.
    ${eG ? `Holding rain, wind, mixing height, sea exposure and month fixed, a tenfold rise in the north-western fires the air met goes with
    <b>${eG.pct >= 0 ? "+" : ""}${fmt1(eG.pct)}%</b> PM2.5 at Chennai's monitors (95% interval ${fmt1(eG.lo)} to ${fmt1(eG.hi)}%)${sig(eG) ? "" : ", which cannot be told apart from zero"}.` : ""}
    ${nwN && maN && igN ? `But the air of the Gangetic plain is dirty with or without the fires: on dry October days Chennai's monitors read a median <b>${fmt(nwN.median)} µg/m³</b> when the air had met the north-western fires,
    <b>${fmt(igN.median)}</b> when it came over the plain but missed them, and <b>${fmt(maN.median)}</b> when it came off the sea.` : ""}
    Across the season that is a few per cent of Chennai's PM2.5; on the handful of days the smoke arrives it can be a third.
    Cyclones matter more. ${N.n ? `When a storm passes north of Chennai it pulls land air down from the north and PM2.5 runs about <b>${fmt(Math.abs((after(N) - 1) * 100))}% ${after(N) >= 1 ? "higher" : "lower"}</b> for the next few days;` : ""}
    ${So.n ? `when it passes south, rain and sea air leave it about <b>${fmt(Math.abs((after(So) - 1) * 100))}% ${after(So) >= 1 ? "higher" : "lower"}</b>.` : ""}
    ${a && ap && ap.peak ? `${esc(titleCase(a.name))} (${esc(a.grade_max)}) passed ${fmt(a.closest_km)} km ${esc(a.bearing || "")} of Chennai on ${dayLabel(a.closest_time.slice(0, 10))};
    as it drew air in from the Gangetic plain, Chennai's sensor network rose from a median <b>${fmt(ap.before)}</b> to a peak of <b>${fmt(ap.peak)} µg/m³</b>.` : ""}`;

  const tiles = [
    { title: "Oct-Nov days with NW fire air", value: `${fmt1(cs.share)}<small>%</small>`, sub: `${esc(city)}, ${fmt(cs.nw)} of ${fmt(cs.days)} days since 2018`, cls: "warn" },
    { title: "Oct PM2.5: NW fire air · plain air · sea air", value: nwN && maN && igN ? `${fmt(nwN.median)}<small> · ${fmt(igN.median)} · ${fmt(maN.median)}</small>` : "–", sub: "Chennai ground monitors, dry days, µg/m³ median", cls: "" },
    { title: "Tenfold more NW fires met", value: eG ? `${eG.pct >= 0 ? "+" : ""}${fmt1(eG.pct)}<small>%</small>` : "–", sub: eG ? `Chennai PM2.5, weather held fixed (${fmt1(eG.lo)} to ${fmt1(eG.hi)}%)` : "", cls: sig(eG) ? "bad" : "" },
    { title: "Tenfold more NW fires met", value: eC ? `${eC.pct >= 0 ? "+" : ""}${fmt1(eC.pct)}<small>%</small>` : "–", sub: eC ? `Chennai CO (CAMS), weather held fixed (${fmt1(eC.lo)} to ${fmt1(eC.hi)}%)` : "", cls: sig(eC) ? "bad" : "" },
    { title: "Storms within 500 km", value: fmt(near.length), sub: "of Chennai, 2016 onward, Oct-Dec and other months", cls: "accent" },
    { title: a ? `${titleCase(a.name)}: peak PM2.5` : "Latest cyclone", value: ap && ap.peak ? `${fmt(ap.peak)}<small> µg/m³</small>` : "–", sub: ap && ap.peak ? `Chennai sensors (low-cost), week-before median ${fmt(ap.before)}` : "", cls: "bad" },
  ];
  $("#stats").innerHTML = tiles.map((x) => `<div class="stat ${x.cls}"><div class="title">${x.title}</div><div class="value">${x.value}</div><div class="sublab">${x.sub}</div></div>`).join("");
}
const titleCase = (s) => String(s || "").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());

// ---------------------------------------------------------------- map
async function loadFires(year) {
  if (fireCache.has(year)) return fireCache.get(year);
  const p = (async () => {
    if (!BBOX) BBOX = (await fetch("../data/fires/live.json").then((r) => r.json()).catch(() => ({}))).bbox || [66, 6, 98, 37.5];
    const r = await fetch(`../data/fires/points_${year}_rice.bin`);
    const buf = r.ok ? await r.arrayBuffer() : new ArrayBuffer(0);
    const [W, Sb, E, N] = BBOX, n = buf.byteLength / 14, dv = new DataView(buf), y0 = Date.UTC(year, 0, 1);
    const pts = [];
    for (let i = 0; i < n; i++) {
      const o = i * 14, s = dv.getUint8(o + 12);
      if (s < 2 || s > 4) continue;                       // VIIRS only, as in the trajectories
      pts.push([W + (dv.getUint16(o, true) / 65535) * (E - W), Sb + (dv.getUint16(o + 2, true) / 65535) * (N - Sb),
        new Date(y0 + (dv.getUint16(o + 4, true) - 1) * 864e5).toISOString().slice(0, 10)]);
    }
    return pts;
  })();
  fireCache.set(year, p);
  return p;
}
function mapStyle() {
  return { version: 8,
    sources: { base: { type: "raster", tiles: [CANVAS_TILES[theme()]], tileSize: 256, maxzoom: 16, attribution: "Basemap &copy; Esri, HERE, Garmin, USGS" },
      ref: { type: "raster", tiles: [REF_TILES[theme()]], tileSize: 256, maxzoom: 16 } },
    layers: [{ id: "base", type: "raster", source: "base" }] };
}
function initMap() {
  if (map) { map.remove(); map = null; mapReady = false; }
  map = new maplibregl.Map({ container: "map", style: mapStyle(), center: [80.5, 20.5], zoom: 3.55, minZoom: 2.5, maxZoom: 9, attributionControl: { compact: true }, dragRotate: false });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.once("style.load", () => {   // not "load": that waits for every basemap tile, which can stall
    const empty = { type: "FeatureCollection", features: [] };
    const box = (b, cls) => ({ type: "Feature", properties: { cls }, geometry: { type: "Polygon", coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]] } });
    map.addSource("boxes", { type: "geojson", data: { type: "FeatureCollection", features: [box([73.5, 27.5, 78.5, 32.5], "nw"), box([74, 24, 88, 31], "igp")] } });
    map.addLayer({ id: "boxes", type: "line", source: "boxes", paint: { "line-color": ["match", ["get", "cls"], "nw", CLS.nw_fire.color, CLS.igp.color], "line-width": 1, "line-dasharray": [3, 2], "line-opacity": 0.8 } });
    map.addSource("fires", { type: "geojson", data: empty });
    map.addLayer({ id: "fires", type: "circle", source: "fires", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 1.1, 7, 2.6], "circle-color": "#ff8a3d", "circle-opacity": 0.75 } });
    map.addSource("cyclone", { type: "geojson", data: empty });
    map.addLayer({ id: "cyclone", type: "line", source: "cyclone", paint: { "line-color": theme() === "dark" ? "#e6e6e6" : "#333", "line-width": 1.6, "line-dasharray": [2, 1.5] } });
    map.addLayer({ id: "cyclone-pt", type: "circle", source: "cyclone", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": 4, "circle-color": theme() === "dark" ? "#e6e6e6" : "#333", "circle-stroke-width": 1, "circle-stroke-color": "#000" } });
    map.addSource("paths", { type: "geojson", data: empty });
    map.addLayer({ id: "paths", type: "line", source: "paths", layout: { "line-cap": "round", "line-join": "round" },
      paint: { "line-color": ["get", "color"], "line-width": ["get", "w"], "line-opacity": ["get", "o"] } });
    map.addSource("receptor", { type: "geojson", data: { type: "Feature", geometry: { type: "Point", coordinates: [80.27, 13.08] } } });
    map.addLayer({ id: "receptor", type: "circle", source: "receptor", paint: { "circle-radius": 5, "circle-color": "#fff", "circle-stroke-width": 2, "circle-stroke-color": "#111" } });
    map.addLayer({ id: "ref", type: "raster", source: "ref" });
    mapReady = true;
    renderMap();
  });
  $("#map-legend").innerHTML = CLS_ORDER.map((k) => `<span class="lg" title="${CLS[k].tip}"><i style="background:${CLS[k].color}"></i>${CLS[k].label}</span>`).join("") +
    `<span class="lg"><i class="dot" style="background:#ff8a3d"></i>fires, 5 days</span><span class="lg"><i class="dash"></i>cyclone</span>`;
}
function mapDate() { return addDays(`${mapYear}-10-01`, mapDay); }
async function renderMap() {
  const date = mapDate();
  $("#map-date").textContent = dayLabel(date);
  renderDayInfo(date);
  if (!mapReady) return;
  const byDate = chennaiByDate(), P = D.chennai.paths;
  const feats = [];
  const add = (d, hi) => {
    const p = P[d]; if (!p) return;
    const c = CLS[(byDate[d] || {}).cls] || CLS.land;
    feats.push({ type: "Feature", properties: { color: c.color, w: hi ? 3.2 : 1.2, o: hi ? 0.95 : 0.35 }, geometry: { type: "LineString", coordinates: p.p } });
  };
  if (pathMode === "season") seasonDates(mapYear).forEach((d) => d !== date && add(d, false));
  add(date, true);
  map.getSource("paths").setData({ type: "FeatureCollection", features: feats });
  // fires of the five days before
  const pts = await loadFires(mapYear).catch(() => []);
  if (mapDate() !== date) return;
  const lo = addDays(date, -5);
  map.getSource("fires").setData({ type: "FeatureCollection", features: pts.filter((q) => q[2] >= lo && q[2] <= date).map((q) => ({ type: "Feature", geometry: { type: "Point", coordinates: [q[0], q[1]] } })) });
  // cyclones active within +-3 days
  const cys = D.cyclones.filter((c) => c.track.some((q) => q[2].slice(0, 10) >= addDays(date, -3) && q[2].slice(0, 10) <= addDays(date, 3)));
  map.getSource("cyclone").setData({ type: "FeatureCollection", features: cys.flatMap((c) => {
    const near = c.track.reduce((a, q) => (Math.abs(Date.parse(q[2]) - Date.parse(date + "T06:00:00Z")) < Math.abs(Date.parse(a[2]) - Date.parse(date + "T06:00:00Z")) ? q : a));
    return [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: c.track.map((q) => [q[0], q[1]]) } },
      { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [near[0], near[1]] } }];
  }) });
}
function renderDayInfo(date) {
  const r = chennaiByDate()[date], t = th();
  $("#t-dayinfo").textContent = `Chennai, ${dayLabel(date)}`;
  if (!r) { $("#dayinfo").innerHTML = `<div class="full">No trajectory for this day.</div>`; }
  else {
    const c = CLS[r.cls] || null;
    const cell = (k, v, full) => `<div class="${full ? "full" : ""}"><span class="k">${k}</span><span class="v">${v}</span></div>`;
    $("#dayinfo").innerHTML = [
      cell("Air came from", c ? `<span class="cls-pill" style="background:${c.color}">${c.label}</span> <span style="font-weight:400;font-size:12px;color:var(--text-2)">${c.tip}</span>` : "–", true),
      cell("NW fire detections met", fmt1(r.fire_nw)), cell("All fire detections met", fmt1(r.fire_all)),
      cell("Hours over the Gangetic plain", fmt(r.igp_h)), cell("Hours over the sea", `${fmt(r.sea_h)} of 121`),
      cell("PM2.5, ground", r.pm25 == null ? "–" : `${fmt(r.pm25)} µg/m³`), cell("PM2.5, CAMS", r.cams_pm25 == null ? "–" : `${fmt(r.cams_pm25)} µg/m³`),
      cell("CO, CAMS", r.cams_co == null ? "–" : `${fmt(r.cams_co)} µg/m³`), cell("Rain (ERA5)", r.precip == null ? "–" : `${fmt1(r.precip)} mm`),
      r.storm_km != null && r.storm_km < 1500 ? cell("Nearest cyclone", `${esc(titleCase(r.storm))}, ${fmt(r.storm_km)} km`, true) : "",
    ].join("");
  }
  // +-20 days of Chennai PM2.5 around the date
  const ds = []; for (let k = -20; k <= 20; k++) ds.push(addDays(date, k));
  const by = chennaiByDate();
  chart("c-daypm").setOption(base({
    grid: { left: 40, right: 12, top: 28, bottom: 24 },
    legend: { ...base().legend, data: ["Ground", "CAMS"] },
    xAxis: cAxis(ds.map((d) => `${+d.slice(8)} ${MONTHS[+d.slice(5, 7) - 1]}`), { axisLabel: { color: t.dim, interval: 9 } }),
    yAxis: vAxis({ name: "PM2.5 µg/m³" }),
    series: [
      { name: "Ground", type: "bar", barWidth: "70%", data: ds.map((d) => { const r2 = by[d]; return r2 && r2.pm25 != null ? { value: r2.pm25, itemStyle: { color: (CLS[r2.cls] || CLS.land).color, opacity: d === date ? 1 : 0.65 } } : null; }) },
      { name: "CAMS", type: "line", symbol: "none", data: ds.map((d) => (by[d] ? by[d].cams_pm25 : null)), lineStyle: { color: t.text2, width: 1.2 }, itemStyle: { color: t.text2 },
        markLine: { symbol: "none", silent: true, data: [{ xAxis: 20 }], lineStyle: { color: t.dim, type: "dashed" }, label: { show: false } } },
    ],
  }), true);
}

// ---------------------------------------------------------------- frequency
function renderFreq() {
  const f = R().contact_by_year, ys = Object.keys(f).sort(), t = th();
  $("#t-freq").textContent = `Where ${city}'s air came from, October-November`;
  chart("c-freq").setOption(base({
    tooltip: { ...base().tooltip, formatter: (ps) => `<div class="tt-title">${ps[0].axisValue}</div>` + ps.map((p) => tipRow(p.color, p.seriesName, `${fmt(p.value)} days`)).join("") },
    xAxis: cAxis(ys), yAxis: vAxis({ name: "days" }),
    series: CLS_ORDER.map((k) => ({ name: CLS[k].label, type: "bar", stack: "a", barWidth: "58%", itemStyle: { color: CLS[k].color }, data: ys.map((y) => f[y][k]) })),
  }), true);
  const names = Object.keys(D.receptors).sort((a, b) => D.receptors[b].lat - D.receptors[a].lat);
  const sh = names.map((n) => contactShare(n));
  chart("c-gradient").setOption(base({
    grid: { left: 96, right: 40, top: 12, bottom: 26 },
    tooltip: { ...base().tooltip, trigger: "item", formatter: (p) => `<div class="tt-title">${p.name}</div>${tipRow(CLS.nw_fire.color, "days with NW fire air", `${fmt1(p.value)}%`)}${tipRow("transparent", "latitude", `${fmt1(D.receptors[p.name].lat)} N`)}` },
    xAxis: vAxis({ axisLabel: { color: t.dim, formatter: (v) => `${v}%` } }), yAxis: cAxis(names, { inverse: true }),
    series: [{ type: "bar", barWidth: "60%", data: names.map((n, i) => ({ value: sh[i] ? +sh[i].share.toFixed(1) : null, itemStyle: { color: n === city ? CLS.nw_fire.color : th().dim } })),
      label: { show: true, position: "right", color: t.text2, formatter: (p) => `${p.value}%` } }],
  }), true);
}

// ---------------------------------------------------------------- effect by class and regression
function renderEffect() {
  const v = varSel, t = th(), st = R().class_stats, months = ["10", "11", "12"];
  $("#t-classes").textContent = `${VARS[v].short} in ${city} by air-mass origin`;
  const total = months.reduce((s, m) => s + CLS_ORDER.reduce((s2, k) => s2 + ((((st[m] || {})[v] || {})[k] || {}).n || 0), 0), 0);
  $("#n-classes").textContent = `${fmt(total)} dry, cyclone-free days`;
  chart("c-classes").setOption(base({
    tooltip: { ...base().tooltip, trigger: "item", formatter: (p) => { const s = (((st[months[p.dataIndex]] || {})[v] || {})[CLS_ORDER[p.seriesIndex]]) || {}; return `<div class="tt-title">${MONTHS[+months[p.dataIndex] - 1]}, ${p.seriesName}</div>${tipRow(p.color, "median", `${fmt(s.median)} ${VARS[v].unit}`)}${tipRow("transparent", "middle half", `${fmt(s.q25)} to ${fmt(s.q75)}`)}${tipRow("transparent", "days", fmt(s.n))}`; } },
    xAxis: cAxis(months.map((m) => MONTHS[+m - 1])), yAxis: vAxis({ name: VARS[v].unit }),
    series: CLS_ORDER.map((k) => ({ name: CLS[k].label, type: "bar", barGap: "12%", itemStyle: { color: CLS[k].color },
      data: months.map((m) => { const s = (((st[m] || {})[v] || {})[k]); return s && s.n >= 3 ? s.median : null; }),
      label: { show: true, position: "top", color: t.dim, fontSize: 10, formatter: (p) => { const s = (((st[months[p.dataIndex]] || {})[v] || {})[k]); return s ? `n ${s.n}` : ""; } } })),
  }), true);

  const rows = Object.keys(VARS).flatMap((vv) => [["log1p_fire_nw", "NW fires"], ["log1p_fire_other", "other fires"]].map(([term, lab]) => ({ vv, lab, e: effect(city, vv, term) }))).filter((x) => x.e);
  chart("c-reg").setOption(base({
    grid: { left: 150, right: 30, top: 14, bottom: 30 },
    tooltip: { ...base().tooltip, trigger: "item", formatter: (p) => { const x = rows[p.dataIndex]; return `<div class="tt-title">${VARS[x.vv].short}: tenfold ${x.lab}</div>${tipRow(p.color, "change", `${fmt1(x.e.pct)}%`)}${tipRow("transparent", "95% interval", `${fmt1(x.e.lo)} to ${fmt1(x.e.hi)}%`)}${tipRow("transparent", "days, R²", `${fmt(x.e.n)}, ${x.e.r2}`)}`; } },
    xAxis: vAxis({ axisLabel: { color: t.dim, formatter: (v2) => `${v2}%` } }),
    yAxis: cAxis(rows.map((x) => `${VARS[x.vv].short}, ${x.lab}`), { inverse: true }),
    series: [
      { type: "bar", barWidth: "45%", data: rows.map((x) => ({ value: +x.e.pct.toFixed(1), itemStyle: { color: x.lab === "NW fires" ? CLS.nw_fire.color : t.dim } })) },
      { type: "custom", renderItem: (params, api) => {
          const x = rows[api.value(0)], y = api.coord([0, api.value(0)])[1], a = api.coord([x.e.lo, 0])[0], b = api.coord([x.e.hi, 0])[0];
          return { type: "group", children: [{ type: "line", shape: { x1: a, y1: y, x2: b, y2: y }, style: { stroke: t.text, lineWidth: 1.2 } },
            { type: "line", shape: { x1: a, y1: y - 4, x2: a, y2: y + 4 }, style: { stroke: t.text, lineWidth: 1.2 } }, { type: "line", shape: { x1: b, y1: y - 4, x2: b, y2: y + 4 }, style: { stroke: t.text, lineWidth: 1.2 } }] };
        }, data: rows.map((x, i) => [i]), z: 3, tooltip: { show: false } },
    ],
  }), true);
  const g = (R().regression || {})[v];
  $("#n-reg").textContent = g ? `${VARS[v].short}: ${fmt(g.n)} days, R² ${g.r2}. Rain: ${fmt1((Math.exp(g.coef.log1p_precip.b * Math.log(2)) - 1) * 100)}% per doubling of (1 + mm); sea share: ${fmt1((Math.exp(g.coef.sea_frac.b) - 1) * 100)}% for all-sea vs all-land air.` : "";
}

function renderSeason() {
  const y = seasonYear, t = th(), rows = dayRows().filter((r) => r.date.startsWith(String(y)));
  const by = Object.fromEntries(rows.map((r) => [r.date, r])), ds = seasonDates(y);
  $("#t-season").textContent = `${city}, October to December ${y}`;
  const cyDays = ds.filter((d) => by[d] && by[d].storm_km != null && by[d].storm_km < 800);
  const ranges = []; for (const d of cyDays) { const l = ranges.at(-1); if (l && addDays(l[1], 1) === d && l[2] === by[d].storm) l[1] = d; else ranges.push([d, d, by[d].storm]); }
  const lab = (d) => `${+d.slice(8)} ${MONTHS[+d.slice(5, 7) - 1]}`;
  chart("c-season").setOption(base({
    grid: [{ left: 48, right: 48, top: 36, bottom: 62 }, { left: 48, right: 48, height: 12, bottom: 30 }],
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    tooltip: { ...base().tooltip, formatter: (ps) => { const d = ds[ps[0].dataIndex], r = by[d] || {}; const c = CLS[r.cls]; return `<div class="tt-title">${dayLabel(d)}</div>` + (c ? tipRow(c.color, "air from", c.label) : "") + tipRow(t.accent, "PM2.5 ground", fmt(r.pm25)) + tipRow(t.text2, "PM2.5 CAMS", fmt(r.cams_pm25)) + tipRow("#4ea8de", "rain mm", fmt1(r.precip)) + tipRow(CLS.nw_fire.color, "NW fires met", fmt1(r.fire_nw)) + (r.storm_km != null && r.storm_km < 800 ? tipRow("transparent", "cyclone", `${esc(titleCase(r.storm))} ${fmt(r.storm_km)} km`) : ""); } },
    legend: { ...base().legend, data: ["PM2.5 ground", "PM2.5 CAMS", "Rain"] },
    xAxis: [cAxis(ds.map(lab), { gridIndex: 0, axisLabel: { show: false } }), cAxis(ds.map(lab), { gridIndex: 1, axisLabel: { color: t.dim, interval: 13 } })],
    yAxis: [vAxis({ name: "µg/m³", gridIndex: 0 }), vAxis({ name: "rain mm", gridIndex: 0, position: "right", splitLine: { show: false }, inverse: true, max: (v) => Math.max(40, v.max * 2.5) }),
      { type: "value", gridIndex: 1, show: false, max: 1 }],
    series: [
      { name: "Rain", type: "bar", xAxisIndex: 0, yAxisIndex: 1, barWidth: "70%", itemStyle: { color: "#4ea8de", opacity: 0.45 }, data: ds.map((d) => (by[d] ? by[d].precip : null)) },
      { name: "PM2.5 ground", type: "line", xAxisIndex: 0, yAxisIndex: 0, symbol: "circle", symbolSize: 4, connectNulls: false, data: ds.map((d) => (by[d] ? by[d].pm25 : null)), lineStyle: { color: t.accent, width: 1.8 }, itemStyle: { color: t.accent },
        markArea: { silent: true, itemStyle: { color: t.text, opacity: 0.07 }, label: { color: t.dim, fontSize: 10, position: "insideTop" }, data: ranges.map((r) => [{ xAxis: lab(r[0]), name: titleCase(r[2]) }, { xAxis: lab(r[1]) }]) } },
      { name: "PM2.5 CAMS", type: "line", xAxisIndex: 0, yAxisIndex: 0, symbol: "none", data: ds.map((d) => (by[d] ? by[d].cams_pm25 : null)), lineStyle: { color: t.text2, width: 1, type: "dashed" }, itemStyle: { color: t.text2 } },
      { name: "Air from", type: "bar", xAxisIndex: 1, yAxisIndex: 2, barWidth: "100%", data: ds.map((d) => (by[d] && by[d].cls ? { value: 1, itemStyle: { color: CLS[by[d].cls].color } } : null)) },
    ],
  }), true);
}

// ---------------------------------------------------------------- cyclones
function renderArnab() {
  const a = arnab(); if (!a) return;
  const t = th(), H = D.chennai.hourly, t0 = istHour(new Date(Date.parse(a.closest_time) - 10 * 864e5).toISOString());
  const ser = (src, k) => { const h = H[src]; if (!h || !h[k]) return []; return h.time.map((tt, i) => [Date.parse(tt + ":00:00+05:30"), h[k][i]]).filter((p) => istHour(new Date(p[0]).toISOString()) >= t0); };   // nulls kept: gaps stay gaps
  $("#t-arnab").textContent = `Cyclone ${titleCase(a.name)}, hour by hour in Chennai`;
  const lf = a.landfall, ca = Date.parse(a.closest_time);
  $("#n-arnab").textContent = `closest ${fmt(a.closest_km)} km ${a.bearing || ""}, ${a.grade_max}`;
  const ap = cyclonePeak(a);
  $("#s-arnab").innerHTML = `Times in IST. Low-cost sensors (aqi.in) read about a quarter above the reference monitors; the TNPCB/CPCB line is the
    24-hour running mean those monitors publish, so it lags. ${lf ? `Landfall ${esc(lf.near || "")} at ${new Date(Date.parse(lf.t) + IST).toISOString().slice(11, 16)} IST on ${dayLabel(istHour(lf.t).slice(0, 10))}.` : ""}
    ${ap && ap.peak ? `Peak ${fmt(ap.peak)} µg/m³ at ${ap.t.slice(11)}:00 on ${dayLabel(ap.t.slice(0, 10))}, against a median of ${fmt(ap.before)} the week before.` : ""}
    Wind direction is ERA5 at 10 m: 270° is from the west (off the land), 90° from the east (off the sea).`;
  const wd = ser("era5", "wdir");
  chart("c-arnab").setOption(base({
    grid: [{ left: 48, right: 48, top: 36, bottom: 120 }, { left: 48, right: 48, height: 60, bottom: 30 }],
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    legend: { ...base().legend, data: ["Low-cost sensors", "Reference monitors (hourly)", "TNPCB/CPCB 24 h mean", "CO (CAMS)", "Rain", "Wind from"] },
    tooltip: { ...base().tooltip, valueFormatter: (v) => (v == null ? "–" : fmt(Array.isArray(v) ? v[1] : v)) },
    xAxis: [tAxis({ gridIndex: 0, axisLabel: { show: false } }), tAxis({ gridIndex: 1 })],
    yAxis: [vAxis({ name: "PM2.5 µg/m³", gridIndex: 0 }), vAxis({ name: "CO µg/m³", gridIndex: 0, position: "right", splitLine: { show: false } }),
      vAxis({ gridIndex: 1, min: 0, max: 360, interval: 90, name: "wind from °", axisLabel: { color: t.dim, formatter: (v) => ({ 0: "N", 90: "E", 180: "S", 270: "W", 360: "N" }[v] || v) } }),
      vAxis({ gridIndex: 1, position: "right", name: "rain mm", splitLine: { show: false } })],
    series: [
      { name: "Low-cost sensors", type: "line", symbol: "none", data: ser("aqi_in_lowcost", "pm25"), lineStyle: { color: t.bad, width: 1.6 }, itemStyle: { color: t.bad },
        markLine: { symbol: "none", silent: true, lineStyle: { color: t.text2, type: "dashed" }, label: { color: t.text2, fontSize: 10, formatter: "{b}" },
          data: [{ xAxis: ca, name: "closest" }, ...(lf ? [{ xAxis: Date.parse(lf.t), name: "landfall" }] : [])] } },
      { name: "Reference monitors (hourly)", type: "line", symbol: "none", data: ser("openaq_cpcb_caaqms", "pm25"), lineStyle: { color: t.accent, width: 1.6 }, itemStyle: { color: t.accent } },
      { name: "TNPCB/CPCB 24 h mean", type: "line", symbol: "none", data: ser("tnpcb_cpcb_pol_runningmean", "pm25_24h"), lineStyle: { color: t.text2, width: 1.2, type: "dashed" }, itemStyle: { color: t.text2 } },
      { name: "CO (CAMS)", type: "line", yAxisIndex: 1, symbol: "none", data: ser("cams_model", "co"), lineStyle: { color: t.warn, width: 1.2 }, itemStyle: { color: t.warn } },
      { name: "Wind from", type: "scatter", xAxisIndex: 1, yAxisIndex: 2, symbolSize: 3, data: wd, itemStyle: { color: t.text2 } },
      { name: "Rain", type: "bar", xAxisIndex: 1, yAxisIndex: 3, data: ser("era5", "precip"), itemStyle: { color: "#4ea8de", opacity: 0.6 }, barMaxWidth: 4 },
    ],
  }), true);
}
function compositeOf(cs) {
  const days = []; for (let k = -7; k <= 7; k++) days.push(k);
  const rel = cs.map((c) => {
    const s = c.series, useG = s.pm25.slice(0, 5).filter((v) => v != null).length >= 3, a = useG ? s.pm25 : s.cams_pm25;
    const b = mean(a.slice(0, 5)); return b ? a.map((v) => (v == null ? null : v / b)) : null;
  }).filter(Boolean);
  return { n: rel.length, med: days.map((_, i) => median(rel.map((r) => r[i]))), q1: days.map((_, i) => quant(rel.map((r) => r[i]), 0.25)), q3: days.map((_, i) => quant(rel.map((r) => r[i]), 0.75)), days };
}
function renderComposite() {
  const t = th();
  const north = compositeOf(D.cyclones.filter((c) => isNorth(c.bearing))), south = compositeOf(D.cyclones.filter((c) => !isNorth(c.bearing)));
  const band = (C, name, color) => [
    { name: `${name} low`, type: "line", stack: name, data: C.q1, symbol: "none", lineStyle: { opacity: 0 }, tooltip: { show: false } },
    { name: `${name} band`, type: "line", stack: name, data: C.q3.map((v, i) => (v == null || C.q1[i] == null ? null : v - C.q1[i])), symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color, opacity: 0.16 }, tooltip: { show: false } },
    { name: `${name} (${C.n})`, type: "line", data: C.med, symbol: "circle", symbolSize: 4, lineStyle: { color, width: 2 }, itemStyle: { color } },
  ];
  chart("c-composite").setOption(base({
    legend: { ...base().legend, data: [`Passed north (${north.n})`, `Passed south or east (${south.n})`] },
    tooltip: { ...base().tooltip, formatter: (ps) => `<div class="tt-title">day ${ps[0].axisValue}</div>` + ps.filter((p) => p.seriesName.includes("(")).map((p) => tipRow(p.color, p.seriesName, `${fmt1(p.value * 100)}% of before`)).join("") },
    xAxis: cAxis(north.days.map((d) => (d > 0 ? `+${d}` : String(d))), { name: "days from closest approach", nameLocation: "middle", nameGap: 24, nameTextStyle: { color: t.dim } }),
    yAxis: vAxis({ name: "PM2.5 / before", axisLabel: { color: t.dim, formatter: (v) => `${Math.round(v * 100)}%` } }),
    series: [...band(north, "Passed north", CLS.nw_fire.color), ...band(south, "Passed south or east", CLS.marine.color),
      { type: "line", data: north.days.map(() => 1), symbol: "none", lineStyle: { color: t.dim, type: "dashed", width: 1 }, tooltip: { show: false }, silent: true }],
  }), true);
  const rows = [...D.cyclones].sort((a, b) => b.closest_time.localeCompare(a.closest_time));
  $("#t-storms tbody").innerHTML = rows.map((c) => {
    const s = c.series, g = s.pm25.filter((v) => v != null).length >= 6, a = g ? s.pm25 : s.cams_pm25;
    const before = mean(a.slice(0, 5)), peak = Math.max(...a.slice(4, 11).filter((v) => v != null)), after = mean(a.slice(9, 13));
    return `<tr data-id="${esc(c.id)}"><td class="name">${esc(titleCase(c.name || c.id))} <span style="color:var(--dim)">${c.season}</span></td><td class="name">${esc(c.grade_max || "")}</td>
      <td class="num">${fmt(c.closest_km)} km ${esc(c.bearing || "")}</td><td class="num">${fmt(before)}${g ? "" : "*"}</td><td class="num">${isFinite(peak) ? fmt(peak) : "–"}</td><td class="num">${fmt(after)}</td></tr>`;
  }).join("") + `<tr><td colspan="6" class="name" style="color:var(--dim)">PM2.5 in µg/m³, Chennai daily means: before = days -7 to -3, peak = highest of days -3 to +3, after = days +2 to +5. * CAMS model where ground data are missing. Click a row to see the storm on the map.</td></tr>`;
  document.querySelectorAll("#t-storms tbody tr[data-id]").forEach((tr) => tr.onclick = () => {
    const c = D.cyclones.find((x) => x.id === tr.dataset.id); if (!c) return;
    const d = c.closest_time.slice(0, 10), y = +d.slice(0, 4);
    if (d.slice(5) >= "10-01" && D.chennai.paths[d]) { mapYear = y; $("#map-year").value = y; mapDay = Math.round((Date.parse(d) - Date.parse(`${y}-10-01`)) / 864e5); $("#map-day").value = mapDay; renderMap(); document.querySelector(".map-panel").scrollIntoView({ behavior: "smooth", block: "center" }); }
  });
}

function renderNotes() {
  const notes = [
    ["Trajectories", "Back-trajectories are computed here from NASA GMAO GEOS-FP assimilated winds at 850 hPa (hourly means, sampled every 3 h on a 0.625 x 0.5 degree grid, keyless OPeNDAP), four arrivals a day, 120 hours back, second-order Runge-Kutta. They are two-dimensional: no vertical motion, mixing, rain-out or chemistry, so a line crossing Punjab means the air could have carried smoke, not that it did. Over the Tibetan plateau 850 hPa is below ground and lines stop. GEOS-FP winds start in December 2017, so trajectories cover 2018 onward."],
    ["Fires met", "VIIRS S-NPP vegetation-fire detections (FIRMS, type 0) on a 0.5 degree grid, counted where an hourly trajectory point fell on the day of the detection or the day after. NW fires are those in the Punjab-Haryana-Delhi box (73.5-78.5 E, 27.5-32.5 N)."],
    ["Ground air quality", "CPCB and TNPCB continuous monitors via OpenAQ (daily city means of stations within 35 km, the same quality checks as the Delhi data plus a flat-line test). OpenAQ has no CPCB data from November 2022 to February 2025; those seasons rely on the CAMS model. The hourly Chennai panel also uses the aqi.in low-cost network and the TNPCB/CPCB values collected by pol.reclaimchennai.city, which are 24-hour running means converted back from the AQI sub-index."],
    ["Model air quality and weather", "CAMS global atmospheric composition forecasts (PM2.5, CO, from August 2022) and ERA5 reanalysis (rain, wind, boundary-layer height), both via Open-Meteo. CAMS reads Chennai's PM2.5 about half of the ground monitors in October-November; its carbon monoxide is used as a combustion tracer."],
    ["Cyclones", "IBTrACS v04r01 (IMD best track, NOAA NCEI) for 2016 onward; the current storm from IMD RSMC New Delhi advisories (observed positions only on the map). Distances are to the track line; 'north' storms passed north of Chennai."],
    ["What this can and cannot say", "The comparison is observational. Air from the north-west is also drier, slower and passes over the Gangetic plain's cities and the east coast's industry, so a difference between air masses is an upper bound on what the farm fires add. The regression holds rain, wind, mixing height, sea exposure and month fixed, but not those other sources."],
  ];
  $("#notes").innerHTML = notes.map(([h, p]) => `<div class="note"><b>${h}</b>${p}</div>`).join("");
  $("#built").textContent = D.generated;
}

// ---------------------------------------------------------------- orchestration
function renderCity() { renderHeader(); renderFreq(); renderEffect(); renderSeason(); }
function renderAll() { renderCity(); renderDayInfo(mapDate()); renderArnab(); renderComposite(); renderNotes(); }
function press(sel, v) { document.querySelectorAll(`${sel} button`).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === v))); }
function bind() {
  paintIcons();
  const syncIcon = () => { $("#theme-icon").innerHTML = icon(theme() === "dark" ? "sun" : "moon"); };
  syncIcon();
  $("#theme-btn").onclick = () => {
    const t = theme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem("fw-theme", t); } catch (e) { /* private mode */ }
    syncIcon();
    for (const k of Object.keys(charts)) { charts[k].dispose(); delete charts[k]; }
    renderAll(); initMap();
  };
  $("#sel-city").onchange = (e) => { city = e.target.value; renderCity(); };
  document.querySelectorAll("#seg-var button").forEach((b) => b.onclick = () => { varSel = b.dataset.v; press("#seg-var", varSel); renderEffect(); });
  document.querySelectorAll("#seg-paths button").forEach((b) => b.onclick = () => { pathMode = b.dataset.v; press("#seg-paths", pathMode); renderMap(); });
  $("#map-year").onchange = (e) => { mapYear = +e.target.value; setDayMax(); renderMap(); };
  $("#season-year").onchange = (e) => { seasonYear = +e.target.value; renderSeason(); };
  $("#map-day").oninput = (e) => { mapDay = +e.target.value; renderMap(); };
  const step = (k) => { mapDay = Math.max(0, Math.min(+$("#map-day").max, mapDay + k)); $("#map-day").value = mapDay; renderMap(); };
  $("#map-prev").onclick = () => step(-1); $("#map-next").onclick = () => step(1);
  $("#map-play").onclick = () => {
    if (playTimer) { clearInterval(playTimer); playTimer = null; $("#map-play").innerHTML = icon("play"); return; }
    $("#map-play").innerHTML = icon("pause");
    playTimer = setInterval(() => { if (mapDay >= lastDay(mapYear)) { clearInterval(playTimer); playTimer = null; $("#map-play").innerHTML = icon("play"); return; } step(1); }, 700);
  };
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => Object.values(charts).forEach((c) => c.resize()), 120); });
}
function setDayMax() {   // the slider ends at the season's last trajectory; reset it so a restored form value cannot stick
  const el = $("#map-day"); el.max = lastDay(mapYear); mapDay = Math.min(mapDay, +el.max); el.value = mapDay;
}
const lastDay = (y) => { const ds = Object.keys(D.chennai.paths).filter((d) => d.startsWith(String(y)) && d.slice(5) >= "10-01").sort(); return ds.length ? Math.round((Date.parse(ds.at(-1)) - Date.parse(`${y}-10-01`)) / 864e5) : 0; };

async function boot() {
  bind();
  D = await window.__boot.south;
  const names = Object.keys(D.receptors).sort((a, b) => D.receptors[b].lat - D.receptors[a].lat);
  $("#sel-city").innerHTML = names.map((n) => `<option value="${esc(n)}"${n === city ? " selected" : ""}>${esc(n)}</option>`).join("");
  const ys = years();
  mapYear = seasonYear = ys.at(-1);
  $("#map-year").innerHTML = $("#season-year").innerHTML = ys.slice().reverse().map((y) => `<option value="${y}">${y}</option>`).join("");
  // open on the latest cyclone's closest approach if it is this season, else the season's last day
  const a = arnab();
  mapDay = a && a.closest_time.startsWith(String(mapYear)) && a.closest_time.slice(5, 10) >= "10-01" ? Math.round((Date.parse(a.closest_time.slice(0, 10)) - Date.parse(`${mapYear}-10-01`)) / 864e5) : lastDay(mapYear);
  mapDay = Math.min(mapDay, lastDay(mapYear));
  setDayMax();
  renderAll();
  initMap();
}
boot().catch((e) => { console.error(e); $("#lede").textContent = `Could not load the data: ${e.message}`; });
})();
