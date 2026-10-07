// Render a text with an exported profile in the real page and save screenshots + PDF.
//   node web/tests/render_profile.js <out_dir> <jspdf.umd.min.js> <profile.json> <text.txt> [html]
const path = require("path");
const fs = require("fs");
const pw = require(path.join(process.env.PW_MODULE || "", "playwright"));
(async () => {
  const [out, jspdfFile, profileFile, textFile, htmlFile] = process.argv.slice(2);
  const html = fs.readFileSync(htmlFile || path.join(__dirname, "..", "index.html"), "utf8");
  const wrapped = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>${html}</body></html>`;
  const browser = await pw.chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1180, height: 1400 }, acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { const t = m.text(); if (m.type() === "error" && !t.startsWith("Failed to load")) errors.push(t); if (t.startsWith("[hs]")) console.log(t); });
  await page.route("**/*", (route) => {
    const url = route.request().url();
    if (url.includes("jspdf")) return route.fulfill({ body: fs.readFileSync(jspdfFile), contentType: "text/javascript" });
    if (url.startsWith("http")) return route.abort();
    if (url.endsWith("/app")) return route.fulfill({ body: wrapped, contentType: "text/html" });
    return route.continue();
  });
  await page.goto("file:///app");
  await page.waitForSelector("#sheet-list canvas");
  await page.click("#tab-profiles");
  await page.setInputFiles("#import-file", profileFile);
  await page.waitForTimeout(1500);
  await page.click("#tab-write");
  const opts = await page.$$eval("#profile-select option", (o) => o.map((x) => [x.value, x.text]));
  const mine = opts.find(([, t]) => !t.includes("Beispiel"));
  await page.selectOption("#profile-select", mine[0]);
  await page.fill("#text", fs.readFileSync(textFile, "utf8"));
  await page.waitForTimeout(2500);
  const sheet = await page.$("#sheet-list .sheet");
  await sheet.screenshot({ path: path.join(out, "sheet1.png") });
  console.log("meta:", await page.textContent("#sheet-meta"));
  console.log("errors:", errors.length ? errors : "none");
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
