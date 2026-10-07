// node web/tests/core.test.js <fixture_dir>
// Runs the browser pipeline on simulated photos and checks every glyph came
// from the right box.
const fs = require("fs");
const path = require("path");
const HS = require("../core.js");

const dir = process.argv[2];
const fixtures = JSON.parse(fs.readFileSync(path.join(dir, "fixtures.json"), "utf8"));
let failed = 0;
const iou = (a, b) => {
  const ix = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])), iy = Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
  const inter = ix * iy, union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter;
  return union > 0 ? inter / union : 0;
};
const lists = [];
for (const f of fixtures) {
  const raw = fs.readFileSync(path.join(dir, f.name + ".gray"));
  const data = new Float32Array(raw.length);
  for (let i = 0; i < raw.length; i++) data[i] = raw[i] / 255;
  const t0 = Date.now();
  const det = HS.detectPage(HS.image(f.w, f.h, data));
  const t1 = Date.now();
  const res = HS.extractPage(HS.image(f.w, f.h, data), det);
  const t2 = Date.now();
  const ious = res.glyphs.map((g) => iou(f.truth[g.char], g.pageBboxMm));
  const wrong = res.glyphs.filter((g, i) => ious[i] < 0.3).map((g) => g.char);
  const mean = ious.reduce((a, b) => a + b, 0) / ious.length;
  const ok = det.page === f.page && res.glyphs.length === HS.CHARSET.length && wrong.length === 0 && mean > 0.75;
  if (!ok) failed++;
  console.log(`${ok ? "OK  " : "FAIL"} ${f.name}: page ${det.page + 1} (${det.markers} markers, err ${det.errorMm.toFixed(2)} mm), ` +
    `${res.glyphs.length}/${HS.CHARSET.length} glyphs, empty [${res.empty.join(" ")}], snapped ${res.snapped}, ` +
    `mean IoU ${mean.toFixed(2)}, wrong [${wrong.join(" ")}], detect ${t1 - t0} ms, extract ${t2 - t1} ms`);
  lists.push(res.glyphs);
}
const prof = HS.buildProfile(lists, "test");
console.log(`profile: x-height ${(prof.xHeightPx / prof.pxPerMm).toFixed(2)} mm, cap ${(prof.capHeightPx / prof.pxPerMm).toFixed(2)} mm, missing [${prof.missing.join(" ")}]`);
for (const vs of Object.values(prof.glyphs)) vs.forEach(HS.glyphProfiles);
const lay = new HS.Layout(prof, { seed: 3 }).pages("Hallo Welt! Grüße aus Köln – 2,50 €.\n\n" + "Lorem ipsum dolor sit amet. ".repeat(80));
const avail = lay.paper.textRight;
const over = lay.pages.flat().filter((it) => it.x + it.w > avail + 3).length;
console.log(`layout: ${lay.pages.length} pages, ${lay.lines} lines, ${lay.pages.flat().length} glyphs, overflow ${over}, missing ${JSON.stringify(lay.missing)}`);
if (over || lay.pages.length < 2) failed++;
process.exit(failed ? 1 : 0);
