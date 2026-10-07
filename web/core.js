/* Handschrift web core: the same pipeline as the Python app, without any
 * browser APIs, so it runs in the page and in Node tests alike.
 *
 *   detectPage(gray)            find the ArUco corner markers -> page + homography
 *   extractPage(gray, det)      rectify, flatten light, snap boxes, cut out glyphs
 *   buildProfile(pages)         baselines + writing size -> a handwriting profile
 *   Layout                      text -> positioned glyph instances on A4 pages
 *
 * Images are {w, h, data} with data a Float32Array of grey values 0..1.
 */
(function (root) {
  "use strict";

  // ------------------------------------------------------------------ layout
  const PAGE_W = 210, PAGE_H = 297;
  const MARKER_SIZE = 14, MARKER_MARGIN = 10, MAX_PAGES = 12;
  const COLS = 11, BOX_W = 16, BOX_H = 24, GAP_X = 1, LABEL_H = 4.5, ROW_GAP = 0.5;
  const GRID_X0 = (PAGE_W - (COLS * BOX_W + (COLS - 1) * GAP_X)) / 2, GRID_Y0 = 31;
  const BASELINE_FROM_TOP = 15, CROP_INSET = 1.1;
  const PPM = 10; // rectified pixels per mm

  const CHARSET = Array.from("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzÄÖÜäöüß0123456789.,;:!?-()\"'„“/+&%€=");
  const BASELINE_CHARS = new Set(Array.from("ABCDEFGHIKLMNOPRSTUVWXYZabcdehiklmnorstuvwxzÄÖÜäöü0123456789.!?:&%€"));
  const XHEIGHT_CHARS = "acemnorsuvwxz", CAPHEIGHT_CHARS = "ABDEFHIKLMNPRTUVWXZ";

  // Inner 4x4 bits (row-major, 1 = black) of ArUco DICT_4X4_50 ids 0..47.
  const MARKER_CODES = ["0100101011001101", "1111000001100101", "1100110011010010", "0110011010111001", "1010101101100001", "1000011000110010", "0110000111010001", "0011101100001101", "0000000100100101", "0011000010101001", "0000011001101110", "1110111001011000", "1111000101001000", "1101010111110000", "1101101101001110", "1101100111000001", "1011100110011010", "1001100111111111", "1001001110100001", "1000100101010000", "0111100101110100", "0100111111010100", "0011001100101010", "0010001001111101", "0000000110111000", "0110101110001110", "0101001100011011", "0101101010101011", "1101111011011100", "1100101110010000", "1011101111101010", "1010100001001101", "0110000100110000", "0000111100110100", "1111011101010001", "1111011011010110", "1110011110001010", "1111101100000000", "1111001000001001", "1110001110100101", "1110100011100111", "1101010111010111", "1100110101110011", "1100011101001101", "1101101100010111", "1101000100010100", "1101001011000000", "1011010010011011"];

  function slots() {
    return CHARSET.map((ch, i) => {
      const r = Math.floor(i / COLS), c = i % COLS;
      return {
        index: i, char: ch, w: BOX_W, h: BOX_H,
        x: GRID_X0 + c * (BOX_W + GAP_X),
        y: GRID_Y0 + r * (LABEL_H + BOX_H + ROW_GAP) + LABEL_H,
      };
    });
  }

  function markerCornersMm(id) {
    const near = MARKER_MARGIN, fx = PAGE_W - MARKER_MARGIN - MARKER_SIZE, fy = PAGE_H - MARKER_MARGIN - MARKER_SIZE;
    const [x, y] = [[near, near], [fx, near], [fx, fy], [near, fy]][id % 4];
    const s = MARKER_SIZE;
    return [[x, y], [x + s, y], [x + s, y + s], [x, y + s]];
  }

  class UserError extends Error {}

  // ------------------------------------------------------------ image utils
  function image(w, h, data) { return { w, h, data: data || new Float32Array(w * h) }; }

  /** Average-downscale by an integer factor. */
  function shrink(img, f) {
    if (f <= 1) return img;
    const w = Math.floor(img.w / f), h = Math.floor(img.h / f), out = new Float32Array(w * h), d = img.data, n = f * f;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let s = 0;
      for (let j = 0; j < f; j++) { const row = (y * f + j) * img.w + x * f; for (let i = 0; i < f; i++) s += d[row + i]; }
      out[y * w + x] = s / n;
    }
    return image(w, h, out);
  }

  function bilinear(img, x, y) {
    const { w, h, data } = img;
    if (x < 0 || y < 0 || x > w - 1 || y > h - 1) return 1;
    const x0 = Math.floor(x), y0 = Math.floor(y), x1 = Math.min(x0 + 1, w - 1), y1 = Math.min(y0 + 1, h - 1);
    const fx = x - x0, fy = y - y0;
    const a = data[y0 * w + x0], b = data[y0 * w + x1], c = data[y1 * w + x0], e = data[y1 * w + x1];
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + e * fx) * fy;
  }

  /** Connected components (8-neighbourhood) of a Uint8 mask. */
  function components(mask, w, h) {
    const labels = new Int32Array(w * h), parent = [0];
    const find = (a) => { while (parent[a] !== a) { parent[a] = parent[parent[a]]; a = parent[a]; } return a; };
    const union = (a, b) => { a = find(a); b = find(b); if (a !== b) { if (a < b) parent[b] = a; else parent[a] = b; } };
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = y * w + x;
      if (!mask[i]) continue;
      const nb = [];
      if (x > 0 && labels[i - 1]) nb.push(labels[i - 1]);
      if (y > 0) {
        const up = i - w;
        if (labels[up]) nb.push(labels[up]);
        if (x > 0 && labels[up - 1]) nb.push(labels[up - 1]);
        if (x < w - 1 && labels[up + 1]) nb.push(labels[up + 1]);
      }
      if (!nb.length) { const l = parent.length; parent.push(l); labels[i] = l; }
      else { let m = nb[0]; for (const l of nb) if (l < m) m = l; labels[i] = m; for (const l of nb) union(m, l); }
    }
    const remap = new Int32Array(parent.length);
    let n = 0;
    for (let l = 1; l < parent.length; l++) { const r = find(l); if (!remap[r]) remap[r] = ++n; remap[l] = remap[r]; }
    const stats = [];
    for (let k = 0; k <= n; k++) stats.push({ x0: Infinity, y0: Infinity, x1: -1, y1: -1, area: 0 });
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = y * w + x;
      if (!labels[i]) continue;
      const l = remap[labels[i]];
      labels[i] = l;
      const s = stats[l];
      s.area++;
      if (x < s.x0) s.x0 = x; if (x > s.x1) s.x1 = x;
      if (y < s.y0) s.y0 = y; if (y > s.y1) s.y1 = y;
    }
    return { labels, n, stats };
  }

  // ------------------------------------------------------------ homography
  function solve(A, b) { // Gaussian elimination with partial pivoting
    const n = b.length;
    for (let c = 0; c < n; c++) {
      let p = c;
      for (let r = c + 1; r < n; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
      [A[c], A[p]] = [A[p], A[c]]; [b[c], b[p]] = [b[p], b[c]];
      if (Math.abs(A[c][c]) < 1e-12) return null;
      for (let r = c + 1; r < n; r++) {
        const f = A[r][c] / A[c][c];
        for (let k = c; k < n; k++) A[r][k] -= f * A[c][k];
        b[r] -= f * b[c];
      }
    }
    const x = new Array(n).fill(0);
    for (let r = n - 1; r >= 0; r--) {
      let s = b[r];
      for (let k = r + 1; k < n; k++) s -= A[r][k] * x[k];
      x[r] = s / A[r][r];
    }
    return x;
  }

  function normalizer(pts) {
    let cx = 0, cy = 0;
    for (const [x, y] of pts) { cx += x; cy += y; }
    cx /= pts.length; cy /= pts.length;
    let d = 0;
    for (const [x, y] of pts) d += Math.hypot(x - cx, y - cy);
    const s = Math.SQRT2 / (d / pts.length || 1);
    return [[s, 0, -s * cx], [0, s, -s * cy], [0, 0, 1]];
  }

  function matMul(A, B) {
    return A.map((row) => [0, 1, 2].map((j) => row[0] * B[0][j] + row[1] * B[1][j] + row[2] * B[2][j]));
  }

  function matInv(m) {
    const [a, b, c] = m[0], [d, e, f] = m[1], [g, h, i] = m[2];
    const A = e * i - f * h, B = -(d * i - f * g), C = d * h - e * g;
    const det = a * A + b * B + c * C;
    return [
      [A / det, -(b * i - c * h) / det, (b * f - c * e) / det],
      [B / det, (a * i - c * g) / det, -(a * f - c * d) / det],
      [C / det, -(a * h - b * g) / det, (a * e - b * d) / det],
    ];
  }

  /** Least-squares homography mapping src points onto dst points. */
  function homography(src, dst) {
    const Ts = normalizer(src), Td = normalizer(dst);
    const ap = (T, [x, y]) => [T[0][0] * x + T[0][2], T[1][1] * y + T[1][2]];
    const AtA = Array.from({ length: 8 }, () => new Array(8).fill(0)), Atb = new Array(8).fill(0);
    const add = (row, v) => {
      for (let i = 0; i < 8; i++) { Atb[i] += row[i] * v; for (let j = 0; j < 8; j++) AtA[i][j] += row[i] * row[j]; }
    };
    for (let k = 0; k < src.length; k++) {
      const [x, y] = ap(Ts, src[k]), [u, v] = ap(Td, dst[k]);
      add([x, y, 1, 0, 0, 0, -u * x, -u * y], u);
      add([0, 0, 0, x, y, 1, -v * x, -v * y], v);
    }
    const h = solve(AtA, Atb);
    if (!h) return null;
    const Hn = [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1]];
    const H = matMul(matInv(Td), matMul(Hn, Ts));
    const s = H[2][2];
    return H.map((r) => r.map((v) => v / s));
  }

  function apply(H, x, y) {
    const w = H[2][0] * x + H[2][1] * y + H[2][2];
    return [(H[0][0] * x + H[0][1] * y + H[0][2]) / w, (H[1][0] * x + H[1][1] * y + H[1][2]) / w];
  }

  // -------------------------------------------------------- marker detection
  function adaptiveDark(img, win, c) {
    const { w, h, data } = img, I = new Float64Array((w + 1) * (h + 1)), r = win >> 1;
    for (let y = 0; y < h; y++) {
      let s = 0;
      for (let x = 0; x < w; x++) { s += data[y * w + x]; I[(y + 1) * (w + 1) + x + 1] = I[y * (w + 1) + x + 1] + s; }
    }
    const out = new Uint8Array(w * h);
    for (let y = 0; y < h; y++) {
      const ya = Math.max(0, y - r), yb = Math.min(h, y + r + 1);
      for (let x = 0; x < w; x++) {
        const xa = Math.max(0, x - r), xb = Math.min(w, x + r + 1);
        const sum = I[yb * (w + 1) + xb] - I[ya * (w + 1) + xb] - I[yb * (w + 1) + xa] + I[ya * (w + 1) + xa];
        const mean = sum / ((yb - ya) * (xb - xa));
        if (data[y * w + x] < mean - c) out[y * w + x] = 1;
      }
    }
    return out;
  }

  function quadCorners(pts) {
    let cx = 0, cy = 0;
    for (const p of pts) { cx += p[0]; cy += p[1]; }
    cx /= pts.length; cy /= pts.length;
    const far = (from) => { let best = null, bd = -1; for (const p of pts) { const d = (p[0] - from[0]) ** 2 + (p[1] - from[1]) ** 2; if (d > bd) { bd = d; best = p; } } return best; };
    const a = far([cx, cy]), c = far(a);
    let b = null, d = null, bmax = 0, dmin = 0;
    const dx = c[0] - a[0], dy = c[1] - a[1];
    for (const p of pts) {
      const s = dx * (p[1] - a[1]) - dy * (p[0] - a[0]);
      if (s > bmax) { bmax = s; b = p; }
      if (s < dmin) { dmin = s; d = p; }
    }
    if (!b || !d) return null;
    const quad = [a, b, c, d].map((p) => {
      const vx = p[0] - cx, vy = p[1] - cy, n = Math.hypot(vx, vy) || 1;
      return [p[0] + 0.5 + 0.7 * vx / n, p[1] + 0.5 + 0.7 * vy / n]; // pixel centre -> outer corner
    });
    quad.sort((p, q) => Math.atan2(p[1] - cy, p[0] - cx) - Math.atan2(q[1] - cy, q[0] - cx));
    return { quad, cx: cx + 0.5, cy: cy + 0.5 };
  }

  function decodeQuad(img, quad) {
    let best = null;
    for (let s = 0; s < 4; s++) {
      const q = [0, 1, 2, 3].map((k) => quad[(k + s) % 4]);
      const H = homography([[0, 0], [6, 0], [6, 6], [0, 6]], q);
      if (!H) continue;
      const vals = [];
      for (let r = 0; r < 6; r++) for (let c = 0; c < 6; c++) {
        let v = 0;
        for (const oy of [-0.2, 0, 0.2]) for (const ox of [-0.2, 0, 0.2]) {
          const [x, y] = apply(H, c + 0.5 + ox, r + 0.5 + oy);
          v += bilinear(img, x, y);
        }
        vals.push(v / 9);
      }
      const lo = Math.min(...vals), hi = Math.max(...vals);
      if (hi - lo < 0.15) return null;
      const thr = (lo + hi) / 2;
      const bits = vals.map((v) => (v < thr ? 1 : 0));
      let borderErr = 0;
      for (let r = 0; r < 6; r++) for (let c = 0; c < 6; c++) if ((r === 0 || r === 5 || c === 0 || c === 5) && !bits[r * 6 + c]) borderErr++;
      if (borderErr > 1) continue;
      let inner = "";
      for (let r = 1; r < 5; r++) for (let c = 1; c < 5; c++) inner += bits[r * 6 + c];
      for (let id = 0; id < MARKER_CODES.length; id++) {
        let d = 0;
        for (let k = 0; k < 16; k++) if (inner[k] !== MARKER_CODES[id][k]) d++;
        if (d <= 1 && (!best || d + borderErr < best.err)) best = { id, corners: q, err: d + borderErr };
      }
    }
    return best;
  }

  function findMarkers(gray) {
    const long = Math.max(gray.w, gray.h);
    const f = Math.max(1, Math.round(long / 2000));
    const small = shrink(gray, f);
    const { w, h } = small;
    const win = (Math.round(Math.max(w, h) / 40) | 1);
    const found = new Map();
    for (const c of [0.06, 0.03, 0.1]) {
      const mask = adaptiveDark(small, win, c);
      const { labels, n, stats } = components(mask, w, h);
      const minSide = Math.max(10, Math.min(w, h) * 0.02), maxSide = Math.min(w, h) * 0.3;
      for (let l = 1; l <= n; l++) {
        const s = stats[l];
        const bw = s.x1 - s.x0 + 1, bh = s.y1 - s.y0 + 1;
        if (bw < minSide || bh < minSide || bw > maxSide || bh > maxSide) continue;
        if (Math.max(bw, bh) / Math.min(bw, bh) > 3 || s.area < 0.25 * bw * bh) continue;
        const pts = [];
        for (let y = s.y0; y <= s.y1; y++) for (let x = s.x0; x <= s.x1; x++) if (labels[y * w + x] === l) pts.push([x, y]);
        const qc = quadCorners(pts);
        if (!qc) continue;
        const m = decodeQuad(small, qc.quad);
        if (m && (!found.has(m.id) || found.get(m.id).err > m.err)) {
          found.set(m.id, { id: m.id, err: m.err, corners: m.corners.map(([x, y]) => [x * f, y * f]) });
        }
      }
      if (bestPage(found).count >= 4) break;
    }
    return found;
  }

  function bestPage(found) {
    const counts = {};
    for (const id of found.keys()) { const p = id >> 2; counts[p] = (counts[p] || 0) + 1; }
    let page = 0, count = 0;
    for (const p in counts) if (counts[p] > count) { count = counts[p]; page = +p; }
    return { page, count };
  }

  /** Find the template page in a photo. Returns {page, H, markers, errorMm};
   *  H maps rectified pixels (mm * PPM) to photo pixels. */
  function detectPage(gray) {
    const found = findMarkers(gray);
    const { page, count } = bestPage(found);
    if (count < 3) {
      throw new UserError(`Nur ${count} von 4 Ecken-Markern erkannt. Fotografiere das ganze Blatt mit allen vier schwarzen Ecken-Quadraten – gerade von oben, hell und scharf.`);
    }
    const src = [], dst = [];
    for (const m of found.values()) {
      if (m.id >> 2 !== page) continue;
      markerCornersMm(m.id).forEach((p, k) => { src.push([p[0] * PPM, p[1] * PPM]); dst.push(m.corners[k]); });
    }
    const H = homography(src, dst);
    if (!H) throw new UserError("Die Perspektive konnte nicht berechnet werden.");
    const Hinv = matInv(H);
    let err = 0;
    dst.forEach((p, k) => { const [x, y] = apply(Hinv, p[0], p[1]); err += Math.hypot(x - src[k][0], y - src[k][1]); });
    return { page, H, markers: count, errorMm: err / dst.length / PPM };
  }

  // ---------------------------------------------------------- rectification
  function rectify(gray, H) {
    // shrink the photo first if it is much sharper than the target grid
    const [ax, ay] = apply(H, 105 * PPM, 148 * PPM), [bx, by] = apply(H, 106 * PPM, 148 * PPM);
    const pxPerMm = Math.hypot(bx - ax, by - ay);
    const f = Math.floor(pxPerMm / PPM / 1.1);
    let src = gray, Hs = H;
    if (f >= 2) {
      src = shrink(gray, f);
      Hs = matMul([[1 / f, 0, -0.5 + 0.5 / f], [0, 1 / f, -0.5 + 0.5 / f], [0, 0, 1]], H);
    }
    const W = Math.round(PAGE_W * PPM), Hh = Math.round(PAGE_H * PPM), out = new Float32Array(W * Hh);
    for (let y = 0; y < Hh; y++) {
      for (let x = 0; x < W; x++) {
        const w = Hs[2][0] * x + Hs[2][1] * y + Hs[2][2];
        out[y * W + x] = bilinear(src, (Hs[0][0] * x + Hs[0][1] * y + Hs[0][2]) / w, (Hs[1][0] * x + Hs[1][1] * y + Hs[1][2]) / w);
      }
    }
    return image(W, Hh, out);
  }

  /** Brightness relative to the local paper colour (paper ~1, ink << 1). */
  function flatten(img) {
    const B = 4, w = Math.ceil(img.w / B), h = Math.ceil(img.h / B);
    let small = new Float32Array(w * h);
    for (let y = 0; y < img.h; y++) for (let x = 0; x < img.w; x++) {
      const i = Math.floor(y / B) * w + Math.floor(x / B), v = img.data[y * img.w + x];
      if (v > small[i]) small[i] = v;
    }
    const pass = (src, fn) => { const o = new Float32Array(w * h); for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) o[y * w + x] = fn(src, x, y); return o; };
    const R = 3;
    small = pass(small, (s, x, y) => { let m = 0; for (let k = -R; k <= R; k++) { const xx = Math.min(w - 1, Math.max(0, x + k)); if (s[y * w + xx] > m) m = s[y * w + xx]; } return m; });
    small = pass(small, (s, x, y) => { let m = 0; for (let k = -R; k <= R; k++) { const yy = Math.min(h - 1, Math.max(0, y + k)); if (s[yy * w + x] > m) m = s[yy * w + x]; } return m; });
    for (let it = 0; it < 3; it++) {
      small = pass(small, (s, x, y) => { let t = 0; for (let k = -2; k <= 2; k++) t += s[y * w + Math.min(w - 1, Math.max(0, x + k))]; return t / 5; });
      small = pass(small, (s, x, y) => { let t = 0; for (let k = -2; k <= 2; k++) t += s[Math.min(h - 1, Math.max(0, y + k)) * w + x]; return t / 5; });
    }
    const bg = image(w, h, small), out = new Float32Array(img.w * img.h);
    for (let y = 0; y < img.h; y++) for (let x = 0; x < img.w; x++) {
      const b = bilinear(bg, (x + 0.5) / B - 0.5, (y + 0.5) / B - 0.5);
      out[y * img.w + x] = Math.min(1, img.data[y * img.w + x] / Math.max(b, 0.05));
    }
    return image(img.w, img.h, out);
  }

  // ------------------------------------------------------------- extraction
  const INK_THRESHOLD = 0.66, FAINT_LEVEL = 0.52, ALPHA_WHITE = 0.9, ALPHA_BLACK = 0.3;

  function prefixSums(norm) {
    const { w, h } = norm, rows = new Int32Array(h * (w + 1)), cols = new Int32Array(w * (h + 1));
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const d = norm.data[y * w + x] < 0.8 ? 1 : 0;
      rows[y * (w + 1) + x + 1] = rows[y * (w + 1) + x] + d;
      cols[x * (h + 1) + y + 1] = cols[x * (h + 1) + y] + d;
    }
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    return {
      row: (y, x0, x1) => { if (y < 0 || y >= h) return 0; x0 = clamp(x0, 0, w); x1 = clamp(x1, 0, w); return rows[y * (w + 1) + x1] - rows[y * (w + 1) + x0]; },
      col: (x, y0, y1) => { if (x < 0 || x >= w) return 0; y0 = clamp(y0, 0, h); y1 = clamp(y1, 0, h); return cols[x * (h + 1) + y1] - cols[x * (h + 1) + y0]; },
    };
  }

  /** Align a box with its printed outline (paper is rarely perfectly flat). */
  function snapBox(ps, slot) {
    const m = 3 * PPM, w = Math.round(slot.w * PPM), h = Math.round(slot.h * PPM);
    const X = Math.round(slot.x * PPM), Y = Math.round(slot.y * PPM);
    let best = -1, bx = 0, by = 0;
    for (let dy = -m; dy <= m; dy++) for (let dx = -m; dx <= m; dx++) {
      const l = X + dx, t = Y + dy;
      let s = 0;
      for (let k = -1; k <= 1; k++) {
        s += ps.row(t + k, l, l + w + 1) + ps.row(t + h + k, l, l + w + 1);
        s += ps.col(l + k, t, t + h + 1) + ps.col(l + w + k, t, t + h + 1);
      }
      if (s > best || (s === best && Math.abs(dx) + Math.abs(dy) < Math.abs(bx) + Math.abs(by))) { best = s; bx = dx; by = dy; }
    }
    if (best < 0.45 * 2 * (w + h)) return { dx: 0, dy: 0, ok: false };
    return { dx: bx, dy: by, ok: true };
  }

  function extractBox(norm, slot, dx, dy) {
    const inset = CROP_INSET * PPM;
    const left = Math.round(slot.x * PPM + dx + inset), top = Math.round(slot.y * PPM + dy + inset);
    const right = Math.round((slot.x + slot.w) * PPM + dx - inset), bottom = Math.round((slot.y + slot.h) * PPM + dy - inset);
    const cw = right - left, ch = bottom - top;
    if (cw <= 0 || ch <= 0) return null;
    const crop = new Float32Array(cw * ch), ink = new Uint8Array(cw * ch);
    for (let y = 0; y < ch; y++) for (let x = 0; x < cw; x++) {
      const yy = top + y, xx = left + x;
      const v = yy >= 0 && yy < norm.h && xx >= 0 && xx < norm.w ? norm.data[yy * norm.w + xx] : 1;
      crop[y * cw + x] = v;
      ink[y * cw + x] = v < INK_THRESHOLD ? 1 : 0;
    }
    const { labels, n, stats } = components(ink, cw, ch);
    const minVal = new Float32Array(n + 1).fill(1);
    for (let i = 0; i < cw * ch; i++) if (labels[i] && crop[i] < minVal[labels[i]]) minVal[labels[i]] = crop[i];
    const keep = new Uint8Array(n + 1), thin = 0.7 * PPM, minArea = Math.max(4, Math.round(10 * (PPM / 12) ** 2));
    for (let l = 1; l <= n; l++) {
      const s = stats[l], bw = s.x1 - s.x0 + 1, bh = s.y1 - s.y0 + 1;
      if (s.area < minArea) continue;
      if (minVal[l] > FAINT_LEVEL && bh <= thin) continue; // printed guide-line dots
      const touches = s.x0 === 0 || s.y0 === 0 || s.x1 >= cw - 1 || s.y1 >= ch - 1;
      if (touches && (bw <= thin || bh <= thin) && Math.max(bw, bh) > 3 * PPM) continue; // box outline remainder
      keep[l] = 1;
    }
    const mask = new Uint8Array(cw * ch);
    let total = 0, x0 = cw, x1 = -1, y0 = ch, y1 = -1;
    for (let y = 0; y < ch; y++) for (let x = 0; x < cw; x++) {
      const i = y * cw + x;
      if (labels[i] && keep[labels[i]]) {
        mask[i] = 1; total++;
        if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
    }
    if (total < 0.35 * PPM * PPM) return null;

    const alpha = new Float32Array(cw * ch), inMask = [];
    for (let y = 0; y < ch; y++) for (let x = 0; x < cw; x++) {
      let grown = 0;
      for (let j = -1; j <= 1 && !grown; j++) for (let i = -1; i <= 1; i++) {
        const yy = y + j, xx = x + i;
        if (yy >= 0 && yy < ch && xx >= 0 && xx < cw && mask[yy * cw + xx]) { grown = 1; break; }
      }
      if (!grown) continue;
      const a = Math.min(1, Math.max(0, (ALPHA_WHITE - crop[y * cw + x]) / (ALPHA_WHITE - ALPHA_BLACK)));
      alpha[y * cw + x] = a;
      if (mask[y * cw + x]) inMask.push(a);
    }
    inMask.sort((a, b) => a - b);
    const p95 = inMask[Math.min(inMask.length - 1, Math.floor(0.95 * (inMask.length - 1)))];
    const scale = 1 / Math.max(p95, 0.35);

    const pad = 3;
    const ix0 = Math.max(x0 - pad, 0), ix1 = Math.min(x1 + 1 + pad, cw), iy0 = Math.max(y0 - pad, 0), iy1 = Math.min(y1 + 1 + pad, ch);
    const gw = ix1 - ix0, gh = iy1 - iy0, out = new Uint8Array(gw * gh);
    for (let y = 0; y < gh; y++) for (let x = 0; x < gw; x++) {
      out[y * gw + x] = Math.round(Math.min(1, alpha[(y + iy0) * cw + x + ix0] * scale) * 255);
    }
    const tmplBaseline = (slot.y + BASELINE_FROM_TOP) * PPM + dy - (top + iy0);
    return {
      char: slot.char, width: gw, height: gh, alpha: out, templateBaseline: tmplBaseline,
      inkLeft: x0 - ix0, inkTop: y0 - iy0, inkRight: x1 + 1 - ix0, inkBottom: y1 + 1 - iy0,
      pageBboxMm: [(left + x0) / PPM, (top + y0) / PPM, (left + x1 + 1) / PPM, (top + y1 + 1) / PPM],
    };
  }

  /** Everything for one photo: {page, glyphs, empty, boxes, rectified, errorMm}. */
  function extractPage(gray, det) {
    const rect = rectify(gray, det.H);
    const norm = flatten(rect);
    const ps = prefixSums(norm);
    const glyphs = [], empty = [], boxes = [];
    let snapped = 0;
    for (const slot of slots()) {
      const { dx, dy, ok } = snapBox(ps, slot);
      if (ok) snapped++;
      const g = extractBox(norm, slot, dx, dy);
      boxes.push({ slot, dx, dy, found: !!g });
      if (g) { g.page = det.page; glyphs.push(g); } else empty.push(slot.char);
    }
    return { page: det.page, glyphs, empty, boxes, snapped, rectified: rect, errorMm: det.errorMm };
  }

  function median(a) { if (!a.length) return null; const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; }

  /** Combine the glyphs of all pages into a profile (without image encoding). */
  function buildProfile(glyphLists, name) {
    const glyphs = {};
    for (const list of glyphLists) for (const g of list) (glyphs[g.char] = glyphs[g.char] || []).push(g);
    const all = Object.values(glyphs).flat();
    let offset = median(all.filter((g) => BASELINE_CHARS.has(g.char)).map((g) => g.inkBottom - g.templateBaseline)) || 0;
    offset = Math.max(-3 * PPM, Math.min(3 * PPM, offset));
    for (const g of all) {
      const guide = g.templateBaseline + offset;
      g.baseline = BASELINE_CHARS.has(g.char) && Math.abs(g.inkBottom - guide) < 3.5 * PPM ? g.inkBottom : guide;
    }
    const height = (chars) => median(all.filter((g) => chars.includes(g.char)).map((g) => g.baseline - g.inkTop));
    let xh = height(XHEIGHT_CHARS), cap = height(CAPHEIGHT_CHARS);
    if (xh == null && cap == null) { xh = 5 * PPM; cap = 8 * PPM; } else if (xh == null) xh = cap * 0.62; else if (cap == null) cap = xh / 0.62;
    const ordered = {};
    for (const ch of CHARSET) if (glyphs[ch]) ordered[ch] = glyphs[ch].map((g, i) => ({ ...g, variant: i }));
    return { name, pxPerMm: PPM, xHeightPx: xh, capHeightPx: cap, glyphs: ordered, missing: CHARSET.filter((c) => !glyphs[c]) };
  }

  // ---------------------------------------------------------------- text
  const REPLACEMENTS = {
    "’": "'", "‘": "'", "‚": ",", "´": "'", "`": "'", "”": "“", "«": '"', "»": '"',
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-", "…": "...", " ": " ", " ": " ",
    " ": " ", "\t": "    ", "ẞ": "ß", "×": "x", "[": "(", "]": ")", "{": "(", "}": ")",
  };

  function normalizeText(t) {
    return Array.from(t.replace(/\r\n?/g, "\n")).map((c) => REPLACEMENTS[c] ?? c).join("");
  }

  function fallbackChain(ch) {
    const chain = [ch];
    if (ch === '"') chain.push("“", "''");
    else if (ch === "“") chain.push('"');
    else if (ch === "„") chain.push(",,");
    else if (ch === "ß") chain.push("ss");
    const base = ch.normalize("NFKD").replace(/[̀-ͯ]/g, "");
    if (base && base !== ch) chain.push(base);
    if (ch.toUpperCase() !== ch.toLowerCase()) {
      chain.push(ch === ch.toUpperCase() ? ch.toLowerCase() : ch.toUpperCase());
      if (base && base !== ch) chain.push(base === base.toUpperCase() ? base.toLowerCase() : base.toUpperCase());
    }
    return [...new Set(chain)];
  }

  function rng(seed) {
    let a = seed >>> 0;
    const uniform = () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
    let spare = null;
    const normal = () => {
      if (spare !== null) { const s = spare; spare = null; return s; }
      let u = 0, v = 0; while (u === 0) u = uniform(); v = uniform();
      const r = Math.sqrt(-2 * Math.log(u)); spare = r * Math.sin(2 * Math.PI * v); return r * Math.cos(2 * Math.PI * v);
    };
    return { uniform, normal, int: (n) => Math.floor(uniform() * n) };
  }

  // ---------------------------------------------------------------- paper
  const PAPER = {
    liniert: { spacing: 8.5 }, kariert: { spacing: 10 }, blanko: { spacing: 8.5 },
  };

  function paperSpec(style, spacing) {
    const s = { style, spacing: spacing || PAPER[style].spacing, left: 20, right: 15, top: 15, bottom: 15 };
    if (style === "kariert") s.spacing = Math.max(5, Math.round(s.spacing / 5) * 5);
    s.lines = [];
    let y = style === "kariert" ? s.top + s.spacing : s.top + s.spacing;
    for (; y <= PAGE_H - s.bottom + 1e-6; y += s.spacing) s.lines.push(y);
    s.textLeft = s.left + 1.5;
    s.textRight = PAGE_W - s.right - 1;
    return s;
  }

  // ---------------------------------------------------------------- layout
  const BASE_X_HEIGHT_MM = 2.9, CAP_RATIO = 0.62, KERN_STEP = 0.08;

  class Layout {
    /** profile: {xHeightPx, capHeightPx, glyphs: {char: [{width,height,baseline,inkLeft,inkRight,profileL,profileR,...}]}} */
    constructor(profile, opts) {
      this.p = profile;
      this.opts = Object.assign({ paper: "liniert", size: 1, jitter: 1, seed: 1 }, opts || {});
      this.paper = paperSpec(this.opts.paper, this.opts.lineSpacing);
      this.r = rng(this.opts.seed);
      this.j = Math.max(0, this.opts.jitter);
      const sp = this.paper.spacing;
      let k = Math.min(BASE_X_HEIGHT_MM / profile.xHeightPx, (CAP_RATIO * sp) / profile.capHeightPx);
      this.k = Math.min(k * this.opts.size, (0.45 * sp) / profile.xHeightPx);
      this.xh = profile.xHeightPx * this.k;
      this.missing = {};
      this.last = {};
    }

    n(sigma) { return this.j > 0 ? this.r.normal() * sigma * this.j : 0; }

    resolve(ch) {
      for (const cand of fallbackChain(ch)) if (Array.from(cand).every((c) => this.p.glyphs[c])) return cand;
      return null;
    }

    pick(ch) {
      const vs = this.p.glyphs[ch];
      if (vs.length === 1) return vs[0];
      const choices = vs.filter((v) => v.variant !== this.last[ch]);
      const g = (choices.length ? choices : vs)[this.r.int((choices.length ? choices : vs).length)];
      this.last[ch] = g.variant;
      return g;
    }

    sample(g, k, ys, side) {
      const prof = side ? g.profileR : g.profileL;
      return ys.map((y) => {
        const row = Math.round(g.baseline + y / k);
        if (row < 0 || row >= prof.length || prof[row] < 0) return NaN;
        return (prof[row] - g.inkLeft) * k;
      });
    }

    nextX(prev, g, k, xAfter) {
      const gap = this.xh * Math.max(0.08, 0.2 + this.n(0.04));
      const ys = [];
      for (let y = -4 * this.xh; y < 2 * this.xh; y += KERN_STEP) ys.push(y);
      const ra = this.sample(prev.g, prev.k, ys, 1).map((v) => (isNaN(v) ? -Infinity : prev.x + v));
      const lb = this.sample(g, k, ys, 0).map((v) => (isNaN(v) ? Infinity : v));
      const reach = Math.round((0.3 * this.xh) / KERN_STEP);
      let need = -Infinity;
      for (let i = 0; i < ys.length; i++) {
        if (lb[i] === Infinity) continue;
        let m = -Infinity;
        for (let s = -reach; s <= reach; s++) { const v = ra[i + s]; if (v !== undefined && v > m) m = v; }
        if (m > -Infinity && m - lb[i] > need) need = m - lb[i];
      }
      if (need === -Infinity) return xAfter + gap;
      const minW = Math.min((prev.g.inkRight - prev.g.inkLeft) * prev.k, (g.inkRight - g.inkLeft) * k);
      return Math.max(need + gap, xAfter - 0.4 * minW);
    }

    planWord(word) {
      const glyphs = [];
      let x = 0;
      for (const ch of Array.from(word)) {
        const sub = this.resolve(ch);
        if (sub === null) { this.missing[ch] = (this.missing[ch] || 0) + 1; x += this.xh * 0.6; continue; }
        if (sub !== ch) this.missing[ch] = (this.missing[ch] || 0) + 1;
        for (const c of Array.from(sub)) {
          const g = this.pick(c);
          const k = this.k * (1 + Math.max(-0.08, Math.min(0.08, this.n(0.03))));
          if (glyphs.length) x = this.nextX(glyphs[glyphs.length - 1], g, k, x);
          glyphs.push({ g, x, k, dy: this.n(0.07), angle: this.n(1.2), opacity: 1 - Math.abs(this.n(0.05)) });
          x += (g.inkRight - g.inkLeft) * k;
        }
      }
      return { glyphs, width: x };
    }

    space() { return this.xh * (0.95 + this.n(0.12)); }

    splitLong(word, avail) {
      const parts = [];
      let cur = "";
      for (const ch of Array.from(word)) {
        if (cur && this.planWord(cur + ch).width > avail) { parts.push(cur); cur = ch; } else cur += ch;
      }
      if (cur) parts.push(cur);
      return parts.map((p) => this.planWord(p));
    }

    lines(text) {
      const avail = this.paper.textRight - this.paper.textLeft, lines = [];
      for (const para of normalizeText(text).split("\n")) {
        let line = [], x = 0, pending = 0;
        for (const tok of para.split(/( +)/)) {
          if (!tok) continue;
          if (tok[0] === " ") { for (let i = 0; i < tok.length; i++) pending += this.space(); continue; }
          const w = this.planWord(tok);
          const pieces = w.width <= avail ? [w] : this.splitLong(tok, avail);
          for (const piece of pieces) {
            let start = line.length ? x + pending : Math.min(pending, Math.max(0, avail - piece.width));
            if (line.length && start + piece.width > avail) { lines.push(line); line = []; start = 0; }
            line.push({ x: start, word: piece });
            x = start + piece.width;
            pending = 0;
          }
          pending = 0;
        }
        lines.push(line);
      }
      while (lines.length && !lines[lines.length - 1].length) lines.pop();
      return lines;
    }

    /** Pages of draw instructions: {glyph, x, baseline (mm), w, h (mm), angle, opacity}. */
    pages(text) {
      const lines = this.lines(text), rows = this.paper.lines, pages = [];
      const count = Math.max(1, Math.ceil(lines.length / rows.length));
      for (let p = 0; p < count; p++) {
        const items = [];
        lines.slice(p * rows.length, (p + 1) * rows.length).forEach((line, i) => {
          if (!line.length) return;
          const base = rows[i] + this.n(0.12), slope = Math.tan((this.n(0.35) * Math.PI) / 180), x0 = this.paper.textLeft + Math.abs(this.n(0.5));
          for (const { x: wx, word } of line) {
            const wdy = this.n(0.15);
            for (const it of word.glyphs) {
              const x = x0 + wx + it.x, g = it.g;
              items.push({
                glyph: g, x: x - g.inkLeft * it.k, baseline: base + wdy + it.dy + (x - x0) * slope,
                w: g.width * it.k, h: g.height * it.k, below: (g.height - g.baseline) * it.k, angle: it.angle, opacity: it.opacity,
              });
            }
          }
        });
        pages.push(items);
      }
      return { pages, paper: this.paper, lines: lines.length, missing: this.missing };
    }
  }

  /** Left/right ink outline per pixel row (for optical letter spacing). */
  function glyphProfiles(g) {
    const L = new Int16Array(g.height).fill(-1), R = new Int16Array(g.height).fill(-1);
    for (let y = 0; y < g.height; y++) {
      for (let x = 0; x < g.width; x++) if (g.alpha[y * g.width + x] > 70) { L[y] = x; break; }
      for (let x = g.width - 1; x >= 0; x--) if (g.alpha[y * g.width + x] > 70) { R[y] = x; break; }
    }
    g.profileL = L; g.profileR = R;
    return g;
  }

  const api = {
    PAGE_W, PAGE_H, PPM, CHARSET, slots, markerCornersMm, UserError,
    image, shrink, components, homography, apply, matInv,
    findMarkers, detectPage, rectify, flatten, extractPage, buildProfile,
    normalizeText, fallbackChain, rng, paperSpec, Layout, glyphProfiles,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.HS = api;
})(typeof window !== "undefined" ? window : globalThis);
