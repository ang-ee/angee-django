// Fidelity capture — the method for lifting fine-grained look&feel + placement
// from the live console mockup without hand-listing every detail.
//
// For a given screen it records two ground-truth artifacts:
//   1. a full screenshot (the visual truth)
//   2. a placement-aware STRUCTURAL EXTRACT (JSON): every visible control and
//      heading with its label, its layout BAND (top control strip / body /
//      bottom bar) and SIDE (left / right). This captures facts like
//      "Save and Discard sit in the TOP band, left group" mechanically.
//
// Run a lifted surface through the same extractor and diff the two: any
// placement / ordering / missing-control mismatch surfaces automatically,
// so "weak copy" drift (e.g. action buttons under the form instead of on top)
// can't pass silently.
//
// Usage:
//   node .agents/tools/fidelity-capture.mjs <base-url> <route> <name> [--hash]
//   node .agents/tools/fidelity-capture.mjs http://127.0.0.1:5174 /notes/1 form --hash
// Output: examples/notes-angee/e2e/test-results/fidelity/<name>.{png,json}

import { createRequire } from "node:module";
import { mkdirSync, writeFileSync } from "node:fs";

// Resolve Playwright from the e2e package (the only place it's installed),
// so this tool runs from any cwd.
const require = createRequire(
  "/Users/alexis/Work/angee/angee-django/examples/notes-angee/e2e/package.json",
);
const { chromium } = require("@playwright/test");

const [baseUrl, route, name, ...flags] = process.argv.slice(2);
if (!baseUrl || !route || !name) {
  console.error("usage: fidelity-capture.mjs <base-url> <route> <name> [--hash]");
  process.exit(2);
}
const hash = flags.includes("--hash");
const dirty = flags.includes("--dirty"); // type into the form to surface dirty-only controls
const storageState = (flags.find((f) => f.startsWith("--storage=")) || "").split("=")[1]; // capture an authenticated app screen
const login = (flags.find((f) => f.startsWith("--login=")) || "").split("=")[1]; // live-login as <user> on baseUrl/login before capturing
const outDir =
  "/Users/alexis/Work/angee/angee-django/examples/notes-angee/e2e/test-results/fidelity";
mkdirSync(outDir, { recursive: true });

const url = hash ? `${baseUrl}/#${route}` : `${baseUrl}${route}`;

const EXTRACT = () => {
  const W = window.innerWidth, H = window.innerHeight;
  const bandOf = (r) => (r.top < H * 0.18 ? "top" : r.bottom > H * 0.86 ? "bottom" : "body");
  const sideOf = (r) => (r.left + r.width / 2 < W / 2 ? "left" : "right");
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && r.top < H && r.bottom > 0 && s.visibility !== "hidden" && s.display !== "none";
  };
  const label = (el) =>
    (el.getAttribute("aria-label") || el.textContent || el.getAttribute("placeholder") || el.title || "")
      .trim().replace(/\s+/g, " ").slice(0, 48);
  const controls = [...document.querySelectorAll(
    "button, a[href], [role=button], [role=tab], [role=menuitem], input, select, textarea"
  )].filter(visible).map((el) => {
    const r = el.getBoundingClientRect();
    return {
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute("role") || null,
      type: el.getAttribute("type") || null,
      label: label(el),
      band: bandOf(r),
      side: sideOf(r),
      x: Math.round(r.x), y: Math.round(r.y),
    };
  }).filter((c) => c.label || c.tag === "input" || c.tag === "textarea");
  const headings = [...document.querySelectorAll("h1,h2,h3")].filter(visible).map((h) => {
    const r = h.getBoundingClientRect();
    return { level: h.tagName.toLowerCase(), text: h.textContent.trim().replace(/\s+/g, " ").slice(0, 64), band: bandOf(r) };
  });
  // band summary: ordered control labels per band/side — the placement fingerprint
  const summary = {};
  for (const c of controls) {
    const k = `${c.band}:${c.side}`;
    (summary[k] ||= []).push(c.label || `<${c.tag}${c.type ? " " + c.type : ""}>`);
  }
  return { viewport: { W, H }, headings, summary, controls };
};

const browser = await chromium.launch();
const page = await (await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
  ...(storageState ? { storageState } : {}),
})).newPage();
try {
  if (login) {
    await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded", timeout: 15000 });
    await page.getByLabel("Username").fill(login);
    await page.getByLabel("Password").fill(login);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL((u) => !u.pathname.includes("/login"), { timeout: 12000 });
  }
  await page.goto(url, { waitUntil: "networkidle", timeout: 20000 });
  await new Promise((r) => setTimeout(r, 1200));
  if (dirty) {
    // dirty the form so dirty-only controls (Save/Discard) render
    const field = page.locator("input:visible, textarea:visible").first();
    await field.click().catch(() => {});
    await field.type(" .").catch(() => {});
    await new Promise((r) => setTimeout(r, 600));
  }
  const data = await page.evaluate(EXTRACT);
  data.url = url;
  data.name = name;
  await page.screenshot({ path: `${outDir}/${name}.png`, fullPage: false });
  writeFileSync(`${outDir}/${name}.json`, JSON.stringify(data, null, 2));
  // print the placement fingerprint to stdout
  console.log(`# ${name}  (${url})`);
  console.log("headings:", data.headings.map((h) => `${h.level}/${h.band}:${h.text}`).join(" | ") || "(none)");
  for (const [k, v] of Object.entries(data.summary)) console.log(`${k.padEnd(12)} ${v.join(" · ")}`);
} catch (e) {
  console.error("capture error:", e.message.slice(0, 160));
} finally {
  await browser.close();
}
