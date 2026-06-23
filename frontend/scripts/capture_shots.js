#!/usr/bin/env node
/**
 * Headless Playwright capture for Fashion Archive preflight.
 * Usage: node capture_shots.js <output_dir> [frontend_url]
 *
 * Saves 4 PNGs to <output_dir>/:
 *   01_landing.png          empty state / hero
 *   02_search_results.png   grid for "sheer black evening looks"
 *   03_video_modal.png      modal opened on first result
 *   04_timeline.png         /timeline page
 *
 * Exits 0 on success, 1 if the frontend is unreachable (preflight skips gracefully).
 */

const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");

const outDir = process.argv[2];
const BASE = process.argv[3] || "http://localhost:3000";

if (!outDir) {
  console.error("Usage: node capture_shots.js <output_dir> [frontend_url]");
  process.exit(1);
}

fs.mkdirSync(outDir, { recursive: true });

(async () => {
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
  } catch (e) {
    console.error("Playwright browser launch failed:", e.message);
    process.exit(1);
  }

  const page = await browser.newPage();
  await page.setViewportSize({ width: 1440, height: 900 });

  // ── 1. Landing / empty state ──────────────────────────────────────────────
  try {
    await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 15000 });
  } catch (e) {
    console.error(`Frontend not reachable at ${BASE}: ${e.message}`);
    await browser.close();
    process.exit(1);
  }
  // wait for hero or chips
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.join(outDir, "01_landing.png"), fullPage: false });
  console.log("  ✓ 01_landing.png");

  // ── 2. Search results grid ────────────────────────────────────────────────
  const input = page.locator("input[type=text]").first();
  await input.fill("sheer black evening looks");
  await input.press("Enter");
  // wait for cards to render
  try {
    await page.waitForSelector("[style*='border-radius: 8px']", { timeout: 12000 });
  } catch (_) {}
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(outDir, "02_search_results.png"), fullPage: false });
  console.log("  ✓ 02_search_results.png");

  // ── 3. Video modal ────────────────────────────────────────────────────────
  // Click the thumbnail of the first result card (the clickable area)
  try {
    const thumb = page.locator("[style*='aspect-ratio: 16/9']").first();
    await thumb.click({ timeout: 5000 });
    await page.waitForTimeout(1200);
    await page.screenshot({ path: path.join(outDir, "03_video_modal.png"), fullPage: false });
    console.log("  ✓ 03_video_modal.png");
    // close modal
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
  } catch (_) {
    // modal may not open if HLS not available in headless — still capture
    await page.screenshot({ path: path.join(outDir, "03_video_modal.png"), fullPage: false });
    console.log("  ✓ 03_video_modal.png (modal did not open — HLS unavailable headless)");
  }

  // ── 4. Timeline page ──────────────────────────────────────────────────────
  try {
    await page.goto(`${BASE}/timeline`, { waitUntil: "domcontentloaded", timeout: 12000 });
    await page.waitForTimeout(1000);
    await page.screenshot({ path: path.join(outDir, "04_timeline.png"), fullPage: false });
    console.log("  ✓ 04_timeline.png");
  } catch (_) {
    await page.screenshot({ path: path.join(outDir, "04_timeline.png"), fullPage: false });
    console.log("  ✓ 04_timeline.png (best effort)");
  }

  await browser.close();
  process.exit(0);
})();
