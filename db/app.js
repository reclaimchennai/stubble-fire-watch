/* Stubble Fire Watch: twenty-year analysis page. Reads ../data/db/db.json (scripts/build_db.py) and
 * ../data/geo/districts.geojson; draws with ECharts. Every number shown is computed here from those files. */
(() => {
"use strict";

const $ = (s) => document.querySelector(s);
const icon = (n) => `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${(window.ICONS || {})[n] || ""}</svg>`;
const paintIcons = (root = document) => root.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = icon(el.dataset.icon); });
const nf = new Intl.NumberFormat("en-IN");
const fmt = (v) => (v == null || isNaN(v) ? "–" : nf.format(Math.round(v)));
const fmt1 = (v) => (v == null || isNaN(v) ? "–" : v.toFixed(1));
const fmtK = (v) => (Math.abs(v) >= 1e5 ? `${(v / 1e3).toFixed(0)}k` : Math.abs(v) >= 1e4 ? `${(v / 1e3).toFixed(1).replace(/\.0$/, "")}k` : nf.format(Math.round(v)));
const pct = (a, b) => (b ? ((a - b) / b) * 100 : null);
const sum = (a) => (a || []).reduce((s, v) => s + (v || 0), 0);
const mean = (a) => { const v = a.filter((x) => x != null && !isNaN(x)); return v.length ? sum(v) / v.length : null; };
const theme = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");
const cssv = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const seasonDayLabel = (k) => { const d = new Date(Date.UTC(2001, 8, 15 + k)); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };

const STATE_COLORS = {
  "Punjab": "#e8604c", "Haryana": "#f2b33d", "Uttar Pradesh": "#4ea8de", "Madhya Pradesh": "#57c785",
  "Rajasthan": "#b48ef0", "Delhi": "#9aa0aa", "Punjab (PK)": "#f08fb0",
};
const HEAT = ["#fff3b0", "#ffd166", "#fca048", "#f2683c", "#d63b48", "#a1205e", "#5e1260"];
const IN_STATES = ["Punjab", "Haryana", "Uttar Pradesh", "Madhya Pradesh", "Rajasthan", "Delhi"];
const MAP_STATES = new Set(["Punjab", "Haryana", "Delhi", "Chandigarh", "Punjab (PK)"]);

let D, GEO, basis = "snpp", totalsMode = "sat", mapMetric = "n", mapYear = 2025, mapTimer = null;
const charts = {};

// ---------------------------------------------------------------- data helpers
const lastDate = () => new Date(D.last_detection_date + "T00:00:00Z");
const curYear = () => lastDate().getUTCFullYear();
const seasonComplete = (y) => y < curYear() || lastDate() >= new Date(Date.UTC(y, 10, 30));
const basisYears = (b = basis) => D.years.filter((y) => (b === "modis" ? y >= 2003 : y >= 2012));
const seasonTotal = (st, y, b = basis) => sum(((D.daily[b] || {})[st] || {})[y]);
const creamsSeries = (st, y) => (((D.creams || {}).cumulative || {})[st] || {})[y] || null;
const creamsTotal = (st, y) => { const s = creamsSeries(st, y); return s ? s.at(-1)[1] : null; };
const creamsAt = (st, y, md) => { const s = creamsSeries(st, y); if (!s) return null; let v = null; for (const [m, c] of s) if (m <= md) v = c; return v; };
const riceState = (st, y) => (((D.rice || {}).states || {})[st] || {})[y] || null;
let riceByMatch = null;   // match_id -> {year: area}
function buildRiceIndex() {
  riceByMatch = {};
  for (const d of (D.rice || {}).districts || []) {
    if (d.match_id == null) continue;
    const m = (riceByMatch[d.match_id] ||= {});
    for (const [y, v] of Object.entries(d.series || {})) if (v && v.area != null) m[y] = (m[y] || 0) + v.area;
  }
}
function riceArea(id, y) {   // latest area at or before y (APY lags a year)
  const m = riceByMatch && riceByMatch[id]; if (!m) return null;
  for (let k = y; k >= y - 2; k--) if (m[k] != null) return m[k];
  return null;
}
const distIdx = (id) => D.districts.ids.indexOf(id);
const distCount = (k, y, b = basis) => { const a = (D.districts[b] || {})[y]; return a ? a[k] : null; };

// ---------------------------------------------------------------- chart scaffolding
function th() {
  return { text: cssv("--text"), text2: cssv("--text-2"), dim: cssv("--dim"), grid: cssv("--grid"), axis: cssv("--axis"),
    panel: cssv("--panel"), panel2: cssv("--panel-2"), border: cssv("--border-2"), accent: cssv("--accent-2"),
    good: cssv("--good"), bad: cssv("--bad"), warn: cssv("--warn") };
}
function base(extra = {}) {
  const t = th();
  return {
    backgroundColor: "transparent",
    textStyle: { fontFamily: "Inter, system-ui, sans-serif", color: t.text2, fontSize: 11.5 },
    animationDuration: 450,
    grid: { left: 52, right: 18, top: 36, bottom: 30 },
    tooltip: { trigger: "axis", backgroundColor: t.panel, borderColor: t.border, borderWidth: 1, padding: [7, 9], confine: true,
      textStyle: { color: t.text, fontSize: 12, fontFamily: "Inter, system-ui, sans-serif" }, axisPointer: { lineStyle: { color: t.dim, type: "dashed" }, shadowStyle: { color: t.grid } } },
    legend: { top: 4, left: "center", icon: "roundRect", itemWidth: 10, itemHeight: 10, itemGap: 12, textStyle: { color: t.text2, fontSize: 11.5 }, inactiveColor: t.axis },
    ...extra,
  };
}
const vAxis = (o = {}) => { const t = th(); return { type: "value", axisLabel: { color: t.dim, formatter: (v) => fmtK(v) }, splitLine: { lineStyle: { color: t.grid } }, axisLine: { show: false }, axisTick: { show: false }, nameTextStyle: { color: t.dim, fontSize: 11 }, ...o }; };
const cAxis = (data, o = {}) => { const t = th(); return { type: "category", data, axisLabel: { color: t.dim }, axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false }, ...o }; };
function chart(id) {
  if (!charts[id]) charts[id] = echarts.init(document.getElementById(id), null, { renderer: "canvas" });
  return charts[id];
}
const tipRow = (color, label, value) => `<div class="tt-row"><span><span class="swatch" style="background:${color}"></span>${label}</span><span>${value}</span></div>`;

// ---------------------------------------------------------------- header: lede + tiles
function sparkSVG(vals, color, w = 200, h = 46) {
  const v = vals.map((x) => (x == null ? null : x)); const max = Math.max(...v.filter((x) => x != null), 1);
  const pts = v.map((x, i) => (x == null ? null : `${(i / Math.max(1, v.length - 1)) * w},${h - 4 - (x / max) * (h - 10)}`)).filter(Boolean);
  if (pts.length < 2) return "";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polygon points="0,${h} ${pts.join(" ")} ${w},${h}" fill="${color}" fill-opacity="0.14"/><polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}
function renderHeader() {
  const t = th(), cy = curYear(), lastFull = seasonComplete(cy) ? cy : cy - 1;
  const vs = (st, y) => seasonTotal(st, y, "snpp");
  const peakYear = (st) => basisYears("snpp").filter(seasonComplete).reduce((a, y) => (vs(st, y) > vs(st, a) ? y : a), 2012);
  const pkPeak = peakYear("Punjab");
  const riceP = riceState("Punjab", lastFull) || riceState("Punjab", lastFull - 1);
  const riceP0 = riceState("Punjab", pkPeak);
  const pkNow = vs("Punjab (PK)", lastFull), inNow = vs("Punjab", lastFull);
  const ov = D.overpass.filter((o) => o.viirs_total && seasonComplete(o.year));
  const nightShare = (o) => (o.viirs_night / o.viirs_total) * 100;
  const ovLast = ov.at(-1), ovMin = ov.reduce((a, o) => (nightShare(o) < nightShare(a) ? o : a), ov[0]);

  $("#lede").innerHTML = `In ${pkPeak}, VIIRS counted <b>${fmt(vs("Punjab", pkPeak))}</b> fire detections in Punjab's paddy season.
    In ${lastFull} it counted <b>${fmt(inNow)}</b>, ${Math.abs(Math.round(pct(inNow, vs("Punjab", pkPeak))))}% fewer, while the state's kharif rice
    ${riceP && riceP0 ? `area stayed near <b>${(riceP.area / 1e6).toFixed(1)} million ha</b>` : "area stayed level"}. Haryana fell almost as far.
    Across the border, Pakistani Punjab went the other way: <b>${fmt(pkNow)}</b> detections in ${lastFull}, ${(pkNow / Math.max(1, inNow)).toFixed(1)} times
    Indian Punjab. Burning also moved later in the day: the share of VIIRS detections seen on the night pass rose from
    <b>${fmt1(nightShare(ovMin))}%</b> (${ovMin.year}) to <b>${fmt1(nightShare(ovLast))}%</b> (${ovLast.year}), so part of the decline is fires the
    afternoon satellites no longer catch.`;

  const tiles = [];
  const cP = creamsTotal("Punjab", lastFull), cP16 = creamsTotal("Punjab", 2016);
  const cYears = Object.keys(((D.creams || {}).cumulative || {}).Punjab || {}).map(Number).filter(seasonComplete);
  if (cP != null) tiles.push({ cls: "good", title: `Punjab, official count ${lastFull}`, value: fmt(cP),
    sub: `${Math.round(pct(cP, cP16))}% from ${fmt(cP16)} in 2016 (CREAMS)`, spark: cYears.map((y) => creamsTotal("Punjab", y)), color: t.good });
  const cH = creamsTotal("Haryana", lastFull), cH16 = creamsTotal("Haryana", 2016);
  if (cH != null) tiles.push({ cls: "good", title: `Haryana, official count ${lastFull}`, value: fmt(cH),
    sub: `${Math.round(pct(cH, cH16))}% from ${fmt(cH16)} in 2016 (CREAMS)`, spark: cYears.map((y) => creamsTotal("Haryana", y)), color: t.good });
  tiles.push({ cls: "bad", title: `Pakistani Punjab, VIIRS ${lastFull}`, value: fmt(pkNow),
    sub: `${(pkNow / Math.max(1, inNow)).toFixed(1)}x Indian Punjab's ${fmt(inNow)}`, spark: basisYears("snpp").map((y) => vs("Punjab (PK)", y)), color: t.bad });
  const s26 = creamsSeries("Punjab", cy);
  if (s26 && !seasonComplete(cy)) {
    const md = s26.at(-1)[0], prev = creamsAt("Punjab", cy - 1, md), prev2 = creamsAt("Punjab", cy - 2, md);
    const [mm, dd] = md.split("-").map(Number);
    tiles.push({ cls: "accent", title: `${cy} so far, Punjab (CREAMS)`, value: fmt(s26.at(-1)[1]),
      sub: `to ${dd} ${MONTHS[mm - 1]}; ${cy - 1}: ${fmt(prev)}, ${cy - 2}: ${fmt(prev2)} by the same date`, spark: s26.map((r) => r[1]), color: t.accent });
  }
  tiles.push({ cls: "warn", title: "Detections on the night pass", value: `${fmt1(nightShare(ovLast))}<small>%</small>`,
    sub: `VIIRS, ${ovLast.year}; ${fmt1(nightShare(ovMin))}% in ${ovMin.year}`, spark: ov.map(nightShare), color: t.warn });
  if (riceP && riceP0) {
    const now = inNow / (riceP.area / 1000), then = vs("Punjab", pkPeak) / (riceP0.area / 1000);
    tiles.push({ cls: "good", title: "Punjab detections per 1,000 ha paddy", value: fmt1(now),
      sub: `VIIRS ${lastFull}; ${fmt1(then)} in ${pkPeak}`, spark: basisYears("snpp").filter(seasonComplete).map((y) => { const r = riceState("Punjab", y) || riceState("Punjab", y - 1); return r ? vs("Punjab", y) / (r.area / 1000) : null; }), color: t.good });
  }
  $("#stats").innerHTML = tiles.slice(0, 6).map((x) => `<div class="stat ${x.cls}"><div class="title">${x.title}</div><div class="value">${x.value}</div><div class="sublab">${x.sub}</div>${sparkSVG(x.spark, x.color)}</div>`).join("");
}

// ---------------------------------------------------------------- 1. totals + two Punjabs
function renderTotals() {
  const t = th(), cy = curYear();
  let years, series;
  if (totalsMode === "creams") {
    years = Object.keys(((D.creams || {}).cumulative || {}).Punjab || {}).map(Number).sort();
    series = IN_STATES.filter((st) => creamsSeries(st, years.at(-1)) || creamsSeries(st, years[0])).map((st) => ({
      name: st, type: "bar", stack: "s", barMaxWidth: 26, itemStyle: { color: STATE_COLORS[st] }, emphasis: { focus: "series" },
      data: years.map((y) => ({ value: creamsTotal(st, y), itemStyle: { opacity: seasonComplete(y) ? 1 : 0.45 } })) }));
    $("#t-totals").textContent = "Official residue-burning events (CREAMS)";
  } else {
    years = basisYears();
    series = IN_STATES.map((st) => ({ name: st, type: "bar", stack: "s", barMaxWidth: 26, itemStyle: { color: STATE_COLORS[st] }, emphasis: { focus: "series" },
      data: years.map((y) => ({ value: seasonTotal(st, y), itemStyle: { opacity: seasonComplete(y) ? 1 : 0.45 } })) }));
    $("#t-totals").textContent = `Paddy-season detections by state, ${basis === "modis" ? "MODIS" : "VIIRS S-NPP"}`;
  }
  chart("c-totals").setOption(base({
    tooltip: { ...base().tooltip, formatter: (ps) => {
      const y = +ps[0].axisValue, tot = sum(ps.map((p) => p.value));
      return `<div class="tt-title">${y}${seasonComplete(y) ? "" : `, to ${D.last_detection_date.slice(8)} ${MONTHS[+D.last_detection_date.slice(5, 7) - 1]}`}</div>${ps.slice().reverse().filter((p) => p.value).map((p) => tipRow(p.color, p.seriesName, fmt(p.value))).join("")}${tipRow("transparent", "<b>Total</b>", `<b>${fmt(tot)}</b>`)}`; } },
    xAxis: cAxis(years.map(String), { axisLabel: { color: t.dim, formatter: (v) => `'${v.slice(2)}`, interval: years.length > 16 ? 1 : 0 } }),
    yAxis: vAxis(), series,
  }), true);

  const by = basisYears();
  const line = (st, dash) => ({ name: st === "Punjab" ? "Punjab (India)" : st === "Punjab (PK)" ? "Punjab (Pakistan)" : st, type: "line", smooth: 0.25, symbol: "circle", symbolSize: 5,
    lineStyle: { width: 2.2, type: dash ? "dashed" : "solid", color: STATE_COLORS[st] }, itemStyle: { color: STATE_COLORS[st] },
    data: by.map((y) => (seasonComplete(y) ? seasonTotal(st, y) : null)) });
  chart("c-border").setOption(base({
    tooltip: { ...base().tooltip, valueFormatter: fmt },
    xAxis: cAxis(by.map(String), { boundaryGap: false, axisLabel: { color: t.dim, formatter: (v) => `'${v.slice(2)}` } }),
    yAxis: vAxis(), series: [line("Punjab"), line("Haryana"), line("Punjab (PK)", true)],
  }), true);
}

// ---------------------------------------------------------------- 2. calendar, timing, overpass
function renderTiming() {
  const t = th(), cal = D.calendar[basis] || {}, years = Object.keys(cal).map(Number).sort();
  const data = [];
  years.forEach((y, yi) => cal[y].forEach((v, k) => data.push([k, yi, v])));
  const breaks = basis === "modis" ? [1, 10, 40, 100, 250, 500, 1000] : [1, 25, 100, 400, 1000, 2500, 5000];
  $("#n-cal").textContent = `${basis === "modis" ? "MODIS Terra + Aqua" : "VIIRS S-NPP"} detections per day`;
  chart("c-calendar").setOption(base({
    grid: { left: 44, right: 16, top: 10, bottom: 64 },
    tooltip: { trigger: "item", backgroundColor: t.panel, borderColor: t.border, textStyle: { color: t.text, fontSize: 12 },
      formatter: (p) => `<div class="tt-title">${seasonDayLabel(p.value[0])} ${years[p.value[1]]}</div>${tipRow(HEAT[3], "Punjab + Haryana", fmt(p.value[2]))}` },
    xAxis: cAxis(Array.from({ length: D.season_days }, (_, k) => seasonDayLabel(k)), { splitArea: { show: false }, axisLabel: { color: t.dim, interval: (i) => [0, 16, 31, 47, 62, 76].includes(i) } }),
    yAxis: cAxis(years.map(String), { axisLabel: { color: t.dim, interval: years.length > 16 ? 1 : 0 } }),
    visualMap: { type: "piecewise", orient: "horizontal", left: "center", bottom: 4, itemWidth: 14, itemHeight: 10, itemGap: 6, textStyle: { color: t.dim, fontSize: 10.5 },
      pieces: [{ value: 0, color: t.panel2, label: "0" }, ...breaks.map((b, i) => ({ min: b, max: breaks[i + 1] ? breaks[i + 1] - 1 : undefined, color: HEAT[i], label: breaks[i + 1] ? `${fmtK(b)}-${fmtK(breaks[i + 1] - 1)}` : `${fmtK(b)}+` }))] },
    series: [{ type: "heatmap", data, itemStyle: { borderColor: t.panel, borderWidth: 1 }, emphasis: { itemStyle: { borderColor: t.text, borderWidth: 1 } }, progressive: 0 }],
  }), true);

  const tm = D.timing[basis] || [];
  chart("c-timing").setOption(base({
    legend: { ...base().legend, data: ["10 % to 90 % of detections", "Half done"] },
    tooltip: { ...base().tooltip, formatter: (ps) => { const r = tm.find((x) => String(x.year) === ps[0].axisValue); return `<div class="tt-title">${r.year}</div>${tipRow(t.accent, "10 % by", seasonDayLabel(r.p10))}${tipRow(t.text, "half by", seasonDayLabel(r.p50))}${tipRow(t.accent, "90 % by", seasonDayLabel(r.p90))}${tipRow("transparent", "season detections", fmt(r.total))}`; } },
    xAxis: cAxis(tm.map((r) => String(r.year)), { axisLabel: { color: t.dim, formatter: (v) => `'${v.slice(2)}` } }),
    yAxis: vAxis({ min: 10, max: 76, interval: 15, axisLabel: { color: t.dim, formatter: (v) => seasonDayLabel(v) }, inverse: true }),
    series: [
      { name: "base", type: "bar", stack: "w", itemStyle: { color: "transparent" }, data: tm.map((r) => r.p10), tooltip: { show: false }, silent: true },
      { name: "10 % to 90 % of detections", type: "bar", stack: "w", barMaxWidth: 16, itemStyle: { color: t.accent, opacity: 0.35, borderRadius: 2 }, data: tm.map((r) => r.p90 - r.p10) },
      { name: "Half done", type: "scatter", symbol: "rect", symbolSize: [14, 3], itemStyle: { color: t.text }, data: tm.map((r) => r.p50) },
    ],
  }), true);

  const ov = D.overpass.filter((o) => seasonComplete(o.year));
  chart("c-overpass").setOption(base({
    grid: { left: 52, right: 52, top: 36, bottom: 30 },
    tooltip: { ...base().tooltip, valueFormatter: (v) => (v == null ? "–" : v.toFixed(1)) },
    xAxis: cAxis(ov.map((o) => String(o.year)), { boundaryGap: false, axisLabel: { color: t.dim, formatter: (v) => `'${v.slice(2)}` } }),
    legend: { ...base().legend, top: 2 },
    yAxis: [vAxis({ axisLabel: { color: t.dim, formatter: (v) => `${v}%` } }), vAxis({ splitLine: { show: false }, axisLabel: { color: t.dim, formatter: (v) => `${v.toFixed(0)}:1` } })],
    series: [
      { name: "VIIRS night-pass share", type: "line", smooth: 0.2, symbolSize: 5, lineStyle: { width: 2.2, color: t.warn }, itemStyle: { color: t.warn },
        data: ov.map((o) => (o.viirs_total ? (o.viirs_night / o.viirs_total) * 100 : null)) },
      { name: "MODIS Aqua : Terra (right)", type: "line", yAxisIndex: 1, smooth: 0.2, symbolSize: 4, lineStyle: { width: 1.6, type: "dashed", color: t.accent }, itemStyle: { color: t.accent },
        data: ov.map((o) => (o.terra ? o.aqua / o.terra : null)) },
    ],
  }), true);
}

// ---------------------------------------------------------------- 3. map, scatter, table
function mercY(lat) { return Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360)) * 180 / Math.PI; }
function registerMap() {
  const feats = GEO.features.filter((f) => MAP_STATES.has(f.properties.state + (f.properties.country === "Pakistan" ? " (PK)" : "")))
    .map((f) => ({ ...f, properties: { ...f.properties, name: String(f.properties.id) } }));
  echarts.registerMap("igp", { type: "FeatureCollection", features: feats });
  return feats;
}
let MAP_FEATS = [];
function mapValue(id, y) {
  const k = distIdx(id); if (k < 0) return null;
  if (mapMetric === "change") {
    const avg = (ys) => mean(ys.map((yy) => distCount(k, yy)));
    const a = avg([2016, 2017, 2018]), b = avg([2023, 2024, 2025]);
    return a >= 20 ? ((b - a) / a) * 100 : null;
  }
  if (mapMetric === "burned") {
    const B = D.burn && D.burn[y]; const ar = riceArea(id, y);
    if (!B || !ar || ar < 2000 || B.districts[id] == null) return null;
    return Math.min(100, (B.districts[id] / ar) * 100);
  }
  const n = distCount(k, y); if (n == null) return null;
  if (mapMetric === "perha") { const ar = riceArea(id, y); return ar && ar > 2000 ? n / (ar / 1000) : null; }
  return n;
}
function renderMap() {
  const t = th();
  const years = basisYears().filter((y) => D.districts[basis][y]);
  mapYear = Math.min(Math.max(mapYear, years[0]), years.at(-1));
  const slider = $("#map-year"); slider.min = years[0]; slider.max = years.at(-1); slider.value = mapYear;
  $("#map-year-label").textContent = mapYear;
  slider.disabled = mapMetric === "change"; $("#map-play").disabled = mapMetric === "change";
  const data = MAP_FEATS.map((f) => ({ name: f.properties.name, value: mapValue(f.properties.id, mapYear),
    ...(f.properties.country === "Pakistan" ? { itemStyle: { borderType: "dashed", borderColor: t.dim } } : {}) }));
  const lab = basis === "modis" ? "MODIS" : "VIIRS S-NPP";
  let vm;
  if (mapMetric === "change") {
    $("#t-map").textContent = `${lab} detections, 2023-25 against 2016-18`;
    vm = { type: "piecewise", pieces: [{ max: -90, color: "#1a7f52", label: "< -90%" }, { min: -90, max: -75, color: "#3fa36b", label: "-90 to -75%" }, { min: -75, max: -50, color: "#86c58e", label: "-75 to -50%" },
      { min: -50, max: -10, color: "#cfe6c3", label: "-50 to -10%" }, { min: -10, max: 10, color: t.panel2, label: "about the same" }, { min: 10, max: 50, color: "#f6c3a0", label: "+10 to +50%" }, { min: 50, color: "#d63b48", label: "> +50%" }] };
  } else if (mapMetric === "burned") {
    const B = D.burn && D.burn[mapYear];
    $("#t-map").textContent = B ? `MODIS burned area as a share of kharif rice area, ${mapYear}${B.complete ? "" : " (incomplete season)"}` : `No MODIS burned-area map for ${mapYear} yet`;
    const br = [5, 15, 30, 50, 70, 90]; vm = { type: "piecewise", pieces: [{ max: 5, color: HEAT[0], label: "< 5%" }, ...br.map((b, i) => ({ min: b, max: br[i + 1], color: HEAT[i + 1], label: br[i + 1] ? `${b}-${br[i + 1]}%` : `${b}%+` }))] };
  } else if (mapMetric === "perha") {
    $("#t-map").textContent = `${lab} detections per 1,000 ha of kharif rice, ${mapYear}`;
    const br = [0.5, 2, 5, 10, 20, 40]; vm = { type: "piecewise", pieces: [{ max: 0.5, color: HEAT[0], label: "< 0.5" }, ...br.map((b, i) => ({ min: b, max: br[i + 1], color: HEAT[i + 1], label: br[i + 1] ? `${b}-${br[i + 1]}` : `${b}+` }))] };
  } else {
    $("#t-map").textContent = `${lab} detections, paddy season ${mapYear}${seasonComplete(mapYear) ? "" : " (to date)"}. Dashed: Pakistani Punjab`;
    const br = basis === "modis" ? [1, 25, 100, 250, 500, 1000] : [1, 100, 500, 1500, 3000, 6000];
    vm = { type: "piecewise", pieces: [{ value: 0, color: t.panel2, label: "0" }, ...br.map((b, i) => ({ min: b, max: br[i + 1] ? br[i + 1] - 1 : undefined, color: HEAT[i], label: br[i + 1] ? `${fmtK(b)}-${fmtK(br[i + 1] - 1)}` : `${fmtK(b)}+` }))] };
  }
  chart("c-map").setOption({
    backgroundColor: "transparent",
    textStyle: { fontFamily: "Inter, system-ui, sans-serif" },
    tooltip: { trigger: "item", backgroundColor: t.panel, borderColor: t.border, textStyle: { color: t.text, fontSize: 12 }, formatter: (p) => {
      const id = +p.name, d = D.districts.names[distIdx(id)], st = D.districts.states[distIdx(id)], k = distIdx(id);
      const ar = riceArea(id, mapYear);
      return `<div class="tt-title">${d}</div><div class="tt-row"><span>${st}</span><span></span></div>${tipRow(HEAT[3], `${lab} ${mapYear}`, fmt(distCount(k, mapYear)))}${ar ? tipRow("transparent", "kharif rice", `${fmt(ar / 1000)}k ha`) : ""}${p.value != null && mapMetric !== "n" ? tipRow("transparent", mapMetric === "perha" ? "per 1,000 ha" : mapMetric === "burned" ? "rice area burned" : "change", mapMetric === "perha" ? fmt1(p.value) : `${Math.round(p.value)}%`) : ""}${D.burn && D.burn[mapYear] && D.burn[mapYear].districts[id] != null ? tipRow("transparent", "MODIS burned", `${fmt(D.burn[mapYear].districts[id] / 1000)}k ha`) : ""}`; } },
    visualMap: { ...vm, left: 8, bottom: 8, itemWidth: 14, itemHeight: 10, textStyle: { color: t.dim, fontSize: 10.5 }, backgroundColor: "transparent" },
    series: [{ type: "map", map: "igp", roam: true, data, nameProperty: "name",
      projection: { project: (p) => [p[0], -mercY(p[1])], unproject: (p) => [p[0], (Math.atan(Math.exp(-p[1] * Math.PI / 180)) * 360 / Math.PI) - 90] },
      itemStyle: { areaColor: t.panel2, borderColor: t.border, borderWidth: 0.6 }, emphasis: { label: { show: false }, itemStyle: { areaColor: t.accent, borderColor: t.text } }, select: { disabled: true },
      label: { show: false } }],
  }, true);
}
function renderScatter() {
  const t = th(), cy = curYear(), late = seasonComplete(cy) ? cy : cy - 1, early = 2016;
  const pts = (y) => D.districts.ids.map((id, k) => {
    const st = D.districts.states[k]; if (st.endsWith("(PK)")) return null;
    const ar = riceArea(id, y), n = distCount(k, y, "snpp");
    return ar && ar > 5000 && n != null ? { value: [ar / 1000, n], name: D.districts.names[k], st } : null;
  }).filter(Boolean);
  const mk = (y, hollow) => ({ name: String(y), type: "scatter", symbolSize: (v) => 6 + Math.sqrt(v[0]) * 0.45,
    itemStyle: hollow ? { color: "transparent", borderColor: t.dim, borderWidth: 1.4 } : { color: t.dim },
    data: pts(y).map((p) => ({ ...p, itemStyle: hollow ? { color: "transparent", borderColor: STATE_COLORS[p.st] || t.dim, borderWidth: 1.2, opacity: 0.7 } : { color: STATE_COLORS[p.st] || t.dim, opacity: 0.9 } })) });
  chart("c-scatter").setOption(base({
    grid: { left: 56, right: 20, top: 36, bottom: 44 },
    legend: { ...base().legend, data: [String(early), String(late)] },
    tooltip: { trigger: "item", backgroundColor: t.panel, borderColor: t.border, textStyle: { color: t.text, fontSize: 12 },
      formatter: (p) => `<div class="tt-title">${p.data.name}, ${p.seriesName}</div>${tipRow(STATE_COLORS[p.data.st] || t.dim, p.data.st, "")}${tipRow("transparent", "kharif rice", `${fmt(p.value[0])}k ha`)}${tipRow("transparent", "VIIRS detections", fmt(p.value[1]))}` },
    xAxis: vAxis({ name: "kharif rice, thousand ha", nameLocation: "middle", nameGap: 26, splitLine: { lineStyle: { color: t.grid } } }),
    yAxis: vAxis({ name: "detections", nameLocation: "end" }),
    series: [mk(early, true), mk(late, false)],
  }), true);
}
let sortKey = "total", sortDir = -1;
function renderTable() {
  const years = basisYears().filter((y) => D.districts[basis][y]);
  const cy = curYear(), late = seasonComplete(cy) ? cy : cy - 1;
  const rows = D.districts.ids.map((id, k) => {
    const s = years.map((y) => distCount(k, y)); const total = sum(s.filter((_, i) => seasonComplete(years[i])));
    if (total < 50) return null;
    const avg = (ys) => mean(ys.map((y) => distCount(k, y)));
    const a = avg([2016, 2017, 2018]), b = avg([2023, 2024, 2025]);
    let peak = years[0]; years.forEach((y, i) => { if (seasonComplete(y) && s[i] > (distCount(k, peak) || 0)) peak = y; });
    const ar = riceArea(id, late);
    return { id, k, name: D.districts.names[k], st: D.districts.states[k], s, total, peak, a, b, change: a >= 20 ? ((b - a) / a) * 100 : null,
      rice: ar, perha: ar && ar > 2000 ? distCount(k, late) / (ar / 1000) : null, latest: distCount(k, late) };
  }).filter(Boolean);
  rows.sort((x, y) => { const a = x[sortKey], b = y[sortKey]; if (a == null) return 1; if (b == null) return -1; return typeof a === "string" ? sortDir * a.localeCompare(b) : sortDir * (a - b); });
  const cols = [["name", "District", "name"], ["spark", `${years[0]}-${String(years.at(-1)).slice(2)}`, ""], ["total", "All seasons", ""], ["peak", "Peak", ""],
    ["a", "2016-18 avg", ""], ["b", "2023-25 avg", ""], ["change", "Change", ""], ["latest", String(late), ""], ["rice", "Rice k ha", ""], ["perha", "per 1,000 ha", ""]];
  $("#t-districts thead").innerHTML = `<tr>${cols.map(([key, lab, cls]) => `<th class="${cls}" data-k="${key}" ${key === sortKey ? `aria-sort="${sortDir < 0 ? "descending" : "ascending"}"` : ""}>${lab}</th>`).join("")}</tr>`;
  const t = th();
  $("#t-districts tbody").innerHTML = rows.slice(0, 60).map((r) => {
    const c = r.change == null ? `<span class="pill flat">n/a</span>` : `<span class="pill ${r.change > 10 ? "up" : r.change < -10 ? "down" : "flat"}">${r.change > 0 ? "+" : ""}${Math.round(r.change)}%</span>`;
    const max = Math.max(...r.s.filter((v) => v != null), 1);
    const pts = r.s.map((v, i) => `${(i / (r.s.length - 1)) * 100},${20 - ((v || 0) / max) * 17}`).join(" ");
    return `<tr><td class="name"><div class="cell-name"><span class="swatch" style="background:${STATE_COLORS[r.st] || t.dim}"></span>${r.name}</div><div class="cell-sub">${r.st}</div></td>
      <td><svg class="spark" width="100" height="22" viewBox="0 0 100 22" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${STATE_COLORS[r.st] || t.dim}" stroke-width="1.4" vector-effect="non-scaling-stroke"/></svg></td>
      <td>${fmt(r.total)}</td><td>${r.peak}</td><td>${fmt(r.a)}</td><td>${fmt(r.b)}</td><td>${c}</td><td>${fmt(r.latest)}</td><td>${r.rice ? fmt(r.rice / 1000) : "–"}</td><td>${r.perha != null ? fmt1(r.perha) : "–"}</td></tr>`;
  }).join("");
  $("#t-districts thead").querySelectorAll("th").forEach((thEl) => thEl.onclick = () => {
    const k = thEl.dataset.k; if (k === "spark") return;
    if (k === sortKey) sortDir = -sortDir; else { sortKey = k; sortDir = k === "name" ? 1 : -1; }
    renderTable();
  });
}

// ---------------------------------------------------------------- 4. per hectare, rice, FRP, CREAMS vs FIRMS
function renderPerHa() {
  const t = th(), ys = basisYears().filter(seasonComplete);
  const per = (st) => ys.map((y) => { const r = riceState(st, y) || riceState(st, y - 1); return r && r.area ? seasonTotal(st, y) / (r.area / 1000) : null; });
  chart("c-perha").setOption(base({
    tooltip: { ...base().tooltip, valueFormatter: (v) => (v == null ? "–" : v.toFixed(2)) },
    xAxis: cAxis(ys.map(String), { boundaryGap: false, axisLabel: { color: t.dim, formatter: (v) => `'${v.slice(2)}` } }),
    yAxis: vAxis({ axisLabel: { color: t.dim, formatter: (v) => v } }),
    series: ["Punjab", "Haryana", "Uttar Pradesh"].map((st) => ({ name: st, type: "line", smooth: 0.2, symbolSize: 5, lineStyle: { width: 2.2, color: STATE_COLORS[st] }, itemStyle: { color: STATE_COLORS[st] }, data: per(st) })),
  }), true);

  const ry = Object.keys(((D.rice || {}).states || {}).Punjab || {}).map(Number).filter((y) => y >= 2003).sort();
  chart("c-rice").setOption(base({
    grid: { left: 52, right: 52, top: 36, bottom: 30 },
    tooltip: { ...base().tooltip, valueFormatter: (v) => (v == null ? "–" : `${v.toFixed(2)} M`) },
    xAxis: cAxis(ry.map(String), { axisLabel: { color: t.dim, formatter: (v) => `'${v.slice(2)}` } }),
    yAxis: [vAxis({ name: "area, M ha", axisLabel: { color: t.dim, formatter: (v) => v } }), vAxis({ name: "production, M t", splitLine: { show: false }, axisLabel: { color: t.dim, formatter: (v) => v } })],
    series: [
      ...["Punjab", "Haryana"].map((st) => ({ name: `${st} area`, type: "bar", barMaxWidth: 12, itemStyle: { color: STATE_COLORS[st], opacity: 0.75 }, data: ry.map((y) => { const r = riceState(st, y); return r ? r.area / 1e6 : null; }) })),
      ...["Punjab", "Haryana"].map((st) => ({ name: `${st} production`, type: "line", yAxisIndex: 1, symbolSize: 4, lineStyle: { width: 1.8, color: STATE_COLORS[st] }, itemStyle: { color: STATE_COLORS[st] }, data: ry.map((y) => { const r = riceState(st, y); return r ? r.prod / 1e6 : null; }) })),
    ],
  }), true);

  const fy = basisYears("snpp").filter(seasonComplete);
  chart("c-frp").setOption(base({
    tooltip: { ...base().tooltip, valueFormatter: (v) => (v == null ? "–" : `${v.toFixed(1)} MW`) },
    xAxis: cAxis(fy.map(String), { boundaryGap: false, axisLabel: { color: t.dim, formatter: (v) => `'${v.slice(2)}` } }),
    yAxis: vAxis({ name: "MW per detection", axisLabel: { color: t.dim, formatter: (v) => v } }),
    series: ["Punjab", "Haryana", "Punjab (PK)"].map((st) => ({ name: st, type: "line", smooth: 0.2, symbolSize: 5, lineStyle: { width: 2, color: STATE_COLORS[st], type: st.endsWith("(PK)") ? "dashed" : "solid" }, itemStyle: { color: STATE_COLORS[st] },
      data: fy.map((y) => { const n = seasonTotal(st, y, "snpp"), f = D.frp.snpp[st][y]; return n > 100 ? f / n : null; }) })),
  }), true);

  const cyrs = Object.keys(((D.creams || {}).cumulative || {}).Punjab || {}).map(Number).filter((y) => y >= 2016 && seasonComplete(y)).sort();
  chart("c-creamsvs").setOption(base({
    grid: { left: 52, right: 44, top: 36, bottom: 30 },
    tooltip: { ...base().tooltip, valueFormatter: (v) => (v == null ? "–" : v < 5 ? v.toFixed(2) : fmt(v)) },
    xAxis: cAxis(cyrs.map(String), { axisLabel: { color: t.dim, formatter: (v) => `'${v.slice(2)}` } }),
    yAxis: [vAxis(), vAxis({ name: "CREAMS / VIIRS", splitLine: { show: false }, axisLabel: { color: t.dim, formatter: (v) => v.toFixed(1) } })],
    series: [
      { name: "CREAMS official", type: "bar", barMaxWidth: 14, itemStyle: { color: t.good }, data: cyrs.map((y) => creamsTotal("Punjab", y)) },
      { name: "FIRMS VIIRS S-NPP", type: "bar", barMaxWidth: 14, itemStyle: { color: STATE_COLORS.Punjab }, data: cyrs.map((y) => seasonTotal("Punjab", y, "snpp")) },
      { name: "ratio", type: "line", yAxisIndex: 1, symbolSize: 5, lineStyle: { width: 1.6, type: "dashed", color: t.dim }, itemStyle: { color: t.dim },
        data: cyrs.map((y) => { const v = seasonTotal("Punjab", y, "snpp"); return v ? creamsTotal("Punjab", y) / v : null; }) },
    ],
  }), true);
}

// ---------------------------------------------------------------- 4b. MODIS burned area
const burnYears = () => Object.keys(D.burn || {}).map(Number).sort();
const burnState = (st, y) => { const b = (D.burn || {})[y]; return b && b.states[st] ? b.states[st].burned_ha : null; };
const burnComplete = (y) => !!(D.burn && D.burn[y] && D.burn[y].complete);
function renderBurn() {
  if (!D.burn) { $("#sect-burn").hidden = true; return; }
  const t = th(), ys = burnYears();
  const share = (st, y) => { const r = riceState(st, y) || riceState(st, y - 1), b = burnState(st, y); return r && b != null ? (b / r.area) * 100 : null; };
  chart("c-burn").setOption(base({
    grid: { left: 56, right: 48, top: 36, bottom: 30 },
    tooltip: { ...base().tooltip, formatter: (ps) => { const y = +ps[0].axisValue; return `<div class="tt-title">${y}${burnComplete(y) ? "" : ` (incomplete: no ${(D.burn[y].missing_months || []).map((m) => MONTHS[+m.slice(5) - 1]).join(", ")})`}</div>` +
      ps.map((p) => tipRow(p.color, p.seriesName, p.value == null ? "–" : p.seriesName.includes("%") ? `${p.value.toFixed(0)}%` : `${fmt(p.value / 1000)}k ha`)).join(""); } },
    xAxis: cAxis(ys.map(String), { axisLabel: { color: t.dim, formatter: (v) => `'${v.slice(2)}` } }),
    yAxis: [vAxis({ axisLabel: { color: t.dim, formatter: (v) => `${(v / 1e6).toFixed(1)}M` } }), vAxis({ max: 100, splitLine: { show: false }, axisLabel: { color: t.dim, formatter: (v) => `${v}%` } })],
    series: [
      ...["Punjab", "Haryana"].map((st) => ({ name: `${st} burned ha`, type: "bar", stack: "b", barMaxWidth: 18, itemStyle: { color: STATE_COLORS[st] },
        data: ys.map((y) => ({ value: burnState(st, y), itemStyle: { opacity: burnComplete(y) ? 1 : 0.35 } })) })),
      { name: "Punjab, % of rice area", type: "line", yAxisIndex: 1, symbolSize: 5, lineStyle: { width: 2, color: t.text }, itemStyle: { color: t.text },
        data: ys.map((y) => (burnComplete(y) ? share("Punjab", y) : null)) },
    ],
  }), true);
  const ratio = (st, y, b) => { const ba = burnState(st, y); return ba && burnComplete(y) ? seasonTotal(st, y, b) / (ba / 1000) : null; };
  chart("c-burnratio").setOption(base({
    tooltip: { ...base().tooltip, valueFormatter: (v) => (v == null ? "–" : v.toFixed(1)) },
    xAxis: cAxis(ys.map(String), { boundaryGap: false, axisLabel: { color: t.dim, formatter: (v) => `'${v.slice(2)}` } }),
    yAxis: vAxis({ axisLabel: { color: t.dim, formatter: (v) => v } }),
    series: [
      { name: "Punjab, VIIRS", type: "line", smooth: 0.2, symbolSize: 5, lineStyle: { width: 2.2, color: STATE_COLORS.Punjab }, itemStyle: { color: STATE_COLORS.Punjab }, data: ys.map((y) => (y >= 2012 ? ratio("Punjab", y, "snpp") : null)) },
      { name: "Haryana, VIIRS", type: "line", smooth: 0.2, symbolSize: 5, lineStyle: { width: 2.2, color: STATE_COLORS.Haryana }, itemStyle: { color: STATE_COLORS.Haryana }, data: ys.map((y) => (y >= 2012 ? ratio("Haryana", y, "snpp") : null)) },
      { name: "Punjab, MODIS", type: "line", smooth: 0.2, symbolSize: 4, lineStyle: { width: 1.6, type: "dashed", color: STATE_COLORS.Punjab }, itemStyle: { color: STATE_COLORS.Punjab }, data: ys.map((y) => ratio("Punjab", y, "modis")) },
    ],
  }), true);
  const full = ys.filter(burnComplete), last = full.at(-1);
  const peak = full.reduce((a, y) => (burnState("Punjab", y) > burnState("Punjab", a) ? y : a), full[0]);
  const r = (y) => ratio("Punjab", y, "snpp");
  $("#burn-lede").innerHTML = `Fire counts only see what is burning when a satellite passes. NASA's MODIS burned-area product maps the
    scars instead, whenever the fire was lit. It is coarse (500 m) and misses many small fields, so its hectares are a floor, but it does
    not depend on the hour of burning. Punjab's burned area peaked at <b>${fmt(burnState("Punjab", peak) / 1000)}k ha</b> in ${peak}
    and was <b>${fmt(burnState("Punjab", last) / 1000)}k ha</b> in ${last}, a fall of ${Math.abs(Math.round(pct(burnState("Punjab", last), burnState("Punjab", peak))))}%.
    Over the same seasons VIIRS detections fell ${Math.abs(Math.round(pct(seasonTotal("Punjab", last, "snpp"), seasonTotal("Punjab", peak, "snpp"))))}%:
    ${r(last) != null && r(peak) != null ? `<b>${r(peak).toFixed(0)}</b> detections per 1,000 ha burned in ${peak}, <b>${r(last).toFixed(0)}</b> in ${last}.` : ""}
    The burning shrank, and it also moved out of the satellites' sight.`;
}

// ---------------------------------------------------------------- 4c. enforcement and residue management
function setupEnf() {
  const E = D.enforcement;
  if (!E || !E.metrics || !Object.keys(E.metrics).length) return;
  $("#sect-enf").hidden = false;
  const keys = Object.keys(E.metrics).filter((k) => Object.entries(E.metrics[k].by_state || {})
    .some(([st, a]) => !/[+]|combined|total/i.test(st) && (a || []).filter((r) => r.season != null && r.value != null).length));
  const order = ["ec_imposed_rs", "ec_recovered_rs", "ec_cases", "firs", "red_entries", "inspections", "crm_machines_cumulative", "crm_machines_year", "chc_count", "crm_funds_released_rs_cr", "incentive_per_acre_rs", "ex_situ_tonnes"];
  keys.sort((a, b) => ((order.indexOf(a) + 1) || 99) - ((order.indexOf(b) + 1) || 99));
  $("#enf-metric").innerHTML = keys.map((k) => `<option value="${esc(k)}">${esc(E.metrics[k].label || k)}</option>`).join("");
  $("#enf-metric").onchange = renderEnf;
}
const esc = (v) => String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https?:\/\//i.test(String(u || "")) ? esc(u) : "");
const srcOf = (id) => ((D.enforcement || {}).sources || []).find((x) => x.id === id) || null;
function enfSeries(key, st) {   // one value per season: the latest "as of" figure the sources give for it
  const rows = ((D.enforcement.metrics[key] || {}).by_state || {})[st] || [];
  const by = {}, hasLatest = rows.some((r) => r.latest);
  for (const r of rows) {
    if (r.value == null || r.season == null || r.suspect_unit || (r.flags || []).includes("suspect_unit")) continue;
    if (hasLatest && !r.latest) continue;   // the build marks one entry per season as the latest as-of figure
    const prev = by[r.season];
    if (!prev || String(r.as_of || "") > String(prev.as_of || "")) by[r.season] = r;
  }
  return by;
}
function renderEnf() {
  const E = D.enforcement; if (!E || $("#sect-enf").hidden) return;
  const t = th(), key = $("#enf-metric").value, M = E.metrics[key] || {};
  // single states only: combined / multi-state totals are never drawn as if they were a state
  const states = Object.keys(M.by_state || {}).filter((st) => (M.by_state[st] || []).length && !/[+]|combined|total/i.test(st));
  const seasons = [...new Set(states.flatMap((st) => Object.keys(enfSeries(key, st)).map(Number)))].sort();
  $("#t-enf").textContent = `${M.label || key}${M.unit ? ` (${M.unit})` : ""}`;   // textContent: no markup
  $("#n-enf").textContent = M.note || "";
  const isRs = /^rs$/i.test((M.unit || "").trim());
  const inr = (v) => (Math.abs(v) >= 1e7 ? `₹${(v / 1e7).toFixed(v >= 1e9 ? 0 : 1)} cr` : Math.abs(v) >= 1e5 ? `₹${(v / 1e5).toFixed(1)} lakh` : `₹${fmt(v)}`);
  const unitFmt = (v) => (v == null ? "–" : isRs ? inr(v) : fmt(v));
  const trim1 = (x) => x.toFixed(1).replace(/\.0$/, "");
  const axisFmt = (v) => (isRs ? (v >= 1e7 ? `${trim1(v / 1e7)} cr` : v >= 1e5 ? `${trim1(v / 1e5)} L` : fmt(v)) : fmtK(v));
  const series = states.map((st) => { const by = enfSeries(key, st); return {
    name: st, type: "bar", barMaxWidth: 18, itemStyle: { color: STATE_COLORS[st] || t.dim },
    data: seasons.map((y) => (by[y] ? { value: by[y].value, src: by[y].source, asof: by[y].as_of } : null)) }; });
  chart("c-enf").setOption(base({
    tooltip: { ...base().tooltip, formatter: (ps) => `<div class="tt-title">${ps[0].axisValue} season</div>` + ps.filter((p) => p.data).map((p) => {
      const s2 = srcOf(p.data.src); return tipRow(p.color, esc(p.seriesName), unitFmt(p.value)) + `<div class="tt-row"><span style="color:${t.dim};font-size:11px">${p.data.asof ? `as of ${esc(p.data.asof)}, ` : ""}${s2 ? esc((s2.publisher || s2.title || "").slice(0, 48)) : ""}</span><span></span></div>`; }).join("") },
    xAxis: cAxis(seasons.map(String)), yAxis: vAxis({ axisLabel: { color: t.dim, formatter: axisFmt } }), series,
  }), true);
  chart("c-enf").off("click");
  chart("c-enf").on("click", (p) => { const s2 = p.data && srcOf(p.data.src); if (s2 && /^https?:\/\//i.test(s2.url || "")) window.open(s2.url, "_blank", "noopener"); });

  const vs = ["Punjab", "Haryana"].filter((st) => states.includes(st));
  const ys = [...new Set([...seasons, ...basisYears("snpp")])].filter((y) => y >= 2016 && y <= curYear()).sort();
  chart("c-enfvs").setOption(base({
    grid: { left: 56, right: 52, top: 58, bottom: 30 },
    tooltip: { ...base().tooltip, formatter: (ps) => `<div class="tt-title">${ps[0].axisValue}</div>` + ps.map((p) => tipRow(p.color, esc(p.seriesName), p.value == null ? "–" : p.seriesName.endsWith("fires") ? fmt(p.value) : unitFmt(p.value))).join("") },
    xAxis: cAxis(ys.map(String), { axisLabel: { color: t.dim, formatter: (v) => `'${v.slice(2)}` } }),
    yAxis: [vAxis({ axisLabel: { color: t.dim, formatter: axisFmt } }), vAxis({ splitLine: { show: false } })],
    series: [
      ...vs.map((st) => { const by = enfSeries(key, st); return { name: st, type: "bar", barMaxWidth: 14, itemStyle: { color: STATE_COLORS[st] }, data: ys.map((y) => (by[y] ? by[y].value : null)) }; }),
      ...vs.map((st) => ({ name: `${st} VIIRS fires`, type: "line", yAxisIndex: 1, symbolSize: 4, lineStyle: { width: 1.8, type: "dashed", color: STATE_COLORS[st] }, itemStyle: { color: STATE_COLORS[st] },
        data: ys.map((y) => (seasonComplete(y) || y === curYear() ? seasonTotal(st, y, "snpp") : null)) })),
    ],
  }), true);

  const ev = (E.events || []).slice().sort((a, b) => String(b.date).localeCompare(String(a.date)));
  $("#t-events tbody").innerHTML = ev.map((e) => { const s2 = srcOf(e.source), u = s2 && safeUrl(s2.url); return `<tr><td class="name">${esc(e.date)}</td><td class="name">${esc(e.state)}</td>
    <td class="name" style="white-space:normal"><b>${esc(e.title)}</b>${e.detail ? `<div class="cell-sub" style="white-space:normal">${esc(e.detail)}</div>` : ""}</td>
    <td class="name">${u ? `<a href="${u}" target="_blank" rel="noopener">${esc((s2.publisher || "source").slice(0, 30))}</a>` : ""}</td></tr>`; }).join("") ||
    `<tr><td colspan="4" class="name" style="color:var(--dim)">No events recorded.</td></tr>`;
}

// ---------------------------------------------------------------- 5. air quality (optional data)
// Ground PM2.5 (CPCB stations via OpenAQ) is the primary series; the CAMS model (Open-Meteo) fills seasons the
// station archive lacks and is drawn dashed. Meteorology is ERA5. Stubble share: IITM DSS (2023+) and SAFAR bulletins.
const groundCity = (c) => (D.aq_ground && D.aq_ground.cities[c]) || null;
const modelCity = (c) => (D.aq && D.aq.cities[c]) || null;
function cityDaily(C, y, key) {   // season-day array (Sep 15 .. Nov 30) of C.daily[key] for year y
  const out = new Array(D.season_days).fill(null);
  if (!C || !C.daily || !C.daily[key]) return out;
  const dd = C.daily;
  dd.date.forEach((d, i) => {
    if (!d.startsWith(String(y))) return;
    const k = Math.round((Date.parse(d + "T00:00:00Z") - Date.UTC(y, 8, 15)) / 864e5);
    if (k >= 0 && k < D.season_days) out[k] = dd[key][i];
  });
  return out;
}
const hasPM = (C, y) => cityDaily(C, y, "pm25").some((v) => v != null);
function setupAir() {
  if (!D.aq && !D.aq_ground) { $("#sect-air").hidden = true; return; }
  const cities = [...new Set([...Object.keys((D.aq_ground || {}).cities || {}), ...Object.keys((D.aq || {}).cities || {})])]
    .sort((a, b) => (a === "Delhi" ? -1 : b === "Delhi" ? 1 : a.localeCompare(b)));
  $("#air-city").innerHTML = cities.map((c) => `<option>${c}</option>`).join("");
  $("#air-city").value = "Delhi";
  const fillYears = () => {
    const c = $("#air-city").value;
    const ys = D.years.filter((y) => hasPM(groundCity(c), y) || hasPM(modelCity(c), y)).sort((a, b) => b - a);
    const keep = parseInt($("#air-year").value, 10);
    $("#air-year").innerHTML = ys.map((y) => `<option value="${y}">${y}${seasonComplete(y) ? "" : " (to date)"}${hasPM(groundCity(c), y) ? "" : " (model only)"}</option>`).join("");
    $("#air-year").value = String(ys.includes(keep) ? keep : (ys.find((y) => seasonComplete(y) && hasPM(groundCity(c), y)) || ys[0]));
  };
  fillYears();
  $("#air-city").onchange = () => { fillYears(); renderAir(); };
  $("#air-year").onchange = renderAir;
  const g = D.aq_ground ? Object.keys(D.aq_ground.cities).length : 0;
  $("#air-lede").innerHTML = `Daily PM2.5 at ${g} receptor cities from CPCB's continuous monitors (via OpenAQ, ${g ? "2016 onward" : ""}), with the
    CAMS atmospheric model dashed for comparison and for seasons the station archive lacks (OpenAQ has no CPCB data from late 2022 to
    February 2025). For Delhi, IITM's Decision Support System and SAFAR publish how much of the city's PM2.5 their models attribute to
    stubble burning.`;
}
function renderAir() {
  const t = th(), city = $("#air-city").value, y = parseInt($("#air-year").value, 10);
  const G = groundCity(city), Mo = modelCity(city);
  const pmG = cityDaily(G, y, "pm25"), pmM = cityDaily(Mo, y, "pm25"), nst = cityDaily(G, y, "n_stations");
  const fires = (D.calendar.snpp[y] || D.calendar.modis[y] || []).slice();
  $("#t-air").textContent = `${city}: daily PM2.5 and Punjab + Haryana detections, ${y}`;
  const days = Array.from({ length: D.season_days }, (_, k) => seasonDayLabel(k));
  chart("c-air").setOption(base({
    grid: { left: 52, right: 56, top: 36, bottom: 30 },
    tooltip: { ...base().tooltip, formatter: (ps) => `<div class="tt-title">${ps[0].axisValue} ${y}</div>` + ps.map((p) => tipRow(p.color, p.seriesName, p.value == null ? "–" : fmt(p.value))).join("") +
      (nst[ps[0].dataIndex] ? tipRow("transparent", "stations reporting", nst[ps[0].dataIndex]) : "") },
    xAxis: cAxis(days, { axisLabel: { color: t.dim, interval: 7 } }),
    yAxis: [vAxis({ name: "PM2.5 µg/m³" }), vAxis({ name: "detections", splitLine: { show: false } })],
    series: [
      { name: `${D.calendar.snpp[y] ? "VIIRS" : "MODIS"} detections, Punjab + Haryana`, type: "bar", yAxisIndex: 1, barMaxWidth: 8, itemStyle: { color: HEAT[2], opacity: 0.65 }, data: fires },
      ...(pmG.some((v) => v != null) ? [{ name: `${city} PM2.5, CPCB monitors`, type: "line", smooth: 0.15, symbol: "none", connectNulls: false, lineStyle: { width: 2.3, color: "#d63fa6" }, itemStyle: { color: "#d63fa6" }, data: pmG,
        markLine: { silent: true, symbol: "none", lineStyle: { color: t.dim, type: "dashed" }, label: { color: t.dim, position: "insideStartTop", formatter: "WHO 24 h guideline, 15" }, data: [{ yAxis: 15 }] } }] : []),
      ...(pmM.some((v) => v != null) ? [{ name: `${city} PM2.5, CAMS model`, type: "line", smooth: 0.2, symbol: "none", lineStyle: { width: 1.5, type: "dashed", color: t.dim }, itemStyle: { color: t.dim }, data: pmM }] : []),
    ],
  }), true);

  // stubble share of Delhi's PM2.5, this season
  const S = (D.stubble || {}).series || {};
  const onSeason = (ser) => { const out = new Array(D.season_days).fill(null); if (!ser) return out;
    ser.date.forEach((d, i) => { if (!d.startsWith(String(y))) return; const k = Math.round((Date.parse(d + "T00:00:00Z") - Date.UTC(y, 8, 15)) / 864e5); if (k >= 0 && k < D.season_days && ser.share_pct[i] != null) out[k] = ser.share_pct[i]; }); return out; };
  const dss = onSeason(S.IITM_DSS), safar = onSeason(S.SAFAR);
  const anyShare = dss.some((v) => v != null) || safar.some((v) => v != null);
  $("#t-stubble").textContent = `Stubble burning's modelled share of Delhi's PM2.5, ${y}`;
  $("#n-stubble").textContent = anyShare ? "" : `No published daily share archived for ${y}; IITM DSS daily values exist for 2023 onward, SAFAR bulletins for scattered days in 2019-2022.`;
  chart("c-stubble").setOption(base({
    grid: { left: 52, right: 56, top: 36, bottom: 30 },
    tooltip: { ...base().tooltip, valueFormatter: (v) => (v == null ? "–" : `${(+v).toFixed(1)}`) },
    xAxis: cAxis(days, { axisLabel: { color: t.dim, interval: 7 } }),
    yAxis: [vAxis({ max: 60, axisLabel: { color: t.dim, formatter: (v) => `${v}%` } }), vAxis({ name: "detections", splitLine: { show: false } })],
    series: [
      { name: "Detections, Punjab + Haryana", type: "bar", yAxisIndex: 1, barMaxWidth: 8, itemStyle: { color: HEAT[2], opacity: 0.45 }, data: fires },
      { name: "IITM DSS: stubble share (%)", type: "line", smooth: 0.15, symbolSize: 4, connectNulls: false, lineStyle: { width: 2.3, color: t.warn }, itemStyle: { color: t.warn }, data: dss },
      { name: "SAFAR bulletin (%)", type: "scatter", symbol: "diamond", symbolSize: 10, itemStyle: { color: t.accent }, data: safar },
    ],
  }), true);

  // season association, one dot per season: 15 Oct - 15 Nov mean PM2.5 against detections over the same days
  const W0 = 30, W1 = 62;
  const yearsAll = Object.keys(D.calendar.snpp).map(Number).filter(seasonComplete);
  const dot = (src, yy) => { const w = cityDaily(src, yy, "pm25").slice(W0, W1); if (w.filter((v) => v != null).length < 20) return null; const p = mean(w); const f = sum((D.calendar.snpp[yy] || []).slice(W0, W1)); return p != null ? { value: [f, p], name: String(yy) } : null; };
  const ptsG = yearsAll.map((yy) => dot(G, yy)).filter(Boolean);
  const ptsM = yearsAll.filter((yy) => !dot(G, yy)).map((yy) => dot(Mo, yy)).filter(Boolean);
  const lab = { show: true, position: "right", color: t.dim, fontSize: 10.5, formatter: (p) => `'${p.name.slice(2)}` };
  chart("c-airscatter").setOption(base({
    grid: { left: 52, right: 24, top: 36, bottom: 44 },
    tooltip: { trigger: "item", backgroundColor: t.panel, borderColor: t.border, textStyle: { color: t.text, fontSize: 12 }, formatter: (p) => `<div class="tt-title">${p.name}, ${p.seriesName}</div>${tipRow("transparent", "detections", fmt(p.value[0]))}${tipRow("transparent", "mean PM2.5", `${fmt(p.value[1])} µg/m³`)}` },
    xAxis: vAxis({ name: "VIIRS detections, 15 Oct - 15 Nov", nameLocation: "middle", nameGap: 26 }),
    yAxis: vAxis({ name: `${city} PM2.5` }),
    series: [
      { name: "CPCB monitors", type: "scatter", symbolSize: 11, itemStyle: { color: "#d63fa6", opacity: 0.9 }, label: lab, data: ptsG },
      { name: "CAMS model", type: "scatter", symbol: "diamond", symbolSize: 11, itemStyle: { color: "transparent", borderColor: t.dim, borderWidth: 1.5 }, label: lab, data: ptsM },
    ],
  }), true);

  if (D.aq) {
    const cities = Object.keys(D.aq.cities);
    const blhYears = [...new Set(D.aq.cities[cities[0]].daily.date.map((d) => +d.slice(0, 4)))].filter((yy) => seasonComplete(yy)).sort();
    chart("c-blh").setOption(base({
      tooltip: { ...base().tooltip, valueFormatter: (v) => (v == null ? "–" : `${fmt(v)} m`) },
      xAxis: cAxis(blhYears.map(String), { boundaryGap: false, axisLabel: { color: t.dim, formatter: (v) => `'${v.slice(2)}` } }),
      yAxis: vAxis({ name: "metres", scale: true }),
      series: ["Delhi", "Ludhiana", "Lucknow"].filter((c) => D.aq.cities[c]).map((c, i) => ({ name: c, type: "line", smooth: 0.2, symbolSize: 4,
        lineStyle: { width: 2, color: ["#d63fa6", "#e8604c", "#4ea8de"][i] }, itemStyle: { color: ["#d63fa6", "#e8604c", "#4ea8de"][i] },
        data: blhYears.map((yy) => mean(cityDaily(D.aq.cities[c], yy, "blh14").slice(W0, W1))) })),
    }), true);
  }
}

// ---------------------------------------------------------------- notes
function renderNotes() {
  const r = "Kharif rice area and production: DA&FW / DES via UPAg (district APY, final estimates; state-wise official APY), as compiled for agri.reclaimchennai.city. Milled rice, agricultural year starting in the fire season's year.";
  const notes = [
    ["Fire detections", "NASA FIRMS active fires: MODIS Terra and Aqua (1 km, 2003 onward) and VIIRS S-NPP (375 m, 2012 onward), from the public yearly country files and the FIRMS area API. Industrial heat sources are removed. Counts are per sensor; nothing is merged across satellites."],
    ["What a detection is", "A pixel that was burning when a satellite passed. Polar orbiters pass at about 10:30, 13:30 and 01:30 IST; cloud, smoke and short fires lit between passes go unseen. Treat counts as an index of burning, not a census of fields."],
    ["Official counts", "ICAR-IARI CREAMS daily bulletins, parsed from the PDFs on creams.iari.res.in. CREAMS counts from 15 September (1 October before 2020) with its own VIIRS protocol, so its numbers are lower than raw FIRMS."],
    ["Paddy area", `${r} The latest APY year can lag the fire season by a year; per-hectare figures use the latest year available.`],
    ["Burned area", "NASA MODIS MCD64A1 collection 6.1 (500 m, monthly burn date), read from Microsoft Planetary Computer, 15 Sep - 5 Dec, clipped to each district. It is NASA's validated global product, but at 500 m it misses many of Punjab's 1-2 ha fields and short burns, so its hectares understate the true area. Some months are missing from the archive (Sep 2023, Sep-Oct 2025); those seasons are shown faded and left out of ratios. A field-scale Sentinel-2 classifier was tested and rejected: it could not tell burned from merely harvested paddy."],
    ["Enforcement", "Punjab Pollution Control Board action-taken reports to CAQM, Haryana's enforcement dashboard, CAQM directions and press releases, NGT affidavits, and Lok Sabha / Rajya Sabha answers (some via data.gov.in), parsed from the documents; every value carries its source, and figures quoted from prose are checked against the cached text. Season figures are cumulative to the date shown; where several reports cover a season, the latest is plotted. A 2022 compensation table with an implausible unit is left out. There are no official end-of-season Punjab totals for 2023 or 2024, and no Haryana compensation or FIR totals for 2021-2025."],
    ["Districts", "geoBoundaries district polygons (India: LGD 2021). Districts created after 2021 are counted inside their parent polygon. Pakistan's districts are shown for comparison; no paddy area is available for them here."],
    ["Air quality", "Ground PM2.5: CPCB continuous ambient air-quality stations via the OpenAQ v3 API, daily means kept only with 18+ hours of data, city value = mean of stations reporting (Delhi grew from 6 stations in 2016 to about 40). OpenAQ holds no CPCB data from late 2022 to February 2025. Model PM2.5: CAMS via Open-Meteo, from August 2022, good at day-to-day swings but biased low in absolute level. Weather moves PM2.5 as much as fires do; the scatter is an association, not an attribution."],
    ["Stubble share", "IITM Pune's Decision Support System for Delhi (2023 onward, daily model mean, from the live page and Wayback Machine captures) and SAFAR's daily bulletin (2019-2022, scattered days from archived captures, dates may be off by one). Both are model attributions, from different models; they are not spliced into one series."],
  ];
  $("#notes").innerHTML = notes.map(([h, b]) => `<div class="note"><b>${h}</b>${b}</div>`).join("");
  $("#built").textContent = `${D.generated}, fires to ${D.last_detection_date}`;
}

// ---------------------------------------------------------------- orchestration
function renderAll() {
  renderHeader(); renderTotals(); renderTiming(); renderMap(); renderScatter(); renderTable(); renderPerHa(); renderBurn(); renderEnf(); renderAir(); renderNotes();
}
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
    renderAll();
  };
  document.querySelectorAll("#seg-basis button").forEach((b) => b.onclick = () => { basis = b.dataset.v; press("#seg-basis", basis); renderAll(); });
  document.querySelectorAll("#seg-totals button").forEach((b) => b.onclick = () => { totalsMode = b.dataset.v; press("#seg-totals", totalsMode); renderTotals(); });
  document.querySelectorAll("#seg-mapmetric button").forEach((b) => b.onclick = () => { mapMetric = b.dataset.v; press("#seg-mapmetric", mapMetric); renderMap(); });
  $("#map-year").oninput = (e) => { mapYear = +e.target.value; renderMap(); };
  $("#map-play").onclick = () => {
    if (mapTimer) { clearInterval(mapTimer); mapTimer = null; $("#map-play").innerHTML = icon("play"); return; }
    $("#map-play").innerHTML = icon("pause");
    const ys = basisYears().filter((y) => D.districts[basis][y]);
    if (mapYear >= ys.at(-1)) mapYear = ys[0] - 1;
    mapTimer = setInterval(() => {
      mapYear++; if (mapYear > ys.at(-1)) { clearInterval(mapTimer); mapTimer = null; $("#map-play").innerHTML = icon("play"); mapYear = ys.at(-1); }
      renderMap();
    }, 900);
  };
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => Object.values(charts).forEach((c) => c.resize()), 120); });
}

async function boot() {
  bind();
  press("#seg-basis", basis);
  const [d, geo] = await Promise.all([window.__boot.db, fetch("../data/geo/districts.geojson").then((r) => r.json())]);
  D = d; GEO = geo;
  buildRiceIndex();
  MAP_FEATS = registerMap();
  mapYear = seasonComplete(curYear()) ? curYear() : curYear() - 1;
  setupAir();
  setupEnf();
  renderAll();
}
boot().catch((e) => { console.error(e); $("#lede").textContent = `Could not load the data: ${e.message}`; });
})();
