/* Stubble Fire Watch, /db: crop greenness (NDVI) against Aqua/MODIS fire counts, refit on every refresh.
 * Reads ../data/db/ndvi_fire.json (scripts/build_ndvi_fire.py); draws with ECharts. Self-contained, like delhi.js. */
(() => {
"use strict";

const $ = (s) => document.querySelector(s);
const nf = new Intl.NumberFormat("en-IN");
const fmt = (v) => (v == null || isNaN(v) ? "–" : nf.format(Math.round(v)));
const cssv = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const STAR = "path://M12 2l2.9 6.9 7.1.6-5.4 4.7 1.6 7.3L12 17.8 5.8 21.5l1.6-7.3L2 9.5l7.1-.6z";
const C = { fit: "#8a8f98", shift: "#f2683c", cur: "#f2b33d", line: "#d63b48" };

let D = null;
const charts = {};

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
      textStyle: { color: t.text, fontSize: 12, fontFamily: "Inter, system-ui, sans-serif" } },
    legend: { top: 4, left: "center", icon: "roundRect", itemWidth: 10, itemHeight: 10, itemGap: 12, textStyle: { color: t.text2, fontSize: 11.5 }, inactiveColor: t.axis },
    ...extra,
  };
}
const vAxis = (o = {}) => { const t = th(); return { type: "value", axisLabel: { color: t.dim }, splitLine: { lineStyle: { color: t.grid } }, axisLine: { show: false }, axisTick: { show: false }, nameTextStyle: { color: t.dim, fontSize: 11 }, ...o }; };
const cAxis = (data, o = {}) => { const t = th(); return { type: "category", data, axisLabel: { color: t.dim }, axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false }, ...o }; };
function chart(id) { if (!charts[id]) charts[id] = echarts.init(document.getElementById(id), null, { renderer: "canvas" }); return charts[id]; }
const tipRow = (color, label, value) => `<div class="tt-row"><span><span class="swatch" style="background:${color}"></span>${label}</span><span>${value}</span></div>`;
const seasonDay = (y, k) => { const d = new Date(Date.UTC(y, 9, 1) + k * 864e5); return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`; };
const shifted = (r) => r.year > D.fit_years[1];
const cur = () => D.years.find((r) => r.year === D.current_year);

// ---------------------------------------------------------------- charts
function scatter() {
  const t = th(), f = D.fit, c = cur(), narrow = window.innerWidth < 640;
  const xs = D.years.map((r) => r.ndvi), x0 = Math.floor((Math.min(...xs) - 0.01) * 100) / 100, x1 = Math.ceil((Math.max(...xs) + 0.01) * 100) / 100;
  const pi = (x) => f.t975 * f.se * Math.sqrt(1 + 1 / f.n + (x - f.x_mean) ** 2 / f.sxx);
  const line = [], lo = [], hi = [];
  for (let i = 0; i <= 40; i++) { const x = x0 + (i / 40) * (x1 - x0), y = f.intercept + f.slope * x; line.push([x, y]); lo.push([x, y - pi(x)]); hi.push([x, y + pi(x)]); }
  const pt = (r) => ({ value: [r.ndvi, r.season], r });
  const lab = (pos) => ({ show: !narrow, formatter: (p) => p.data.r.year, position: pos, color: t.text2, fontSize: 10.5 });
  const series = [
    { name: "fit", type: "line", data: line, showSymbol: false, silent: true, lineStyle: { color: C.line, width: 1.6, type: [2, 4] }, z: 1 },
    { name: "95% band", type: "line", data: lo, showSymbol: false, silent: true, lineStyle: { color: C.line, width: 0.8, opacity: 0.5 }, z: 1 },
    { name: "95% band hi", type: "line", data: hi, showSymbol: false, silent: true, lineStyle: { color: C.line, width: 0.8, opacity: 0.5 }, z: 1 },
    { name: `Fit seasons ${D.fit_years[0]}–${D.fit_years[1]}`, type: "scatter", symbolSize: 7, itemStyle: { color: C.fit }, label: lab("right"),
      data: D.years.filter((r) => !shifted(r) && r.complete).map(pt) },
    { name: `${D.fit_years[1] + 1} onward`, type: "scatter", symbol: STAR, symbolSize: 15, itemStyle: { color: C.shift }, label: { ...lab("right"), color: C.shift, fontWeight: 600 },
      data: D.years.filter((r) => shifted(r) && r.complete).map(pt) },
  ];
  if (c) {
    series.push({ name: `${c.year} predicted`, type: "scatter", symbolSize: 16, itemStyle: { color: "transparent", borderColor: C.cur, borderWidth: 2.5 },
      label: { show: false },
      data: [{ value: [c.ndvi, c.pred], r: c, kind: "pred" }],
      markLine: { silent: true, symbol: "none", lineStyle: { color: C.cur, width: 1.5 }, label: { show: false }, data: [[{ coord: [c.ndvi, c.pred_lo] }, { coord: [c.ndvi, c.pred_hi] }]] } });
    if (!c.complete) series.push({ name: `${c.year} so far`, type: "scatter", symbol: STAR, symbolSize: 15, itemStyle: { color: C.cur },
      label: { show: true, formatter: `${c.year} to ${seasonDay(c.year, D.days_observed - 1)}`, position: "right", color: C.cur, fontWeight: 600, fontSize: 11 },
      data: [{ value: [c.ndvi, c.to_date], r: c, kind: "now" }] });
  }
  chart("c-ndvisc").setOption(base({
    grid: { left: 8, right: narrow ? 14 : 30, top: 40, bottom: 30, containLabel: true },
    legend: { ...base().legend, data: series.filter((s) => s.type === "scatter").map((s) => s.name) },
    tooltip: { ...base().tooltip, trigger: "item", formatter: (p) => {
      const r = p.data.r; if (!r) return "";
      const head = `<div class="tt-title">${r.year} · NDVI ${r.ndvi.toFixed(3)}</div>`;
      if (p.data.kind === "now") return head + tipRow(C.cur, `detections, 1 Oct – ${seasonDay(r.year, D.days_observed - 1)}`, fmt(r.to_date)) + tipRow("transparent", "predicted by now", `${fmt(r.pred_to_date)} (${fmt(r.pred_to_date_lo)}–${fmt(r.pred_to_date_hi)})`);
      return head + (r.season != null ? tipRow(p.color, "Oct–Nov detections", fmt(r.season)) : "") + tipRow("transparent", "predicted", `${fmt(r.pred)} (${fmt(r.pred_lo)}–${fmt(r.pred_hi)})`) +
        (r.ratio != null ? tipRow("transparent", "seen ÷ predicted", `${Math.round(r.ratio * 100)}%`) : "");
    } },
    xAxis: vAxis({ min: x0, max: x1, name: "NDVI, 6–21 Sep (Aqua MODIS 16-day)", nameLocation: "middle", nameGap: 24, axisLabel: { color: t.dim, formatter: (v) => v.toFixed(2) } }),
    yAxis: vAxis({ min: 0, axisLabel: { color: t.dim, formatter: (v) => (v >= 1000 ? `${v / 1000}k` : v) } }),
    series,
  }), true);
}

function cumulative() {
  const t = th(), c = cur();
  if (!c) return;
  const days = D.typical_share.map((_, k) => seasonDay(c.year, k)), sh = D.typical_share, k = D.days_observed;
  const path = sh.map((s) => Math.round(s * c.pred)), lo = sh.map((s) => Math.round(s * Math.max(c.pred_lo, 0))), band = sh.map((s, i) => Math.round(s * c.pred_hi) - lo[i]);
  const past = D.years.filter((r) => shifted(r) && r.complete).slice(-3);
  const series = [
    { name: "band lo", type: "line", data: lo, stack: "band", showSymbol: false, silent: true, lineStyle: { opacity: 0 }, tooltip: { show: false } },
    { name: "95% range", type: "line", data: band, stack: "band", showSymbol: false, silent: true, lineStyle: { opacity: 0 }, areaStyle: { color: C.line, opacity: 0.1 }, tooltip: { show: false } },
    { name: `${c.year} predicted from NDVI`, type: "line", data: path, showSymbol: false, lineStyle: { color: C.line, width: 1.6, type: [4, 3] }, itemStyle: { color: C.line } },
    ...past.map((r, i) => ({ name: String(r.year), type: "line", data: r.cum, showSymbol: false, lineStyle: { color: C.shift, width: 1.2, opacity: 0.35 + 0.2 * i }, itemStyle: { color: C.shift } })),
    { name: `${c.year} seen`, type: "line", data: c.cum.slice(0, k), showSymbol: false, lineStyle: { color: C.cur, width: 2.6 }, itemStyle: { color: C.cur } },
  ];
  chart("c-ndvicum").setOption(base({
    grid: { left: 8, right: 14, top: document.getElementById("c-ndvicum").clientWidth < 560 ? 58 : 40, bottom: 24, containLabel: true },
    legend: { ...base().legend, data: series.filter((s) => !s.name.startsWith("band") && s.name !== "95% range").map((s) => s.name) },
    tooltip: { ...base().tooltip, axisPointer: { type: "line", lineStyle: { color: t.axis } }, formatter: (ps) => `<div class="tt-title">by ${ps[0].axisValue}</div>` +
      ps.filter((p) => !p.seriesName.startsWith("band") && p.seriesName !== "95% range" && p.value != null).map((p) => tipRow(p.color, p.seriesName, fmt(p.value))).join("") },
    xAxis: cAxis(days, { boundaryGap: false, axisLabel: { color: t.dim, interval: (i) => [0, 14, 31, 45, 60].includes(i) } }),
    yAxis: vAxis({ axisLabel: { color: t.dim, formatter: (v) => (v >= 1000 ? `${v / 1000}k` : v) } }),
    series,
  }), true);
}

function ratio() {
  const t = th(), rows = D.years.filter((r) => r.complete || r.ratio_to_date != null);
  const val = (r) => (r.complete ? r.ratio : r.ratio_to_date);
  chart("c-ndviratio").setOption(base({
    legend: { show: false },
    tooltip: { ...base().tooltip, axisPointer: { type: "shadow", shadowStyle: { color: t.grid } }, formatter: (ps) => { const r = rows[ps[0].dataIndex]; return `<div class="tt-title">${r.year}</div>` +
      (r.complete ? tipRow(ps[0].color, "seen ÷ predicted", `${Math.round(r.ratio * 100)}%`) + tipRow("transparent", "seen", fmt(r.season)) + tipRow("transparent", "predicted", fmt(r.pred))
        : tipRow(ps[0].color, `to ${seasonDay(r.year, D.days_observed - 1)}, seen ÷ predicted`, `${Math.round(r.ratio_to_date * 100)}%`) + tipRow("transparent", "seen", fmt(r.to_date)) + tipRow("transparent", "predicted by now", fmt(r.pred_to_date))); } },
    xAxis: cAxis(rows.map((r) => String(r.year)), { axisLabel: { color: t.dim, interval: window.innerWidth < 640 ? 3 : 1 } }),
    yAxis: vAxis({ axisLabel: { color: t.dim, formatter: (v) => `${Math.round(v * 100)}%` } }),
    series: [{ type: "bar", barMaxWidth: 18, data: rows.map((r) => ({ value: val(r), itemStyle: { color: r.complete ? (shifted(r) ? C.shift : C.fit) : C.cur, opacity: r.complete ? 1 : 0.8 } })),
      markLine: { silent: true, symbol: "none", lineStyle: { color: t.dim, type: [3, 3] }, label: { formatter: "as predicted", color: t.dim, fontSize: 10.5, position: "insideEndTop" }, data: [{ yAxis: 1 }] } }],
  }), true);
}

function info() {
  const c = cur(), f = D.fit;
  const sign = f.intercept < 0 ? "−" : "+";
  const kv = (k, v, full) => `<div${full ? ' class="full"' : ""}><span class="k">${k}</span><span class="v">${v}</span></div>`;
  $("#ndvi-info").innerHTML = [
    c ? kv(`${c.year} NDVI, 6–21 Sep`, c.ndvi.toFixed(3)) : "",
    c ? kv("Predicted Oct–Nov", `${fmt(c.pred)} <small>(${fmt(Math.max(c.pred_lo, 0))}–${fmt(c.pred_hi)})</small>`) : "",
    c && !c.complete ? kv(`Seen, 1 Oct – ${seasonDay(c.year, D.days_observed - 1)}`, fmt(c.to_date)) : "",
    c && !c.complete ? kv("Predicted by now", `${fmt(c.pred_to_date)} <small>(${fmt(c.pred_to_date_lo)}–${fmt(c.pred_to_date_hi)})</small>`) : "",
    kv(`Fit, ${D.fit_years[0]}–${D.fit_years[1]}`, `y = ${fmt(f.slope)}·NDVI ${sign} ${fmt(Math.abs(f.intercept))}`, true),
    kv("R²", f.r2.toFixed(2)), kv("Standard error", fmt(f.se)),
  ].join("");
  const done = D.years.filter((r) => shifted(r) && r.complete);
  const pct = (v) => `${Math.round(v * 100)}%`;
  let lede = `In ${D.fit_years[0]}–${D.fit_years[1]} a greener paddy crop in early September meant more fires seen by Aqua in October and November (R² ${f.r2.toFixed(2)}).`;
  if (done.length) lede += ` Since ${done[0].year} the satellite has seen ${pct(Math.min(...done.map((r) => r.ratio)))}–${pct(Math.max(...done.map((r) => r.ratio)))} of what the crop predicts: much of the burning now happens after its 13:30 pass.`;
  if (c && !c.complete && c.ratio_to_date != null) lede += ` ${c.year} so far: ${fmt(c.to_date)} detections against ${fmt(c.pred_to_date)} expected by ${seasonDay(c.year, D.days_observed - 1)} (${pct(c.ratio_to_date)}).`;
  $("#ndvi-lede").textContent = lede;
  $("#ndvi-src").innerHTML = `Updated ${D.generated.replace("T", " ")} · ` + D.sources.map((s) => `<a href="${s.url}" rel="noopener">${s.name}</a>`).join(" · ");
}

function render() {
  if (!D) return;
  scatter(); cumulative(); ratio(); info();
}

async function boot() {
  const sect = $("#sect-ndvi");
  if (!sect) return;
  D = await fetch("../data/db/ndvi_fire.json").then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (!D || !D.years || !D.years.length) return;
  sect.hidden = false;
  render();
  let w = window.innerWidth;
  window.addEventListener("resize", () => { Object.values(charts).forEach((c) => c.resize()); if ((w < 640) !== (window.innerWidth < 640)) { w = window.innerWidth; render(); } });
  new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
