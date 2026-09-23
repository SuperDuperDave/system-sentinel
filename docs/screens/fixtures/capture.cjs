// Captures the five sanitized screenshots for docs/screens/ against the fixture server on 8021.
// Run: TOKEN=... OUT=... node screens-capture.cjs
const { chromium } = require('playwright');

const port = process.env.PORT || '8021';
const out = process.env.OUT || '.';
const token = process.env.TOKEN;

async function waitSettled(page, timeout = 90000) {
  await page.waitForFunction(() => !document.body.innerText.toLowerCase().includes('taking the reading'), null, { timeout }).catch(() => {});
  await page.waitForTimeout(500);
}

async function signIn(page) {
  await page.goto(`http://127.0.0.1:${port}/`);
  await page.waitForSelector('#token', { timeout: 15000 });
}

async function openView(page, name) {
  const nav = page.locator('nav[aria-label="Views"]:visible').first();
  const choice = nav.locator('button', { hasText: name }).first();
  if (!(await choice.isVisible())) await nav.locator('summary').click();
  await choice.click();
  await page.waitForTimeout(300);
  await waitSettled(page);
}

async function checkOverflow(page, label, width) {
  const sw = await page.evaluate(() => document.documentElement.scrollWidth);
  console.log(`${label}: scrollWidth=${sw} viewport=${width} ${sw === width ? 'OK' : '!! OVERFLOW'}`);
}

async function openKernelPower41(page) {
  // The row whose message names the Kernel-Power provider's own text.
  const row = page.locator('button', { hasText: 'The system has rebooted without cleanly shutting down first' }).first();
  await row.scrollIntoViewIfNeeded();
  await row.click();
  await page.waitForTimeout(300);
  const before = page.locator('button', { hasText: 'The record before this' }).first();
  await before.click();
  await waitSettled(page, 20000);
}

(async () => {
  const browser = await chromium.launch();
  console.log('chromium', browser.version());

  // ---- desktop ----
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
    const page = await ctx.newPage();
    await signIn(page);
    await page.fill('#token', token);
    await page.click('button[type=submit]');
    await page.waitForSelector('nav[aria-label="Views"]:visible', { timeout: 20000 });

    await openView(page, 'Hardware errors');
    // The whea reading decodes every record in the capped set through a real subprocess per
    // record; give it the room the earlier probe measured (~33s at count=30).
    await waitSettled(page, 90000);
    const metrics = await page.evaluate(() => {
      const h2s = [...document.querySelectorAll('h2')].map((h) => ({ el: h, text: h.textContent.trim() }));
      const statusH2 = h2s.find((h) => h.text === 'Status');
      const trace = document.querySelector('svg[role="img"]');
      const ol = [...document.querySelectorAll('ol')].pop();
      const firstRow = ol ? ol.querySelector('li') : null;
      const r = (el) => (el ? el.getBoundingClientRect() : null);
      return { status: r(statusH2 ? statusH2.el : null), trace: r(trace), firstRow: r(firstRow) };
    });
    // Status is the earliest of the three, so it is the binding constraint: scroll no further
    // than leaves its heading just inside the top edge. Only then does more scroll (up to that
    // cap) help bring the first record row into view.
    let scrollBy = 0;
    if (metrics.firstRow && metrics.firstRow.bottom > 900) {
      const desired = metrics.firstRow.top - 900 + 56; // enough of the row to read, not necessarily all of it
      const cap = metrics.status ? Math.max(0, metrics.status.top - 8) : 0;
      scrollBy = Math.max(0, Math.min(desired, cap));
    }
    if (scrollBy > 0) {
      await page.evaluate((y) => window.scrollTo(0, y), scrollBy);
      await page.waitForTimeout(200);
    }
    console.log('desktop-hardware-errors scrollBy', scrollBy, JSON.stringify(metrics));
    await checkOverflow(page, 'desktop-hardware-errors', 1440);
    await page.screenshot({ path: `${out}/desktop-hardware-errors.png` });

    await openView(page, 'Record');
    await page.click('button:has-text("Every level")');
    await page.click('button:has-text("last 200")');
    await waitSettled(page, 20000);
    await openKernelPower41(page);
    // Bring the opened row (and its expanded "record before" block) into frame.
    const openRow = page.locator('li').filter({ has: page.locator('button[aria-expanded="true"]') }).first();
    await openRow.scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollBy(0, -80)); // leave room above so the row's own facts are visible too
    await page.waitForTimeout(200);
    await checkOverflow(page, 'desktop-record', 1440);
    await page.screenshot({ path: `${out}/desktop-record.png` });

    await ctx.close();
  }

  // ---- phone ----
  {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3 });
    const page = await ctx.newPage();
    await signIn(page);
    await checkOverflow(page, 'phone-sign-in', 390);
    await page.screenshot({ path: `${out}/phone-sign-in.png` });

    await page.fill('#token', token);
    await page.click('button[type=submit]');
    await page.waitForSelector('nav[aria-label="Views"]:visible', { timeout: 20000 });

    await openView(page, 'Hardware errors');
    await waitSettled(page, 90000);
    await page.evaluate(() => window.scrollTo(0, 0));
    await checkOverflow(page, 'phone-hardware-errors', 390);
    await page.screenshot({ path: `${out}/phone-hardware-errors.png` });

    await openView(page, 'Record');
    await waitSettled(page, 20000);
    // The phone view's default is "Critical and error" — the Kernel-Power 41 row is present there too.
    await openKernelPower41(page);
    const openRow = page.locator('li').filter({ has: page.locator('button[aria-expanded="true"]') }).first();
    await openRow.scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollBy(0, -8)); // the opened row at the top of the viewport
    await page.waitForTimeout(200);
    await checkOverflow(page, 'phone-record', 390);
    await page.screenshot({ path: `${out}/phone-record.png` });

    await ctx.close();
  }

  await browser.close();
})().catch((e) => {
  console.error('screens-capture failed:', e.stack || e.message);
  process.exit(1);
});
