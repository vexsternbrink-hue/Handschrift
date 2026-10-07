// End-to-end test of web/index.html in headless Chromium.
//   node web/tests/e2e.test.js <out_dir> <jspdf.umd.min.js> <photo.jpg> [<photo2.jpg>]
const path = require("path");
const fs = require("fs");
let pw;
try { pw = require("playwright"); } catch (e) { pw = require(path.join(process.env.PW_MODULE || "", "playwright")); }

(async () => {
  const [out, jspdfFile, ...photos] = process.argv.slice(2);
  const page_html = fs.readFileSync(path.join(__dirname, "..", "index.html"), "utf8");
  const wrapped = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body>${page_html}</body></html>`;
  const browser = await pw.chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 }, deviceScaleFactor: 1, acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) errors.push(m.text()); });
  await page.route("**/*", (route) => {
    const url = route.request().url();
    if (url.includes("jspdf")) return route.fulfill({ body: fs.readFileSync(jspdfFile), contentType: "text/javascript" });
    if (url.startsWith("http")) return route.abort();
    if (url.startsWith("file:") && url.endsWith("app")) return route.fulfill({ body: wrapped, contentType: "text/html" });
    return route.continue();
  });
  await page.goto("file:///app");
  await page.waitForSelector("#sheet-list canvas", { timeout: 15000 });
  await page.waitForTimeout(800);
  console.log("meta:", await page.textContent("#sheet-meta"));
  await page.screenshot({ path: path.join(out, "01_write.png") });

  // learn
  await page.click("#tab-learn");
  await page.setInputFiles("#photos", photos);
  await page.waitForSelector("#step-result:not([hidden])", { timeout: 60000 });
  await page.waitForFunction((n) => document.querySelectorAll(".result .status-ok, .result .status-err").length >= n, photos.length, { timeout: 60000 });
  for (const s of await page.$$eval(".result", (els) => els.map((e) => e.innerText.replace(/\n/g, " | ")))) console.log("result:", s);
  console.log("summary:", await page.textContent("#learn-summary"));
  await page.fill("#new-name", "Testperson");
  await page.screenshot({ path: path.join(out, "02_learn.png"), fullPage: true });
  await page.click("#save-profile");
  await page.waitForSelector("#view-write:not([hidden])", { timeout: 15000 });
  await page.waitForTimeout(1200);
  console.log("active profile:", await page.$eval("#profile-select", (s) => s.options[s.selectedIndex].text));
  await page.fill("#text", fs.readFileSync(path.join(__dirname, "..", "..", "examples", "texte", "erlkoenig.txt"), "utf8"));
  await page.waitForTimeout(1200);
  console.log("meta:", await page.textContent("#sheet-meta"));
  await page.screenshot({ path: path.join(out, "03_write_trained.png") });

  const [dl] = await Promise.all([page.waitForEvent("download", { timeout: 30000 }), page.click("#save-pdf")]);
  const pdfPath = path.join(out, "e2e.pdf");
  await dl.saveAs(pdfPath);
  console.log("pdf:", fs.statSync(pdfPath).size, "bytes");

  const [tl] = await Promise.all([page.waitForEvent("download", { timeout: 30000 }), (async () => { await page.click("#tab-learn"); await page.click("#save-template"); })()]);
  await tl.saveAs(path.join(out, "vorlage.pdf"));
  console.log("template pdf:", fs.statSync(path.join(out, "vorlage.pdf")).size, "bytes");

  await page.click("#tab-profiles");
  console.log("profiles:", (await page.innerText("#plist")).replace(/\n/g, " | "));
  await page.screenshot({ path: path.join(out, "04_profiles.png") });

  // reload: profile must survive (IndexedDB)
  await page.reload();
  await page.waitForTimeout(2500);
  console.log("after reload:", await page.$$eval("#profile-select option", (o) => o.map((x) => x.text).join(", ")), "selected:", await page.$eval("#profile-select", (s) => s.options[s.selectedIndex].text));

  await page.setViewportSize({ width: 400, height: 860 });
  await page.click("#tab-write");
  await page.waitForTimeout(1200);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  console.log("phone horizontal overflow:", overflow);
  await page.screenshot({ path: path.join(out, "05_phone.png") });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.setViewportSize({ width: 820, height: 1180 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(out, "06_ipad_dark.png") });

  console.log("errors:", errors.length ? errors : "none");
  await browser.close();
  process.exit(errors.length || overflow ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
