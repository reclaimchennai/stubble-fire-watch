/* Stubble Fire Watch, /timeline: the season since 1 September, animated every three hours.
 * Data: ../data/timeline/ (scripts/build_timeline.py): GEOS-FP smoke + 10 m wind atlases (8 days each), fire records
 * by month (FIRMS, CREAMS, Meteosat, INSAT-3DS), CPCB daily AQI. Smoke and fires are MapLibre custom WebGL layers;
 * wind particles are a canvas source. The basemap fades between light and dark with the sun over central India. */
(() => {
"use strict";

const $ = (s) => document.querySelector(s);
const HOUR = 3600e3, DAY = 864e5;
const SEC_PER_DAY = 4;                 // playback speed: one day every four seconds
const IST = 5.5 * HOUR;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const SENS = [
  { label: "Terra", sub: "MODIS", color: "#f6a623", fade: 12, size: 3.4, ring: 0 },
  { label: "Aqua", sub: "MODIS", color: "#ffd166", fade: 12, size: 3.4, ring: 0 },
  { label: "S-NPP", sub: "VIIRS", color: "#ff6b35", fade: 12, size: 2.6, ring: 0 },
  { label: "NOAA-20", sub: "VIIRS", color: "#ff3d6e", fade: 12, size: 2.6, ring: 0 },
  { label: "NOAA-21", sub: "VIIRS", color: "#d36cf2", fade: 12, size: 2.6, ring: 0 },
  { label: "CREAMS", sub: "events", color: "#46c08a", fade: 12, size: 2.8, ring: 0 },
  { label: "Meteosat", sub: "15 min", color: "#60a5fa", fade: 1, size: 7, ring: 1 },
  { label: "INSAT-3DS", sub: "30 min", color: "#22d3ee", fade: 1.5, size: 8, ring: 1 },
];
const SMOKE_STOPS = [
  [0.00, 255, 255, 255, 0.00], [0.06, 238, 232, 238, 0.00], [0.12, 236, 222, 232, 0.32],
  [0.25, 240, 190, 220, 0.55], [0.40, 236, 130, 200, 0.70], [0.60, 214, 72, 178, 0.80],
  [0.90, 160, 34, 160, 0.86], [1.30, 98, 16, 128, 0.90], [2.00, 52, 6, 84, 0.94],
];
const SMOKE_TICKS = [0, 0.1, 0.25, 0.5, 1, 2];
const AQI_CATS = [
  { k: "Good", r: "0–50", bg: "#00b050", fg: "#06240f" }, { k: "Satisfactory", r: "51–100", bg: "#92d050", fg: "#1b2a07" },
  { k: "Moderate", r: "101–200", bg: "#ffff00", fg: "#2b2a00" }, { k: "Poor", r: "201–300", bg: "#ff9900", fg: "#2e1600" },
  { k: "Very Poor", r: "301–400", bg: "#ff0000", fg: "#ffffff" }, { k: "Severe", r: "401–500", bg: "#c00000", fg: "#ffffff" },
];
const ESRI = "https://services.arcgisonline.com/arcgis/rest/services";
const TILES = {
  baseL: `${ESRI}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`, baseD: `${ESRI}/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
  refL: `${ESRI}/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, refD: `${ESRI}/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`,
};
const DEM_TILES = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";
// two ends of the day-night fade
const LOOK = {
  day: { bg: [232, 235, 239], line: [31, 35, 41], wind: [30, 40, 60, 0.5], hsS: [74, 79, 87], hsH: [255, 255, 255], hsA: [125, 130, 139] },
  night: { bg: [5, 7, 10], line: [236, 239, 244], wind: [235, 240, 248, 0.72], hsS: [0, 0, 0], hsH: [89, 102, 122], hsA: [17, 20, 26] },
};

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const lerp = (a, b, t) => a + (b - a) * t;
const mix = (A, B, t) => A.map((v, i) => lerp(v, B[i], t));
const rgb = (c) => `rgb(${c.slice(0, 3).map(Math.round).join(",")})`;
const rgba = (c) => `rgba(${c.slice(0, 3).map(Math.round).join(",")},${c[3].toFixed(3)})`;
const hex3 = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
const mercY = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
const mercN = (lon, lat) => [(lon + 180) / 360, 0.5 - mercY(lat) / (2 * Math.PI)];
const smoothstep = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } } };

let mapReady = false;
let M, map, T0, T1, F0, t, playing = true, lastTs = 0, holdUntil = 0, night = -1, startAt;
const on = SENS.map(() => true);

// ---------------------------------------------------------------- sun
function sunElevation(ms, lon = 80, lat = 23) {
  const d = ms / DAY - 10957.5, rad = Math.PI / 180;
  const g = (357.529 + 0.98560028 * d) * rad, q = 280.459 + 0.98564736 * d;
  const L = (q + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * rad, e = (23.439 - 3.6e-7 * d) * rad;
  const dec = Math.asin(Math.sin(e) * Math.sin(L)), ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L));
  const gmst = ((18.697374558 + 24.06570982441908 * d) % 24 + 24) % 24;
  const H = (gmst * 15 + lon) * rad - ra;
  return Math.asin(Math.sin(lat * rad) * Math.sin(dec) + Math.cos(lat * rad) * Math.cos(dec) * Math.cos(H)) / rad;
}

// ---------------------------------------------------------------- GL helpers
function program(gl, vs, fs) {
  const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
  const p = gl.createProgram();
  gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  return p;
}
// MapLibre 5: mainMatrix takes 0-1 Mercator coordinates (modelViewProjectionMatrix is in world pixels)
const matrixOf = (args) => (args.defaultProjectionData && args.defaultProjectionData.mainMatrix) || args;

function rampPixels(n = 256) {
  const out = new Uint8Array(n * 4), max = SMOKE_STOPS[SMOKE_STOPS.length - 1][0];
  for (let i = 0; i < n; i++) {
    const v = (i / (n - 1)) ** 2 * max;
    let k = 0; while (k < SMOKE_STOPS.length - 2 && SMOKE_STOPS[k + 1][0] < v) k++;
    const a = SMOKE_STOPS[k], b = SMOKE_STOPS[k + 1], f = clamp((v - a[0]) / (b[0] - a[0]), 0, 1);
    for (let c = 0; c < 4; c++) out[i * 4 + c] = Math.round((a[c + 1] + (b[c + 1] - a[c + 1]) * f) * (c === 3 ? 255 : 1));
  }
  return out;
}

// ---------------------------------------------------------------- smoke + wind data (8-day atlases)
const chunks = new Map();   // index -> {tex, px (Uint8ClampedArray), w, h} | Promise
const dead = [];            // textures to free on the GL thread
function chunkOf(f) { const A = M.aer; return clamp(Math.floor(f / (A.chunks[0] ? A.chunks[0].n || 64 : 64)), 0, A.chunks.length - 1); }
function loadChunk(c) {
  if (chunks.has(c)) return chunks.get(c);
  const meta = M.aer.chunks[c];
  const p = fetch(`../data/timeline/${meta.file}?v=${encodeURIComponent(M.aer.run)}`).then((r) => r.blob())
    .then((b) => createImageBitmap(b, { premultiplyAlpha: "none", colorSpaceConversion: "none" }))
    .then((bmp) => {
      const cv = document.createElement("canvas"); cv.width = bmp.width; cv.height = bmp.height;
      const ctx = cv.getContext("2d", { willReadFrequently: true }); ctx.drawImage(bmp, 0, 0);
      const px = ctx.getImageData(0, 0, bmp.width, bmp.height).data;
      bmp.close && bmp.close();
      const o = { tex: null, px, w: cv.width, h: cv.height, f0: meta.f0, n: meta.n };
      chunks.set(c, o);
      // keep three chunks around; drop the farthest (its texture is freed in the layer's prerender)
      const keys = [...chunks.keys()].filter((k) => !(chunks.get(k) instanceof Promise));
      if (keys.length > 3) { const far = keys.sort((a, b) => Math.abs(b - c) - Math.abs(a - c))[0]; const x = chunks.get(far); if (x.tex) dead.push(x.tex); chunks.delete(far); }
      map && map.triggerRepaint();
      return o;
    }).catch((e) => { console.warn("chunk", c, e); chunks.delete(c); });
  chunks.set(c, p);
  return p;
}
const ready = (c) => { const x = chunks.get(c); return x && !(x instanceof Promise) ? x : null; };
const readyTex = (c) => { const x = ready(c); return x && x.tex ? x : null; };
function frameAt(ms) {
  const n = M.aer.frames; if (!n) return null;
  const f = clamp((ms - F0) / (3 * HOUR), 0, n - 1), a = Math.floor(f), b = Math.min(n - 1, a + 1);
  return { a, b, m: f - a };
}

const SMOKE_VS = `attribute vec2 a_merc; attribute vec2 a_grid; uniform mat4 u_matrix; varying vec2 v_grid;
void main() { v_grid = a_grid; gl_Position = u_matrix * vec4(a_merc, 0.0, 1.0); }`;
const SMOKE_FS = `precision highp float;
varying vec2 v_grid;
uniform sampler2D u_A; uniform sampler2D u_B; uniform sampler2D u_ramp;
uniform vec2 u_sizeA; uniform vec2 u_sizeB; uniform vec2 u_tile; uniform float u_cols;
uniform float u_fA; uniform float u_fB; uniform float u_mix; uniform float u_aodMax; uniform float u_rampMax; uniform float u_opacity;
float tap(sampler2D at, vec2 sz, float f, vec2 p) {     // p in texels within the tile (centres at k + 0.5)
  p = clamp(p, vec2(0.5), u_tile - 0.5);
  float r = floor(f / u_cols), k = f - r * u_cols;
  float v = texture2D(at, (vec2(k * u_tile.x, r * u_tile.y) + p) / sz).r;
  return v * v * u_aodMax;
}
vec4 cubic(float v) { vec4 n = vec4(1.0, 2.0, 3.0, 4.0) - v; vec4 s = n * n * n;
  float x = s.x, y = s.y - 4.0 * s.x, z = s.z - 4.0 * s.y + 6.0 * s.x; return vec4(x, y, z, 6.0 - x - y - z) / 6.0; }
float bicubic(sampler2D at, vec2 sz, float f, vec2 g) {  // B-spline from four bilinear taps
  vec2 st = g - 0.5, i = floor(st), fr = st - i;
  vec4 wx = cubic(fr.x), wy = cubic(fr.y);
  vec4 s = vec4(wx.x + wx.y, wx.z + wx.w, wy.x + wy.y, wy.z + wy.w);
  vec4 o = vec4(i.x - 0.5, i.x + 1.5, i.y - 0.5, i.y + 1.5) + vec4(wx.y, wx.w, wy.y, wy.w) / s;
  float sx = s.x / (s.x + s.y), sy = s.z / (s.z + s.w);
  return mix(mix(tap(at, sz, f, vec2(o.y, o.w)), tap(at, sz, f, vec2(o.x, o.w)), sx),
             mix(tap(at, sz, f, vec2(o.y, o.z)), tap(at, sz, f, vec2(o.x, o.z)), sx), sy);
}
void main() {
  vec2 g = v_grid * u_tile;
  float aod = mix(bicubic(u_A, u_sizeA, u_fA, g), bicubic(u_B, u_sizeB, u_fB, g), u_mix);
  vec4 c = texture2D(u_ramp, vec2(sqrt(clamp(aod / u_rampMax, 0.0, 1.0)), 0.5));
  float a = c.a * u_opacity;
  gl_FragColor = vec4(c.rgb * a, a);
}`;

const smokeLayer = {
  id: "smoke", type: "custom", renderingMode: "2d",
  onAdd(m, gl) {
    this.p = program(gl, SMOKE_VS, SMOKE_FS);
    const b = M.aer.bounds, R = 72, v = [];
    for (let j = 0; j <= R; j++) {
      const lat = b[3] - ((b[3] - b[1]) * j) / R, gy = j / R;
      const [x0, y] = mercN(b[0], lat), [x1] = mercN(b[2], lat);
      v.push(x0, y, 0, gy, x1, y, 1, gy);
    }
    this.n = (R + 1) * 2;
    this.buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, this.buf); gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(v), gl.STATIC_DRAW);
    this.ramp = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, this.ramp);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, rampPixels());
    for (const [k, val] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR], [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, val);
    this.u = {}; for (const n of ["u_matrix", "u_A", "u_B", "u_ramp", "u_sizeA", "u_sizeB", "u_tile", "u_cols", "u_fA", "u_fB", "u_mix", "u_aodMax", "u_rampMax", "u_opacity"]) this.u[n] = gl.getUniformLocation(this.p, n);
    this.aM = gl.getAttribLocation(this.p, "a_merc"); this.aG = gl.getAttribLocation(this.p, "a_grid");
  },
  // GL work only inside MapLibre's passes: it caches GL state and resets it after custom layers
  prerender(gl) {
    while (dead.length) gl.deleteTexture(dead.pop());
    for (const x of chunks.values()) {
      if (x instanceof Promise || x.tex) continue;
      const tex = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false); gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, x.w, x.h, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array(x.px.buffer));
      for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR], [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
      x.tex = tex;
    }
  },
  render(gl, args) {
    const fr = frameAt(t); if (!fr) return;
    const A = readyTex(chunkOf(fr.a)), B = readyTex(chunkOf(fr.b)) || A;
    if (!A) return;
    gl.useProgram(this.p);
    gl.uniformMatrix4fv(this.u.u_matrix, false, matrixOf(args));
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, A.tex); gl.uniform1i(this.u.u_A, 0);
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, B.tex); gl.uniform1i(this.u.u_B, 1);
    gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, this.ramp); gl.uniform1i(this.u.u_ramp, 2);
    gl.uniform2f(this.u.u_sizeA, A.w, A.h); gl.uniform2f(this.u.u_sizeB, B.w, B.h);
    gl.uniform2f(this.u.u_tile, M.aer.tile_w, M.aer.tile_h); gl.uniform1f(this.u.u_cols, M.aer.cols);
    gl.uniform1f(this.u.u_fA, fr.a - A.f0); gl.uniform1f(this.u.u_fB, B === A && fr.b - A.f0 >= A.n ? fr.a - A.f0 : fr.b - B.f0);
    gl.uniform1f(this.u.u_mix, fr.m); gl.uniform1f(this.u.u_aodMax, M.aer.aod_max);
    gl.uniform1f(this.u.u_rampMax, SMOKE_STOPS[SMOKE_STOPS.length - 1][0]); gl.uniform1f(this.u.u_opacity, 0.92);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buf);
    gl.enableVertexAttribArray(this.aM); gl.vertexAttribPointer(this.aM, 2, gl.FLOAT, false, 16, 0);
    gl.enableVertexAttribArray(this.aG); gl.vertexAttribPointer(this.aG, 2, gl.FLOAT, false, 16, 8);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, this.n);
    gl.disableVertexAttribArray(this.aM); gl.disableVertexAttribArray(this.aG);
  },
};

// ---------------------------------------------------------------- wind particles (canvas source)
const wind = { cv: null, ctx: null, parts: [], W: 0, H: 0 };
function windAt(lon, lat, fr) {
  const A = M.aer, b = A.bounds, tw = A.tile_w, th = A.tile_h;
  const gx = ((lon - b[0]) / (b[2] - b[0])) * tw - 0.5, gy = ((b[3] - lat) / (b[3] - b[1])) * th - 0.5;
  const x0 = clamp(Math.floor(gx), 0, tw - 2), y0 = clamp(Math.floor(gy), 0, th - 2), fx = clamp(gx - x0, 0, 1), fy = clamp(gy - y0, 0, 1);
  const one = (f) => {
    const C = ready(chunkOf(f)); if (!C) return null;
    const i = f - C.f0, r = Math.floor(i / A.cols), k = i % A.cols, W = C.w, px = C.px;
    const at = (x, y, ch) => px[((r * th + y) * W + k * tw + x) * 4 + ch];
    const bil = (ch) => (at(x0, y0, ch) * (1 - fx) + at(x0 + 1, y0, ch) * fx) * (1 - fy) + (at(x0, y0 + 1, ch) * (1 - fx) + at(x0 + 1, y0 + 1, ch) * fx) * fy;
    const s = (2 * A.wind_max) / 255;
    return [bil(1) * s - A.wind_max, bil(2) * s - A.wind_max];
  };
  const a = one(fr.a); if (!a) return null;
  const c = one(fr.b) || a;
  return [lerp(a[0], c[0], fr.m), lerp(a[1], c[1], fr.m)];
}
function newParticle(randomAge) {
  const b = M.aer.bounds;
  return { lon: b[0] + Math.random() * (b[2] - b[0]), lat: b[1] + Math.random() * (b[3] - b[1]), age: randomAge ? Math.floor(Math.random() * 80) : 0, max: 50 + Math.floor(Math.random() * 50) };
}
function stepWind(col) {
  const ctx = wind.ctx, b = M.aer.bounds, fr = frameAt(t);
  ctx.globalCompositeOperation = "destination-in"; ctx.fillStyle = "rgba(0,0,0,0.9)"; ctx.fillRect(0, 0, wind.W, wind.H);
  ctx.globalCompositeOperation = "source-over";
  if (!fr) return;
  const my0 = mercY(b[1]), my1 = mercY(b[3]);
  const toXY = (lon, lat) => [((lon - b[0]) / (b[2] - b[0])) * wind.W, ((my1 - mercY(lat)) / (my1 - my0)) * wind.H];
  ctx.lineWidth = 1.1; ctx.strokeStyle = col; ctx.beginPath();
  const k = 0.006;
  for (const p of wind.parts) {
    const w = windAt(p.lon, p.lat, fr); if (!w) break;
    const [u, v] = w, [x0, y0] = toXY(p.lon, p.lat);
    p.lon += (u * k) / Math.cos((p.lat * Math.PI) / 180); p.lat += v * k; p.age++;
    if (p.age > p.max || p.lon < b[0] || p.lon > b[2] || p.lat < b[1] || p.lat > b[3] || Math.hypot(u, v) < 0.3) { Object.assign(p, newParticle(false)); continue; }
    const [x1, y1] = toXY(p.lon, p.lat);
    ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
  }
  ctx.stroke();
}

// ---------------------------------------------------------------- fires (custom layer, records sorted by time)
const fire = { buf: null, tarr: null, n: 0, parts: new Map() };
const FIRE_VS = `attribute vec2 a_xy; attribute float a_t; attribute float a_s; attribute float a_f;
uniform mat4 u_matrix; uniform vec4 u_bounds; uniform float u_now; uniform float u_scale;
uniform float u_fade[8]; uniform float u_on[8]; uniform float u_size[8]; uniform float u_ring[8]; uniform vec3 u_col[8];
varying vec4 v_col; varying float v_ring;
void main() {
  int s = int(a_s + 0.5);
  float fade = 1.0, onv = 0.0, size = 1.0, ring = 0.0; vec3 col = vec3(1.0);
  for (int i = 0; i < 8; i++) { if (i == s) { fade = u_fade[i]; onv = u_on[i]; size = u_size[i]; ring = u_ring[i]; col = u_col[i]; } }
  float age = u_now - a_t;
  if (onv < 0.5 || age < 0.0 || age > fade) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); gl_PointSize = 0.0; return; }
  float lon = mix(u_bounds.x, u_bounds.z, a_xy.x), lat = mix(u_bounds.y, u_bounds.w, a_xy.y);
  float my = 0.5 - log(tan(0.7853981634 + radians(lat) * 0.5)) / 6.2831853072;
  gl_Position = u_matrix * vec4((lon + 180.0) / 360.0, my, 0.0, 1.0);
  float k = 1.0 - age / fade;
  gl_PointSize = size * u_scale * (0.85 + 0.07 * a_f / 25.0) * (0.55 + 0.45 * k);
  v_col = vec4(col, ring > 0.5 ? 0.25 + 0.75 * k : 0.15 + 0.85 * k);
  v_ring = ring;
}`;
const FIRE_FS = `precision mediump float; varying vec4 v_col; varying float v_ring;
void main() {
  vec2 p = gl_PointCoord * 2.0 - 1.0; float r = length(p);
  if (r > 1.0) discard;
  vec3 c = v_col.rgb; float a;
  if (v_ring > 0.5) { a = smoothstep(1.0, 0.82, r) * smoothstep(0.42, 0.62, r); }
  else { a = smoothstep(1.0, 0.15, r); c = mix(c, vec3(1.0, 0.96, 0.82), smoothstep(0.45, 0.0, r) * 0.75); }
  a *= v_col.a;
  gl_FragColor = vec4(c * a, a);
}`;
const fireLayer = {
  id: "fires", type: "custom", renderingMode: "2d",
  onAdd(m, gl) {
    this.p = program(gl, FIRE_VS, FIRE_FS);
    this.u = {}; for (const n of ["u_matrix", "u_bounds", "u_now", "u_scale"]) this.u[n] = gl.getUniformLocation(this.p, n);
    for (const n of ["u_fade", "u_on", "u_size", "u_ring", "u_col"]) this.u[n] = gl.getUniformLocation(this.p, `${n}[0]`);
    this.a = Object.fromEntries(["a_xy", "a_t", "a_s", "a_f"].map((n) => [n, gl.getAttribLocation(this.p, n)]));
    fire.buf = gl.createBuffer();
  },
  upload(bytes) { fire.pending = bytes; },
  render(gl, args) {
    if (fire.pending) { gl.bindBuffer(gl.ARRAY_BUFFER, fire.buf); gl.bufferData(gl.ARRAY_BUFFER, fire.pending, gl.STATIC_DRAW); fire.pending = null; fire.live = true; }
    if (!fire.n || !fire.live) return;
    const now = (t - T0) / (M.fires.tstep_s * 1000), maxFade = Math.max(...SENS.map((s, i) => (on[i] ? s.fade : 0))) * 3600 / M.fires.tstep_s;
    const lo = lowerBound(fire.tarr, now - maxFade), hi = lowerBound(fire.tarr, now + 1e-6);
    if (hi <= lo) return;
    gl.useProgram(this.p);
    gl.uniformMatrix4fv(this.u.u_matrix, false, matrixOf(args));
    gl.uniform4f(this.u.u_bounds, ...M.region); gl.uniform1f(this.u.u_now, now);
    const z = map.getZoom();
    gl.uniform1f(this.u.u_scale, Math.min(window.devicePixelRatio || 1, 2) * clamp(Math.pow(1.45, z - 4.2), 0.7, 3.2));
    gl.uniform1fv(this.u.u_fade, SENS.map((s) => (s.fade * 3600) / M.fires.tstep_s));
    gl.uniform1fv(this.u.u_on, on.map((v) => (v ? 1 : 0)));
    gl.uniform1fv(this.u.u_size, SENS.map((s) => s.size));
    gl.uniform1fv(this.u.u_ring, SENS.map((s) => s.ring));
    gl.uniform3fv(this.u.u_col, SENS.flatMap((s) => hex3(s.color)));
    gl.bindBuffer(gl.ARRAY_BUFFER, fire.buf);
    const A = this.a;
    gl.enableVertexAttribArray(A.a_xy); gl.vertexAttribPointer(A.a_xy, 2, gl.UNSIGNED_SHORT, true, 8, 0);
    gl.enableVertexAttribArray(A.a_t); gl.vertexAttribPointer(A.a_t, 1, gl.UNSIGNED_SHORT, false, 8, 4);
    gl.enableVertexAttribArray(A.a_s); gl.vertexAttribPointer(A.a_s, 1, gl.UNSIGNED_BYTE, false, 8, 6);
    gl.enableVertexAttribArray(A.a_f); gl.vertexAttribPointer(A.a_f, 1, gl.UNSIGNED_BYTE, false, 8, 7);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.drawArrays(gl.POINTS, lo, hi - lo);
    for (const k of Object.values(A)) gl.disableVertexAttribArray(k);
  },
};
function lowerBound(a, v) { let lo = 0, hi = a.length; while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < v) lo = m + 1; else hi = m; } return lo; }
async function loadFires() {
  const files = M.fires.files;
  // the month being shown first, then the rest
  const cur = new Date(t).toISOString().slice(0, 7);
  const order = [...files].sort((a, b) => (a.month === cur ? -1 : b.month === cur ? 1 : a.month < b.month ? -1 : 1));
  for (const f of order) {
    const buf = await fetch(`../data/timeline/${f.file}?v=${encodeURIComponent(M.generated)}`).then((r) => r.arrayBuffer()).catch(() => null);
    if (!buf) continue;
    fire.parts.set(f.month, new Uint8Array(buf));
    const parts = [...fire.parts.keys()].sort().map((k) => fire.parts.get(k));
    const total = parts.reduce((s, p) => s + p.length, 0), all = new Uint8Array(total);
    let o = 0; for (const p of parts) { all.set(p, o); o += p.length; }
    const n = total / 8, dv = new DataView(all.buffer), ta = new Float64Array(n);
    for (let i = 0; i < n; i++) ta[i] = dv.getUint16(i * 8 + 4, true);
    fire.tarr = ta; fire.n = n;
    fireLayer.upload(all);
    map.triggerRepaint();
  }
}

// ---------------------------------------------------------------- cyclones of the season
let storms = [], stormKey = -1;
async function loadStorms() {
  try {
    const g = await fetch("../data/geo/cyclone_tracks.geojson").then((r) => r.json());
    storms = g.features.filter((f) => f.properties.feature === "track" && f.properties.start && Date.parse(f.properties.end) >= T0 && Date.parse(f.properties.start) <= T1)
      .map((f) => ({ name: f.properties.name, a: Date.parse(f.properties.start), b: Date.parse(f.properties.end), c: f.geometry.coordinates, kind: f.properties.kind }));
  } catch { storms = []; }
}
function stormData() {
  const lines = [], pts = [];
  for (const s of storms) {
    if (t < s.a - DAY || t > s.b + 2 * DAY) continue;
    lines.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: s.c } });
    if (t >= s.a && t <= s.b && s.c.length > 1) {
      const f = ((t - s.a) / (s.b - s.a)) * (s.c.length - 1), i = Math.min(s.c.length - 2, Math.floor(f)), m = f - i;
      pts.push({ type: "Feature", properties: { name: s.name && s.name !== "UNNAMED" ? s.name : "" },
        geometry: { type: "Point", coordinates: [lerp(s.c[i][0], s.c[i + 1][0], m), lerp(s.c[i][1], s.c[i + 1][1], m)] } });
    }
  }
  return { lines: { type: "FeatureCollection", features: lines }, pts: { type: "FeatureCollection", features: pts } };
}

// ---------------------------------------------------------------- map
function initMap() {
  map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      transition: { duration: 0, delay: 0 },
      sources: {
        baseL: { type: "raster", tiles: [TILES.baseL], tileSize: 256, maxzoom: 16, attribution: "Basemap &copy; Esri, HERE, Garmin, USGS" },
        baseD: { type: "raster", tiles: [TILES.baseD], tileSize: 256, maxzoom: 16 },
        refL: { type: "raster", tiles: [TILES.refL], tileSize: 256, maxzoom: 16 },
        refD: { type: "raster", tiles: [TILES.refD], tileSize: 256, maxzoom: 16 },
        dem: { type: "raster-dem", encoding: "terrarium", tiles: [DEM_TILES], tileSize: 256, maxzoom: 10, attribution: "Terrain: AWS Terrain Tiles (SRTM, GMTED)" },
      },
      layers: [
        { id: "bg", type: "background", paint: { "background-color": rgb(LOOK.day.bg) } },
        { id: "baseL", type: "raster", source: "baseL", paint: { "raster-fade-duration": 0 } },
        { id: "baseD", type: "raster", source: "baseD", paint: { "raster-opacity": 0, "raster-fade-duration": 0 } },
        { id: "hillshade", type: "hillshade", source: "dem", paint: { "hillshade-exaggeration": 0.62, "hillshade-illumination-direction": 315, "hillshade-illumination-anchor": "map" } },
      ],
    },
    bounds: [[60, 5], [100, 41]], fitBoundsOptions: { padding: 4 },
    minZoom: 2.6, maxZoom: 8.5, dragRotate: false, pitchWithRotate: false, touchPitch: false, cooperativeGestures: matchMedia("(pointer: coarse)").matches,
    pixelRatio: Math.min(window.devicePixelRatio || 1, 2), fadeDuration: 0,
    attributionControl: { compact: true, customAttribution: "Data: NASA FIRMS, GEOS-FP; CREAMS; LSA SAF; MOSDAC; CPCB" },
  });
  map.touchZoomRotate.disableRotation();
  return new Promise((res) => (map.isStyleLoaded() ? res() : map.once("style.load", res)));
}

async function addLayers() {
  const geo = await Promise.all(["countries", "states"].map((n) => fetch(`../data/geo/${n}.geojson`).then((r) => r.json()).catch(() => null)));
  map.addLayer(smokeLayer);
  const b = M.aer.bounds;
  wind.W = 900; wind.H = Math.round((wind.W * (mercY(b[3]) - mercY(b[1]))) / (((b[2] - b[0]) * Math.PI) / 180));
  wind.cv = document.createElement("canvas"); wind.cv.width = wind.W; wind.cv.height = wind.H; wind.ctx = wind.cv.getContext("2d");
  const n = window.innerWidth < 760 ? 1500 : 2600;
  for (let i = 0; i < n; i++) wind.parts.push(newParticle(true));
  map.addSource("wind", { type: "canvas", canvas: wind.cv, coordinates: [[b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]], animate: true });
  map.addLayer({ id: "wind", type: "raster", source: "wind", paint: { "raster-opacity": 0.6, "raster-fade-duration": 0 } });
  if (geo[1]) {
    map.addSource("states", { type: "geojson", data: geo[1] });
    map.addLayer({ id: "states", type: "line", source: "states", paint: { "line-color": rgb(LOOK.day.line), "line-opacity": 0.28, "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.4, 7, 1] } });
  }
  if (geo[0]) {
    map.addSource("countries", { type: "geojson", data: geo[0] });
    map.addLayer({ id: "countries", type: "line", source: "countries", paint: { "line-color": rgb(LOOK.day.line), "line-opacity": 0.55, "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.6, 7, 1.5] } });
  }
  map.addLayer({ id: "refL", type: "raster", source: "refL", paint: { "raster-fade-duration": 0 } });
  map.addLayer({ id: "refD", type: "raster", source: "refD", paint: { "raster-opacity": 0, "raster-fade-duration": 0 } });
  map.addSource("st-line", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addSource("st-pt", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({ id: "st-line", type: "line", source: "st-line", paint: { "line-color": rgb(LOOK.day.line), "line-opacity": 0.7, "line-width": 1.2, "line-dasharray": [3, 2] } });
  map.addLayer({ id: "st-pt", type: "circle", source: "st-pt", paint: { "circle-radius": 6, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#f2683c", "circle-stroke-width": 2.5 } });
  map.addLayer(fireLayer);
}

function applyLook(k) {
  const L = {}; for (const key of Object.keys(LOOK.day)) L[key] = mix(LOOK.day[key], LOOK.night[key], k);
  map.setPaintProperty("bg", "background-color", rgb(L.bg));
  map.setPaintProperty("baseD", "raster-opacity", k);
  map.setPaintProperty("baseL", "raster-opacity", 1 - k * 0.999);
  map.setPaintProperty("refD", "raster-opacity", k);
  map.setPaintProperty("refL", "raster-opacity", 1 - k);
  map.setPaintProperty("hillshade", "hillshade-shadow-color", rgb(L.hsS));
  map.setPaintProperty("hillshade", "hillshade-highlight-color", rgb(L.hsH));
  map.setPaintProperty("hillshade", "hillshade-accent-color", rgb(L.hsA));
  for (const id of ["states", "countries", "st-line"]) if (map.getLayer(id)) map.setPaintProperty(id, "line-color", rgb(L.line));
  wind.col = rgba(L.wind);
}

// ---------------------------------------------------------------- UI
const fmtDate = (ms) => { const d = new Date(ms + IST); return `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };
const fmtTime = (ms) => { const d = new Date(ms + IST); return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")} IST`; };
const istDay = (ms) => new Date(ms + IST).toISOString().slice(0, 10);

function buildLegend() {
  $("#lg-fire").innerHTML = SENS.map((s, i) => `<button type="button" data-i="${i}" aria-pressed="true" style="--c:${s.color}" title="${s.label} ${s.sub}">
    <span class="sw${s.ring ? " ring" : ""}" style="background:${s.color}"></span><span class="nm">${s.label}</span><span class="sb">${s.sub}</span></button>`).join("");
  $("#lg-fire").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const i = +b.dataset.i; on[i] = !on[i]; b.setAttribute("aria-pressed", String(on[i])); map.triggerRepaint();
  });
  const max = SMOKE_STOPS[SMOKE_STOPS.length - 1][0], px = rampPixels(64), stops = [];
  for (let i = 0; i < 64; i++) stops.push(`rgba(${px[i * 4]},${px[i * 4 + 1]},${px[i * 4 + 2]},${(px[i * 4 + 3] / 255).toFixed(2)}) ${((i / 63) * 100).toFixed(1)}%`);
  $("#aer-bar").style.background = `linear-gradient(90deg, ${stops.join(",")}), repeating-conic-gradient(var(--panel-2) 0 25%, var(--panel) 0 50%) 0 0 / 8px 8px`;
  $("#aer-ticks").innerHTML = SMOKE_TICKS.map((v) => `<span style="left:${(Math.sqrt(v / max) * 100).toFixed(1)}%">${v}</span>`).join("");
  $("#aqi-scale").innerHTML = AQI_CATS.map((c) => `<div style="background:${c.bg};color:${c.fg}">${c.k}<br>${c.r}</div>`).join("");
}

let AQ = null, aqiShown = "";
function renderAQI() {
  if (!AQ || !AQ.dates.length) return;
  const d = istDay(t);
  let i = AQ.dates.length - 1;
  while (i > 0 && AQ.dates[i] > d) i--;
  const key = AQ.dates[i]; if (key === aqiShown) return;
  aqiShown = key;
  const dd = new Date(key + "T00:00:00Z");
  $("#aqi-date").textContent = `${dd.getUTCDate()} ${MONTHS[dd.getUTCMonth()]}`;
  $("#aqi").innerHTML = AQ.cities.map((c) => {
    const v = c.aqi[i], cat = AQI_CATS.find((x) => x.k === c.cat[i]);
    const st = cat ? `background:${cat.bg};color:${cat.fg}` : "";
    return `<div class="aqi-cell" style="${st}" title="${c.name}${c.prom[i] ? `: ${c.prom[i]}` : ""}"><span class="c">${c.name}</span><span class="v">${v == null ? "–" : v}</span><span class="k">${cat ? cat.k : "no bulletin"}</span></div>`;
  }).join("");
}

const wrap = () => $("#mapwrap");
let hideTimer = 0, dragging = false;
function showCtl() {
  wrap().classList.add("show-ctl");
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => { if (!dragging) wrap().classList.remove("show-ctl"); }, 2600);
}
function setPlaying(p) {
  playing = p;
  const ic = $("#play-ic"); ic.dataset.icon = p ? "pause" : "play";
  ic.innerHTML = `<svg class="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${(window.ICONS || {})[ic.dataset.icon] || ""}</svg>`;
  $("#play").setAttribute("aria-label", p ? "Pause" : "Play");
}
function bindUI() {
  const w = wrap(), s = $("#slider");
  w.addEventListener("mousemove", showCtl);
  w.addEventListener("touchstart", showCtl, { passive: true });
  s.max = String(Math.round((T1 - T0) / HOUR));
  s.addEventListener("pointerdown", () => { dragging = true; showCtl(); });
  window.addEventListener("pointerup", () => { if (dragging) { dragging = false; showCtl(); } });
  s.addEventListener("input", () => {
    t = T0 + +s.value * HOUR; startAt = t; store.set("tl-start", String(Math.round(t)));
    lastTs = 0; showCtl(); drawUI(true);
  });
  $("#play").addEventListener("click", () => { setPlaying(!playing); lastTs = 0; showCtl(); });
  document.addEventListener("keydown", (e) => { if (e.key === " " && e.target === document.body) { e.preventDefault(); setPlaying(!playing); showCtl(); } });
}

let lastLabel = "";
function drawUI(force) {
  const lab = `${fmtDate(t)}|${fmtTime(t)}`;
  if (lab !== lastLabel || force) {
    lastLabel = lab;
    $("#d-date").textContent = fmtDate(t); $("#d-time").textContent = fmtTime(t);
    const d = new Date(t + IST); $("#ctl-d").textContent = `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
    if (!dragging) $("#slider").value = String(Math.round((t - T0) / HOUR));
    renderAQI();
  }
}

function tick(ts) {
  requestAnimationFrame(tick);
  if (!mapReady) return;
  if (playing && ts > holdUntil) {
    if (lastTs) t += ((ts - lastTs) / 1000) * (DAY / SEC_PER_DAY);
    if (t >= T1) { t = T1; holdUntil = ts + 2500; lastTs = 0; setTimeout(() => { t = startAt >= T1 - DAY ? T0 : startAt; }, 2400); }
    else lastTs = ts;
  } else lastTs = ts > holdUntil ? 0 : lastTs;
  const fr = frameAt(t);
  if (fr) { const c = chunkOf(fr.a); loadChunk(c); if (fr.a - M.aer.chunks[c].f0 > M.aer.chunks[c].n - 16 && c + 1 < M.aer.chunks.length) loadChunk(c + 1); }
  const k = 1 - smoothstep(-7, 5, sunElevation(t));
  if (Math.abs(k - night) > 0.015 || (k !== night && (k === 0 || k === 1))) { night = k; applyLook(k); }
  if (wind.ctx) stepWind(wind.col || rgba(LOOK.day.wind));
  const sk = storms.length ? Math.floor(t / HOUR) : 0;
  if (sk !== stormKey) {
    stormKey = sk;
    const sd = stormData();
    map.getSource("st-line").setData(sd.lines);
    map.getSource("st-pt").setData(sd.pts);
  }
  drawUI(false);
  map.triggerRepaint();
}

async function boot() {
  try { M = await window.__boot.meta; } catch { $("#load").textContent = "The timeline data is not available yet."; return; }
  T0 = Date.parse(M.start); T1 = Date.parse(M.end); F0 = Date.parse(M.aer.first_frame);
  const saved = +store.get("tl-start");
  startAt = saved && saved >= T0 && saved < T1 ? saved : T0;
  t = startAt;
  buildLegend(); bindUI(); setPlaying(true); drawUI(true);
  fetch(`../data/timeline/aqi.json?v=${encodeURIComponent(M.generated)}`).then((r) => r.json()).then((a) => { AQ = a; aqiShown = ""; renderAQI(); }).catch(() => {});
  $("#foot").innerHTML = `Every 3 hours from ${fmtDate(T0)} to ${fmtDate(T1)}; built ${M.generated.replace("T", " ")}. Fires: NASA FIRMS (MODIS Terra and Aqua, VIIRS S-NPP, NOAA-20, NOAA-21), ICAR-IARI CREAMS bulletin events, Meteosat SEVIRI FRP-PIXEL (EUMETSAT LSA SAF, Punjab-Haryana window, CC BY 4.0) and INSAT-3DS L2P fire pixels (ISRO/SAC via MOSDAC). Polar-orbiter fires stay on the map 12 hours, geostationary cells 1-1.5 hours. Smoke and 10 m wind: NASA GMAO GEOS-FP. AQI: CPCB National Air Quality Index bulletin (Delhi-NCR shows Delhi). The basemap darkens with the sun over central India. <a href="https://github.com/reclaimchennai/stubble-fire-watch">Open data and code</a>.`;
  await initMap();
  await loadStorms();
  await addLayers();
  night = -1;
  const fr = frameAt(t);
  if (fr) await loadChunk(chunkOf(fr.a));
  $("#load").hidden = true;
  mapReady = true;
  window.__tl = { map, smokeLayer, fireLayer, fire, chunks, get t() { return t; } };   // for debugging in the console
  const at = document.querySelector(".maplibregl-ctrl-attrib"); if (at) at.classList.remove("maplibregl-compact-show");
  loadFires();
  requestAnimationFrame(tick);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
