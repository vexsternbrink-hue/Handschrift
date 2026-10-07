// Exercises the AI review code paths with a mocked window.claude (sample/db/downloads).
//   node web/tests/ai_mock.test.js <out_dir> <jspdf.umd.min.js> <photo.jpg>
const path = require("path");
const fs = require("fs");
const pw = require(path.join(process.env.PW_MODULE || "", "playwright"));
(async () => {
  const [out, jspdfFile, photo] = process.argv.slice(2);
  const html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const wrapped = `<!doctype html><html><head><meta charset="utf-8"></head><body>${html}</body></html>`;
  const browser = await pw.chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1180, height: 1300 } });
  await ctx.addInitScript(() => {
    window.__calls = [];
    window.__saved = [];
    const sample = async () => ({ text: "", truncated: false });
    sample.limits = async () => ({ maxPromptBytes: 262144, images: { maxCount: 2, maxInputBytes: 2e7, mediaTypes: ["image/png"] } });
    sample.json = async (prompt, opts) => {
      window.__calls.push({ kind: prompt.includes("Zellen") ? "glyphs" : "page", images: (opts.images || []).length });
      await new Promise((r) => setTimeout(r, 50));
      if (prompt.includes("Zellen")) return prompt.includes("Zellen 1 ") ? { bad: [{ id: 3, reason: "abgeschnitten" }, { id: 40, reason: "Rahmenrest" }] } : { bad: [] };
      return { score: 7, letterSpacing: 0.8, wordSpacing: 1.1, size: 1.05, jitter: 0.9, badChars: ["x"], notes: "Buchstaben etwas zu weit auseinander." };
    };
    const downloads = { save: async ({ filename, data }) => { window.__saved.push({ filename, size: data.size }); return { status: "saved" }; } };
    window.claude = { use: async (n) => (n === "sample" ? sample : n === "downloads" ? downloads : null) };
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !m.text().startsWith("Failed to load")) errors.push(m.text()); });
  await page.route("**/*", (route) => {
    const url = route.request().url();
    if (url.includes("jspdf")) return route.fulfill({ body: fs.readFileSync(jspdfFile), contentType: "text/javascript" });
    if (url.startsWith("http")) return route.abort();
    if (url.endsWith("/app")) return route.fulfill({ body: wrapped, contentType: "text/html" });
    return route.continue();
  });
  await page.goto("file:///app");
  await page.waitForSelector("#sheet-list canvas");
  await page.waitForTimeout(500);
  console.log("ai box visible:", await page.isVisible("#ai-box"));
  await page.click("#tab-learn");
  await page.fill("#new-name", "KI-Test");
  await page.setInputFiles("#photos", [photo]);
  await page.waitForFunction(() => /KI-Prüfung/.test(document.getElementById("ai-review").textContent), null, { timeout: 60000 });
  console.log("review:", await page.textContent("#ai-review"));
  console.log("summary:", await page.textContent("#learn-summary"));
  await page.click("#save-profile");
  await page.waitForFunction(() => /Bewertung/.test(document.getElementById("ai-note").textContent), null, { timeout: 30000 });
  console.log("page check:", await page.textContent("#ai-note"));
  await page.click("#save-pdf");
  await page.waitForFunction(() => window.__saved.length > 0, null, { timeout: 30000 });
  console.log("saved:", JSON.stringify(await page.evaluate(() => window.__saved)));
  console.log("calls:", JSON.stringify(await page.evaluate(() => window.__calls)));
  await page.screenshot({ path: path.join(out, "ai_write.png") });
  console.log("errors:", errors.length ? errors : "none");
  await browser.close();
  process.exit(errors.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
