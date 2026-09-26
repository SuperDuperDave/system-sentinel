// Renders dashboard views on the synthetic fixture server at desktop (1440x900) and phone
// (390x844) widths and saves one PNG per shot and width: the first screen a person sees, and with
// FULL=1 the whole scrolled page beside it. Reusable by any branch that changes the interface.
//
// Run, with fixture-server.py serving on PORT (see ../README.md, "How to reproduce"):
//   NODE_PATH=<scratch>/node_modules TOKEN_FILE=<scratch>/token PORT=8021 OUT=<dir> \
//     node docs/screens/fixtures/capture-views.cjs [shot ...]
//
// With no arguments every shot below is taken. `node capture-views.cjs --list` prints the names.
// Either `playwright` or `playwright-core` on the module path works; CHROMIUM names a browser
// executable when the package's own download is missing (a cached one under ~/.cache/ms-playwright).
// Env: WIDTHS=1440,390  SCALE=1  FULL=1  MAX_FULL=6000 (px cap on a full-page shot's height).
const fs = require('fs');
const path = require('path');

let chromium;
try { ({ chromium } = require('playwright')); } catch { ({ chromium } = require('playwright-core')); }

const port = process.env.PORT || '8021';
const base = `http://127.0.0.1:${port}`;
const out = process.env.OUT || '.';
const token = process.env.TOKEN || (process.env.TOKEN_FILE ? fs.readFileSync(process.env.TOKEN_FILE, 'utf8').trim() : null);
const widths = (process.env.WIDTHS || '1440,390').split(',').map(Number);
const scale = Number(process.env.SCALE || 1);
const full = process.env.FULL === '1';
const maxFull = Number(process.env.MAX_FULL || 6000);
const HEIGHT = { 1440: 900, 390: 844 };

async function settled(page, timeout = 60000) {
  await page.waitForLoadState('networkidle', { timeout }).catch(() => {});
  await page.waitForFunction(() => !/taking the reading|asking windows|reading…/i.test(document.body.innerText), null, { timeout }).catch(() => {});
  await page.waitForTimeout(400);
}

async function click(page, selector, text) {
  const target = page.locator(selector, text ? { hasText: text } : undefined).first();
  if (!(await target.count())) return false;
  await target.scrollIntoViewIfNeeded();
  await target.click();
  await settled(page, 20000);
  return true;
}

const view = (id) => async (page) => { await page.goto(`${base}/?view=${id}`); await settled(page); };

// A shot is a name and how to reach its state from a signed-in tab. Views come first, in nav order;
// states that need a click follow. Add a shot here rather than writing another capture script.
const SHOTS = {
  'sign-in': { signedOut: true, reach: async (page) => { await page.goto(base); await page.waitForSelector('#token'); } },
  home: { reach: view('home') },
  // Home after the person has opened the stop door: its mark is acknowledged, the others remain.
  'home-after': { reach: async (page) => { await view('stopped')(page); await view('home')(page); } },
  // Needs a fixture server started with SENTINEL_FIXTURE_HOME_GAPS=1.
  'home-gaps': { reach: view('home') },
  stopped: { reach: view('stopped') },
  'stopped-changes': {
    reach: async (page) => {
      await view('stopped')(page);
      await click(page, 'main button', 'Read what changed before');
      await page.locator('#step-changes-title').scrollIntoViewIfNeeded();
      await page.evaluate(() => window.scrollBy(0, -60));
    },
  },
  'stopped-older': {
    reach: async (page) => {
      await view('stopped')(page);
      await click(page, 'main nav button', 'MEMORY_MANAGEMENT');
    },
  },
  programs: { reach: view('programs') },
  'programs-open': {
    reach: async (page) => {
      await view('programs')(page);
      if (await click(page, 'main li > button[aria-expanded]', 'example.exe')) await page.evaluate(() => window.scrollBy(0, 200));
    },
  },
  record: { reach: view('record') },
  errors: { reach: view('errors') },
  machine: { reach: view('machine') },
  performance: { reach: view('performance') },
  space: { reach: view('space') },
  diagnostics: { reach: view('diagnostics') },
  signals: { reach: view('signals') },
  stack: { reach: view('stack') },
  agents: { reach: view('agents') },
  'record-open': {
    reach: async (page) => {
      await view('record')(page);
      await click(page, 'button', 'The system has rebooted without cleanly shutting down first');
      await click(page, 'button', 'The record before this');
      const open = page.locator('li').filter({ has: page.locator('button[aria-expanded="true"]') }).first();
      if (await open.count()) { await open.scrollIntoViewIfNeeded(); await page.evaluate(() => window.scrollBy(0, -80)); }
    },
  },
  'diagnostics-memory': {
    reach: async (page) => {
      await view('diagnostics')(page);
      await page.locator('main button', { hasText: 'Take the reading' }).nth(2).click(); // PCIe, Power, Memory
      await settled(page, 20000);
      await page.locator('#panel-memory').scrollIntoViewIfNeeded();
      await page.evaluate(() => window.scrollBy(0, -40));
    },
  },
  'nav-open': {
    reach: async (page) => {
      await view('record')(page);
      const summary = page.locator('nav[aria-label="Doors and places"] summary:visible');
      if (await summary.count()) { await summary.click(); await page.waitForTimeout(300); }
    },
  },
  devices: {
    reach: async (page) => {
      await view('record')(page);
      const summary = page.locator('nav[aria-label="Doors and places"] summary:visible');
      if (await summary.count()) await summary.click();
      await click(page, 'button:visible', 'Sign in another device');
    },
  },
  'space-lab': { reach: async (page) => { await page.goto(`${base}/space-atlas-lab/index.html`); await settled(page); } },
};

async function signIn(context) {
  const page = await context.newPage();
  await page.goto(base);
  await page.waitForSelector('#token', { timeout: 15000 });
  await page.fill('#token', token);
  await page.click('button[type=submit]');
  await page.waitForSelector('nav[aria-label="Doors and places"]:visible', { timeout: 20000 });
  return page;
}

(async () => {
  if (process.argv.includes('--list')) { console.log(Object.keys(SHOTS).join('\n')); return; }
  const names = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(SHOTS);
  const unknown = names.filter((name) => !SHOTS[name]);
  if (unknown.length) throw new Error(`unknown shot(s): ${unknown.join(', ')}; --list names them`);
  if (!token && names.some((name) => !SHOTS[name].signedOut)) throw new Error('set TOKEN or TOKEN_FILE');
  fs.mkdirSync(out, { recursive: true });

  const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
  console.log('chromium', browser.version());
  try {
    for (const width of widths) {
      const viewport = { width, height: HEIGHT[width] || 900 };
      const signedOut = await browser.newContext({ viewport, deviceScaleFactor: scale, reducedMotion: 'reduce' });
      const signedIn = await browser.newContext({ viewport, deviceScaleFactor: scale, reducedMotion: 'reduce' });
      let page = null;
      for (const name of names) {
        const shot = SHOTS[name];
        const tab = shot.signedOut ? await signedOut.newPage() : (page ??= await signIn(signedIn));
        await shot.reach(tab);
        const sw = await tab.evaluate(() => document.documentElement.scrollWidth);
        const sh = await tab.evaluate(() => document.documentElement.scrollHeight);
        const file = path.join(out, `${name}-${width}.png`);
        await tab.screenshot({ path: file });
        let note = '';
        if (full) {
          const height = Math.min(sh, maxFull);
          await tab.screenshot({ path: path.join(out, `${name}-${width}-full.png`), fullPage: true, clip: height === sh ? undefined : { x: 0, y: 0, width, height } });
          note = height < sh ? ` (full shot cut at ${height}px)` : '';
        }
        console.log(`${name} @${width}: scrollWidth=${sw} ${sw === width ? 'OK' : '!! OVERFLOW'} page height=${sh}${note}`);
        if (shot.signedOut) await tab.close();
      }
      await signedOut.close();
      await signedIn.close();
    }
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error('capture-views failed:', error.stack || error.message); process.exit(1); });
