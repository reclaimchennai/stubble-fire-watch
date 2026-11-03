/* Stubble Fire Watch, /db: where Delhi's air came from. Reads ../data/db/delhi_air.json (scripts/build_south.py) and the
 * season's fire points (../data/fires/points_YYYY_rice.bin); draws with MapLibre + ECharts. Self-contained, so app.js
 * does not need to know about it. */
(() => {
"use strict";

const $ = (s) => document.querySelector(s);
const icon = (n) => `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${(window.ICONS || {})[n] || ""}</svg>`;
const nf = new Intl.NumberFormat("en-IN");
const fmt = (v) => (v == null || isNaN(v) ? "–" : nf.format(Math.round(v)));
const fmt1 = (v) => (v == null || isNaN(v) ? "–" : v.toFixed(1));
const theme = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");
const cssv = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dayLabel = (iso) => { const d = new Date(iso + "T00:00:00Z"); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
const addDays = (iso, k) => new Date(Date.parse(iso + "T00:00:00Z") + k * 864e5).toISOString().slice(0, 10);
const ESRI = "https://services.arcgisonline.com/arcgis/rest/services";
const CANVAS_TILES = { dark: `${ESRI}/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`, light: `${ESRI}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}` };
const REF_TILES = { dark: `${ESRI}/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, light: `${ESRI}/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}` };

const CLS = {
  nw: { label: "North-west", color: "#f2683c" },
  stagnant: { label: "Stagnant", color: "#b36bd6" },
  east: { label: "East", color: "#f2b33d" },
  west: { label: "West / south-west", color: "#4ea8de" },
};
const CLS_ORDER = ["nw", "stagnant", "east", "west"];
const FIRE = { low: { label: "little fire (< 10)", color: "#8a8f98" }, some: { label: "some (10-50)", color: "#f2b33d" }, heavy: { label: "heavy (50+)", color: "#d63b48" } };

let A, byDate = {}, year, day = 0, mode = "day", timer = null, map = null, ready = false, BBOX = null;
const charts = {};
const fireCache = new Map();

function th() {
  return { text: cssv("--text"), text2: cssv("--text-2"), dim: cssv("--dim"), grid: cssv("--grid"), axis: cssv("--axis"), panel: cssv("--panel"), border: cssv("--border-2") };
}
function base(extra = {}) {
  const t = th();
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: "Inter, system-ui, sans-serif", color: t.text2, fontSize: 11.5 },
    animationDuration: 400,
    grid: { left: 8, right: 14, top: 36, bottom: 24, containLabel: true },
    tooltip: { trigger: "axis", backgroundColor: t.panel, borderColor: t.border, borderWidth: 1, padding: [7, 9], confine: true,
      textStyle: { color: t.text, fontSize: 12, fontFamily: "Inter, system-ui, sans-serif" }, axisPointer: { type: "shadow", shadowStyle: { color: t.grid } } },
    legend: { top: 4, left: "center", icon: "roundRect", itemWidth: 10, itemHeight: 10, itemGap: 12, textStyle: { color: t.text2, fontSize: 11.5 }, inactiveColor: t.axis },
    ...extra,
  };
}
const vAxis = (o = {}) => { const t = th(); return { type: "value", axisLabel: { color: t.dim }, splitLine: { lineStyle: { color: t.grid } }, axisLine: { show: false }, axisTick: { show: false }, nameTextStyle: { color: t.dim, fontSize: 11 }, ...o }; };
const cAxis = (data, o = {}) => { const t = th(); return { type: "category", data, axisLabel: { color: t.dim }, axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false }, ...o }; };
function chart(id) { if (!charts[id]) charts[id] = echarts.init(document.getElementById(id), null, { renderer: "canvas" }); return charts[id]; }
const tipRow = (color, label, value) => `<div class="tt-row"><span><span class="swatch" style="background:${color}"></span>${label}</span><span>${value}</span></div>`;

// ---------------------------------------------------------------- data
const seasonStart = (y) => `${y}-09-15`;
const dateOf = () => addDays(seasonStart(year), day);
const lastDay = (y) => { const ds = Object.keys(A.paths).filter((d) => d.startsWith(String(y))).sort(); return ds.length ? Math.round((Date.parse(ds.at(-1)) - Date.parse(seasonStart(y))) / 864e5) : 0; };
async function loadFires(y) {
  if (fireCache.has(y)) return fireCache.get(y);
  const p = (async () => {
    if (!BBOX) BBOX = (await fetch("../data/fires/live.json").then((r) => r.json()).catch(() => ({}))).bbox || [66, 6, 98, 37.5];
    const r = await fetch(`../data/fires/points_${y}_rice.bin`);
    const buf = r.ok ? await r.arrayBuffer() : new ArrayBuffer(0);
    const [W, S, E, N] = BBOX, n = buf.byteLength / 14, dv = new DataView(buf), y0 = Date.UTC(y, 0, 1), out = [];
    for (let i = 0; i < n; i++) {
      const o = i * 14, s = dv.getUint8(o + 12);
      if (s < 2 || s > 4) continue;   // VIIRS only, as in the trajectories
      out.push([W + (dv.getUint16(o, true) / 65535) * (E - W), S + (dv.getUint16(o + 2, true) / 65535) * (N - S), new Date(y0 + (dv.getUint16(o + 4, true) - 1) * 864e5).toISOString().slice(0, 10)]);
    }
    return out;
  })();
  fireCache.set(y, p);
  return p;
}

// ---------------------------------------------------------------- map
function initMap() {
  if (map) { map.remove(); map = null; ready = false; }
  map = new maplibregl.Map({ container: "dmap", center: [75.5, 28.2], zoom: 4.6, minZoom: 3, maxZoom: 9, attributionControl: { compact: true }, dragRotate: false,
    style: { version: 8, sources: { base: { type: "raster", tiles: [CANVAS_TILES[theme()]], tileSize: 256, maxzoom: 16, attribution: "Basemap &copy; Esri, HERE, Garmin, USGS" },
      ref: { type: "raster", tiles: [REF_TILES[theme()]], tileSize: 256, maxzoom: 16 } }, layers: [{ id: "base", type: "raster", source: "base" }] } });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.once("style.load", () => {
    const empty = { type: "FeatureCollection", features: [] };
    map.addSource("box", { type: "geojson", data: { type: "Feature", geometry: { type: "Polygon", coordinates: [[[73.5, 27.5], [78.5, 27.5], [78.5, 32.5], [73.5, 32.5], [73.5, 27.5]]] } } });
    map.addLayer({ id: "box", type: "line", source: "box", paint: { "line-color": CLS.nw.color, "line-width": 1, "line-dasharray": [3, 2], "line-opacity": 0.8 } });
    map.addSource("fires", { type: "geojson", data: empty });
    map.addLayer({ id: "fires", type: "circle", source: "fires", paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 1.1, 7, 2.6], "circle-color": "#ff8a3d", "circle-opacity": 0.75 } });
    map.addSource("paths", { type: "geojson", data: empty });
    map.addLayer({ id: "paths", type: "line", source: "paths", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": ["get", "color"], "line-width": ["get", "w"], "line-opacity": ["get", "o"] } });
    map.addSource("rec", { type: "geojson", data: { type: "Feature", geometry: { type: "Point", coordinates: [A.receptor.lon, A.receptor.lat] } } });
    map.addLayer({ id: "rec", type: "circle", source: "rec", paint: { "circle-radius": 5, "circle-color": "#fff", "circle-stroke-width": 2, "circle-stroke-color": "#111" } });
    map.addLayer({ id: "ref", type: "raster", source: "ref" });
    ready = true;
    renderMap();
  });
  $("#dmap-legend").innerHTML = CLS_ORDER.map((k) => `<span class="lg" title="${A.classes[k]}"><i style="background:${CLS[k].color}"></i>${CLS[k].label}</span>`).join("") +
    `<span class="lg" title="VIIRS fire detections in the five days up to the selected date"><i class="dot" style="background:#ff8a3d"></i>fires, 5 days before</span>`;
}
async function renderMap() {
  const date = dateOf();
  $("#dmap-date").textContent = dayLabel(date);
  renderDay(date);
  if (!ready) return;
  const feats = [];
  const add = (d, hi) => { const p = A.paths[d], r = byDate[d]; if (!p || !r) return;
    feats.push({ type: "Feature", properties: { color: CLS[r.cls].color, w: hi ? 3.2 : 1.1, o: hi ? 0.95 : 0.3 }, geometry: { type: "LineString", coordinates: p } }); };
  if (mode === "season") Object.keys(A.paths).filter((d) => d.startsWith(String(year)) && d !== date && d.slice(5, 7) >= "10" && d.slice(5, 7) <= "11").forEach((d) => add(d, false));
  add(date, true);
  map.getSource("paths").setData({ type: "FeatureCollection", features: feats });
  const pts = await loadFires(year).catch(() => []);
  if (dateOf() !== date) return;
  const lo = addDays(date, -5);
  map.getSource("fires").setData({ type: "FeatureCollection", features: pts.filter((q) => q[2] >= lo && q[2] <= date).map((q) => ({ type: "Feature", geometry: { type: "Point", coordinates: [q[0], q[1]] } })) });
}
function renderDay(date) {
  const r = byDate[date], t = th();
  $("#t-dday").textContent = `Delhi, ${dayLabel(date)}`;
  const cell = (k, v, full) => `<div class="${full ? "full" : ""}"><span class="k">${k}</span><span class="v">${v}</span></div>`;
  $("#d-dayinfo").innerHTML = !r ? `<div class="full">No trajectory for this day.</div>` : [
    cell("Air came from", `<span class="cls-pill" style="background:${CLS[r.cls].color}">${CLS[r.cls].label}</span> <span style="font-weight:400;font-size:12px;color:var(--text-2)">${A.classes[r.cls]}</span>`, true),
    cell("Punjab-Haryana-Delhi fires met", `${fmt1(r.fire_nw)} <span style="font-weight:400;font-size:12px;color:var(--text-2)">${r.fire ? FIRE[r.fire].label : ""}</span>`, true),
    cell("Farthest point, 5 days back", `${fmt(r.far_km)} km`), cell("Rain (ERA5)", r.precip == null ? "–" : `${fmt1(r.precip)} mm`),
    cell("PM2.5, ground", r.pm25 == null ? "–" : `${fmt(r.pm25)} µg/m³`), cell("PM2.5, CAMS", r.cams_pm25 == null ? "–" : `${fmt(r.cams_pm25)} µg/m³`),
    cell("Wind (ERA5)", r.wind == null ? "–" : `${fmt1(r.wind)} m/s`), cell("Mixing height, 14:00", r.blh14 == null ? "–" : `${fmt(r.blh14)} m`),
  ].join("");
  const ds = []; for (let k = -20; k <= 20; k++) ds.push(addDays(date, k));
  chart("c-dday").setOption(base({
    grid: { left: 8, right: 12, top: 28, bottom: 22, containLabel: true },
    legend: { ...base().legend, data: ["Ground", "CAMS"] },
    tooltip: { ...base().tooltip, axisPointer: { type: "line", lineStyle: { color: t.dim, type: "dashed" } } },
    xAxis: cAxis(ds.map((d) => `${+d.slice(8)} ${MONTHS[+d.slice(5, 7) - 1]}`), { axisLabel: { color: t.dim, interval: 9 } }),
    yAxis: vAxis({ name: "PM2.5 µg/m³" }),
    series: [
      { name: "Ground", type: "bar", barWidth: "70%", data: ds.map((d) => { const x = byDate[d]; return x && x.pm25 != null ? { value: Math.round(x.pm25), itemStyle: { color: CLS[x.cls].color, opacity: d === date ? 1 : 0.65 } } : null; }) },
      { name: "CAMS", type: "line", symbol: "none", data: ds.map((d) => (byDate[d] && byDate[d].cams_pm25 != null ? Math.round(byDate[d].cams_pm25) : null)), lineStyle: { color: t.text2, width: 1.2 }, itemStyle: { color: t.text2 },
        markLine: { symbol: "none", silent: true, data: [{ xAxis: 20 }], lineStyle: { color: t.dim, type: "dashed" }, label: { show: false } } },
    ],
  }), true);
}

// ---------------------------------------------------------------- season charts
function renderCharts() {
  const t = th(), ys = Object.keys(A.by_year).sort();
  chart("c-dfreq").setOption(base({
    tooltip: { ...base().tooltip, formatter: (ps) => `<div class="tt-title">${ps[0].axisValue}, October-November</div>` + ps.map((p) => tipRow(p.color, p.seriesName, `${fmt(p.value)} days`)).join("") },
    xAxis: cAxis(ys), yAxis: vAxis({ name: "days" }),
    series: CLS_ORDER.map((k) => ({ name: CLS[k].label, type: "bar", stack: "a", barWidth: "58%", itemStyle: { color: CLS[k].color }, data: ys.map((y) => A.by_year[y][k]) })),
  }), true);
  const M = (A.medians || {}).pm25 || {};
  chart("c-dmed").setOption(base({
    tooltip: { ...base().tooltip, formatter: (ps) => `<div class="tt-title">${ps[0].axisValue} air</div>` + ps.filter((p) => p.value != null).map((p) => {
      const m = (M[CLS_ORDER[p.dataIndex]] || {})[p.seriesId]; return tipRow(p.color, p.seriesName, `${fmt(m.median)} µg/m³ <span style="color:var(--dim)">(${m.n} days, IQR ${fmt(m.q25)}-${fmt(m.q75)})</span>`); }).join("") },
    xAxis: cAxis(CLS_ORDER.map((k) => CLS[k].label)), yAxis: vAxis({ name: "PM2.5 µg/m³" }),
    series: Object.keys(FIRE).map((lv) => ({ id: lv, name: `${FIRE[lv].label} met`, type: "bar", barGap: "12%", barMaxWidth: 26, itemStyle: { color: FIRE[lv].color },
      label: { show: true, position: "top", color: t.dim, fontSize: 10, formatter: (p) => { const m = (M[CLS_ORDER[p.dataIndex]] || {})[lv]; return m ? `n=${m.n}` : ""; } },
      data: CLS_ORDER.map((k) => { const m = (M[k] || {})[lv]; return m && m.n >= 3 ? m.median : null; }) })),
  }), true);
  // lede numbers
  const nwH = (M.nw || {}).heavy, wL = (M.west || {}).low, eL = (M.east || {}).low;
  let nw = 0, n = 0; for (const y in A.by_year) { nw += A.by_year[y].nw; n += A.by_year[y].days; }
  $("#d-lede").innerHTML = `Each line traces the air that arrived over Delhi at 11:30 IST five days back along the 850 hPa winds (about 1.5 km up) of NASA's GEOS-FP analysis, coloured by where it came from;
    dots are VIIRS fire detections of the five days before. On <b>${fmt(n ? (nw / n) * 100 : null)}%</b> of October-November days since 2018 the air came from the north-west, over Punjab and Haryana.
    ${nwH && wL ? `On dry days, Delhi's monitors read a median <b>${fmt(nwH.median)} µg/m³</b> when that air had crossed heavy burning, against <b>${fmt(wL.median)}</b> when it came from the west with little fire met${eL ? ` and <b>${fmt(eL.median)}</b> from the east` : ""}.` : ""}
    Delhi lies inside the burning region, so most days meet some fire; the right-hand chart splits each direction by how much.`;
}

// ---------------------------------------------------------------- orchestration
function setYear(y) {
  year = y; const el = $("#dmap-day"); el.max = lastDay(y);
  if (day > +el.max || day < 0) day = +el.max;
  el.value = day;
}
function step(k) { day = Math.max(0, Math.min(+$("#dmap-day").max, day + k)); $("#dmap-day").value = day; renderMap(); }
async function boot() {
  A = await fetch("../data/db/delhi_air.json").then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
  const d = A.days, n = d.date.length;
  for (let i = 0; i < n; i++) { const r = {}; for (const k in d) r[k] = d[k][i]; byDate[r.date] = r; }
  const ys = [...new Set(Object.keys(A.paths).map((x) => +x.slice(0, 4)))].sort((a, b) => b - a);
  $("#dmap-year").innerHTML = ys.map((y) => `<option value="${y}">${y}</option>`).join("");
  day = 1e9; setYear(ys[0]);
  // open on the latest day; for a past season, the peak of the burning (5 Nov)
  $("#dmap-year").onchange = (e) => { const y = +e.target.value; day = y === ys[0] ? 1e9 : Math.round((Date.parse(`${y}-11-05`) - Date.parse(seasonStart(y))) / 864e5); setYear(y); renderMap(); };
  $("#dmap-day").oninput = (e) => { day = +e.target.value; renderMap(); };
  $("#dmap-prev").onclick = () => step(-1); $("#dmap-next").onclick = () => step(1);
  $("#dmap-play").onclick = () => {
    if (timer) { clearInterval(timer); timer = null; $("#dmap-play").innerHTML = icon("play"); return; }
    $("#dmap-play").innerHTML = icon("pause");
    if (day >= +$("#dmap-day").max) { day = 0; }
    timer = setInterval(() => { if (day >= +$("#dmap-day").max) { clearInterval(timer); timer = null; $("#dmap-play").innerHTML = icon("play"); return; } step(1); }, 650);
  };
  document.querySelectorAll("#seg-dpaths button").forEach((b) => b.onclick = () => { mode = b.dataset.v; document.querySelectorAll("#seg-dpaths button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); renderMap(); });
  // follow the page's theme toggle: redraw charts and basemap
  new MutationObserver(() => { for (const k of Object.keys(charts)) { charts[k].dispose(); delete charts[k]; } renderCharts(); initMap(); renderDay(dateOf()); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { Object.values(charts).forEach((c) => c.resize()); }, 120); });
  renderCharts();
  initMap();
  renderDay(dateOf());
}
const start = () => boot().catch((e) => { console.error(e); const s = $("#sect-dair"); if (s) s.hidden = true; });
if (document.readyState === "loading") addEventListener("DOMContentLoaded", start); else start();
})();
