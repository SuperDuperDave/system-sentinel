// Run against fixture-server.py after building dashboard, with TOKEN_FILE, PORT and NODE_PATH.
// Capture routes are synthetic; the app's sign-in, navigation and UI are real.
const { chromium } = require('playwright');
const fs = require('fs');
if (!process.env.TOKEN_FILE) throw Error('TOKEN_FILE must name the fixture server token');
const token = fs.readFileSync(process.env.TOKEN_FILE, 'utf8').trim();
const base = `http://127.0.0.1:${process.env.PORT || '8021'}`;
const name = 'capture-20260924T120000Z.zip';
const at = '2026-09-24T11:59:59.0000000Z';
const contents = {
  capture: { name, file_bytes: 12345, captured_at: '2026-09-24T12:00:00Z', version: '1.9.26', unredacted: true, saved_redaction_gaps: [] },
  readings: [
    { reading: 'crash', outcome: 'ok', bytes: 850, observed_by: 'signals', params: { count: 20 } },
    { reading: 'whea', outcome: 'empty', bytes: 225 },
  ],
  omitted: ['whea_record'], omitted_count: 1, unavailable: ['composed.md'], scope: 'Manifest index only',
};
const reading = {
  capture: contents.capture, member: { ...contents.readings[0], saved_redacted: [] },
  reading: { reading: 'crash', outcome: 'ok', count: 1, asked_at: at, took_ms: 5, warnings: [], redacted: ['host'],
    sections: [{ name: 'records', class: 'raw', data: [{ Log: 'System', RecordId: 77, MachineName: '<host>', Message: 'Synthetic saved stop' }] }],
    method: { kind: 'synthetic' }, params: { count: 20 } },
  warnings: ['Held observation from this capture; no new machine reading was taken.'],
};
function assert(ok, message) { if (!ok) throw Error(message); }
(async () => {
  const browser = await chromium.launch();
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    page.setDefaultTimeout(10000);
    const requests = { manifest: 0, crash: 0, whea: 0, live: 0 };
    await page.route('**/api/readings/**', async route => { requests.live++; await route.continue(); });
    await page.route(`**/api/captures/${name}/manifest`, async route => {
      requests.manifest++;
      await route.fulfill({ json: contents });
    });
    await page.route(`**/api/captures/${name}/readings/crash`, async route => {
      requests.crash++;
      await route.fulfill({ json: reading });
    });
    await page.route(`**/api/captures/${name}/readings/whea`, async route => {
      requests.whea++;
      await route.fulfill({ status: 422, json: { detail: 'member_unreadable: Saved member damaged' } });
    });
    await page.route('**/api/captures', async route => route.fulfill({ json: { captures: [{ name, bytes: 12345,
      created_at: '2026-09-24T12:00:00Z', manifest: { status: 'read', captured_at: '2026-09-24T12:00:00Z', unredacted: true,
        readings: 2, omitted: 1, unavailable: ['composed.md'], outcomes: { ok: 1, empty: 1 } } }] } }));
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#token') || document.querySelector('nav[aria-label="Views"]'));
    if (await page.locator('#token').count()) {
      await page.locator('#token').fill(token);
      await page.locator('button[type=submit]').click();
    }
    const nav = page.locator('nav[aria-label="Views"]:visible').first();
    await nav.waitFor();
    const stack = nav.locator('button', { hasText: 'Stack' }).first();
    if (!(await stack.isVisible())) await nav.locator('summary').click();
    await stack.click();
    const disclosure = page.getByText('Contents · saved readings', { exact: true });
    await disclosure.waitFor();
    const liveBefore = requests.live;
    assert(requests.manifest === 0 && requests.crash === 0, 'Capture content was fetched before opening');
    await disclosure.scrollIntoViewIfNeeded();
    const top = await disclosure.evaluate(el => el.getBoundingClientRect().top);
    await disclosure.click();
    await page.getByText(/Download the ZIP for every saved member/).waitFor();
    assert(requests.manifest === 1 && requests.crash === 0, 'Contents did not read exactly one saved index');
    assert(Math.abs(await disclosure.evaluate(el => el.getBoundingClientRect().top) - top) < 4, 'Opening Contents moved its control');
    assert(await page.getByText('original ZIP was saved unredacted; this view masks known identifiers').count() === 1,
      'Unredacted saved-file state was hidden');
    const crash = page.getByRole('button', { name: 'crash', exact: true });
    await crash.click();
    await page.getByText(/taken 2026-09-24T11:59:59/).waitFor();
    assert(requests.crash === 1 && requests.live === liveBefore,
      `Opening a saved reading asked Windows or fetched the wrong member: ${JSON.stringify({ requests, liveBefore })}`);
    assert(await page.locator('pre').filter({ hasText: 'Synthetic saved stop' }).count() === 0,
      'Complete saved JSON rendered while its disclosure was closed');
    await page.getByText('Complete saved reading · JSON').click();
    const raw = page.locator('pre').filter({ hasText: 'Synthetic saved stop' }).first();
    await raw.waitFor();
    assert((await raw.textContent()).includes('"MachineName": "<host>"'), 'Saved raw projection was not inspectable');
    if (process.env.SCREENSHOT_DIR) await page.locator('details').filter({ hasText: 'Contents · saved readings' }).first().screenshot({
      path: `${process.env.SCREENSHOT_DIR}/capture-reading-${width}.png`,
    });
    const whea = page.getByRole('button', { name: 'whea', exact: true });
    await whea.click();
    await page.getByText(/Saved reading unavailable: 422: member_unreadable/).waitFor();
    assert(requests.whea === 1 && requests.live === liveBefore, 'A refused saved member triggered a live reading');
    assert(await page.locator(`a[href="/api/captures/${name}"]`).count() === 1, 'ZIP download disappeared');
    assert(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth) === 0,
      `Capture Contents overflows at ${width}px`);
    if (process.env.SCREENSHOT_DIR) await page.locator('details').filter({ hasText: 'Contents · saved readings' }).first().screenshot({
      path: `${process.env.SCREENSHOT_DIR}/capture-contents-${width}.png`,
    });
    console.log(`capture contents ${width}px: passed; requests ${JSON.stringify(requests)}`);
    await page.close();
  }
  await browser.close();
})().catch(error => { console.error(error); process.exitCode = 1; });
