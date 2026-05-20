#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

function parseArgs(argv) {
  const args = {
    url: "http://127.0.0.1:8509",
    out: "/tmp/spectral-mobile-check",
    mode: "mobile",
    sections: ["Home"],
  };
  for (let index = 2; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg.startsWith("--url=")) {
      args.url = arg.slice("--url=".length);
    } else if (arg === "--url") {
      args.url = argv[++index];
    } else if (arg.startsWith("--out=")) {
      args.out = arg.slice("--out=".length);
    } else if (arg === "--out") {
      args.out = argv[++index];
    } else if (arg.startsWith("--mode=")) {
      args.mode = arg.slice("--mode=".length);
    } else if (arg === "--mode") {
      args.mode = argv[++index];
    } else if (arg.startsWith("--sections=")) {
      args.sections = arg
        .slice("--sections=".length)
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean);
    } else if (arg === "--sections") {
      args.sections = String(argv[++index] || "")
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean);
    } else if (arg === "--help" || arg === "-h") {
      printHelp();
      process.exit(0);
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }
  if (!["mobile", "desktop", "both"].includes(args.mode)) {
    throw new Error("--mode must be one of: mobile, desktop, both");
  }
  return args;
}

function printHelp() {
  console.log(`Usage:
  node scripts/mobile_render_check.mjs --url=http://127.0.0.1:8509 --out=/tmp/spectral-mobile-check

Options:
  --url=URL              Running Streamlit base URL. Default: http://127.0.0.1:8509
  --out=DIR              Screenshot/report output directory. Default: /tmp/spectral-mobile-check
  --mode=mobile|desktop|both
  --sections=A,B,C       Mobile sections to select and capture. Default: Home

Required app env for mobile checks:
  STREAMLIT_MOBILE_UI_ENABLED=true
`);
}

function loadPlaywright() {
  try {
    return require("playwright");
  } catch (error) {
    console.error("Playwright is required for render checks.");
    console.error("Install it outside the repo if you do not want Node dependencies here:");
    console.error("  npm install --prefix /tmp/spectral-pw playwright");
    console.error("  NODE_PATH=/tmp/spectral-pw/node_modules node scripts/mobile_render_check.mjs --url=http://127.0.0.1:8509");
    throw error;
  }
}

async function launchChromium(chromium) {
  try {
    return await chromium.launch({ headless: true });
  } catch (firstError) {
    try {
      return await chromium.launch({ headless: true, channel: "chrome" });
    } catch (secondError) {
      secondError.message = [
        secondError.message,
        "",
        "Could not launch Playwright Chromium or local Chrome.",
        "If browsers are missing, run: npx playwright install chromium",
        `First launch error: ${firstError.message}`,
      ].join("\n");
      throw secondError;
    }
  }
}

function withLayoutParam(rawUrl, layout) {
  const url = new URL(rawUrl);
  url.searchParams.set("layout", layout);
  return url.toString();
}

function slug(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

async function waitForStreamlit(page) {
  await page.waitForSelector('[data-testid="stAppViewContainer"]', { timeout: 30000 });
  await page.waitForTimeout(1200);
}

async function scrollToTop(page) {
  await page.evaluate(() => {
    window.scrollTo(0, 0);
    for (const element of document.querySelectorAll("*")) {
      if (element instanceof HTMLElement && element.scrollTop > 0) {
        element.scrollTop = 0;
      }
    }
  });
  await page.waitForTimeout(800);
}

async function assertNoHorizontalOverflow(page, label) {
  const metrics = await page.evaluate(() => {
    const viewportWidth = window.innerWidth;
    const docWidth = document.documentElement.scrollWidth;
    const bodyWidth = document.body ? document.body.scrollWidth : 0;
    const offenders = Array.from(document.body.querySelectorAll("*"))
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName,
          classes: String(element.className || "").slice(0, 160),
          text: String(element.textContent || "").trim().replace(/\s+/g, " ").slice(0, 120),
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
        };
      })
      .filter((item) => item.width > 2 && item.right > viewportWidth + 2)
      .slice(0, 10);
    return { viewportWidth, docWidth, bodyWidth, offenders };
  });
  const overflow = Math.max(metrics.docWidth, metrics.bodyWidth) - metrics.viewportWidth;
  if (overflow > 2) {
    throw new Error(`${label}: horizontal overflow ${overflow}px\n${JSON.stringify(metrics.offenders, null, 2)}`);
  }
  return metrics;
}

async function assertMobileShell(page) {
  const combo = page.getByRole("combobox", { name: /navigate/i }).first();
  await combo.waitFor({ state: "visible", timeout: 15000 });
  const desktopSidebarButtons = await page.locator('[class*="st-key-sn_nav_"]').count();
  if (desktopSidebarButtons > 0) {
    throw new Error(`Mobile layout rendered ${desktopSidebarButtons} desktop sidebar nav buttons.`);
  }
}

async function selectedComboboxHasValue(page, section) {
  return await page.evaluate((target) => {
    return Array.from(document.querySelectorAll('[role="combobox"]')).some((element) => {
      const values = [
        element.textContent,
        element.getAttribute("aria-label"),
        element.getAttribute("value"),
        element.value,
      ];
      return values.some((value) => String(value || "").includes(target));
    });
  }, section);
}

async function selectSection(page, section) {
  const combo = page.getByRole("combobox", { name: /navigate/i }).first();
  if (!(await selectedComboboxHasValue(page, section))) {
    await combo.click();
    const option = page.getByRole("option", { name: section }).first();
    await option.waitFor({ state: "visible", timeout: 8000 });
    await option.click();
    await page.waitForTimeout(3200);
  }
  await page.waitForFunction(
    (target) =>
      Array.from(document.querySelectorAll('[role="combobox"]')).some((element) => {
        const values = [
          element.textContent,
          element.getAttribute("aria-label"),
          element.getAttribute("value"),
          element.value,
        ];
        return values.some((value) => String(value || "").includes(target));
      }),
    section,
    { timeout: 10000 },
  );
}

async function capture(page, outDir, name) {
  const screenshotPath = path.join(outDir, `${name}.png`);
  await page.screenshot({ path: screenshotPath, fullPage: true });
  return screenshotPath;
}

async function runMobile(browser, args) {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    userAgent:
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
  });
  const page = await context.newPage();
  const results = [];
  await page.goto(withLayoutParam(args.url, "mobile"), { waitUntil: "domcontentloaded", timeout: 30000 });
  await waitForStreamlit(page);
  await assertMobileShell(page);

  for (const section of args.sections) {
    await selectSection(page, section);
    await waitForStreamlit(page);
    await scrollToTop(page);
    await assertMobileShell(page);
    const overflow = await assertNoHorizontalOverflow(page, `mobile:${section}`);
    const screenshot = await capture(page, args.out, `mobile-${slug(section) || "home"}`);
    results.push({ mode: "mobile", section, screenshot, overflow });
  }

  await context.close();
  return results;
}

async function runDesktop(browser, args) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  await page.goto(withLayoutParam(args.url, "desktop"), { waitUntil: "domcontentloaded", timeout: 30000 });
  await waitForStreamlit(page);
  await scrollToTop(page);
  const sidebarNavButtons = await page.locator('[class*="st-key-sn_nav_"]').count();
  if (sidebarNavButtons < 1) {
    throw new Error("Desktop layout did not render sidebar navigation buttons.");
  }
  const overflow = await assertNoHorizontalOverflow(page, "desktop:Home");
  const screenshot = await capture(page, args.out, "desktop-home");
  await context.close();
  return [{ mode: "desktop", section: "Home", screenshot, overflow }];
}

async function main() {
  const args = parseArgs(process.argv);
  fs.mkdirSync(args.out, { recursive: true });
  const { chromium } = loadPlaywright();
  const browser = await launchChromium(chromium);
  const results = [];
  try {
    if (args.mode === "mobile" || args.mode === "both") {
      results.push(...(await runMobile(browser, args)));
    }
    if (args.mode === "desktop" || args.mode === "both") {
      results.push(...(await runDesktop(browser, args)));
    }
  } finally {
    await browser.close();
  }
  const reportPath = path.join(args.out, "report.json");
  fs.writeFileSync(reportPath, `${JSON.stringify({ url: args.url, results }, null, 2)}\n`);
  console.log(`Render check passed. Report: ${reportPath}`);
  for (const result of results) {
    console.log(`- ${result.mode} ${result.section}: ${result.screenshot}`);
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});
