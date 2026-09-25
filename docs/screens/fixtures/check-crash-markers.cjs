// Run against fixture-server.py with TOKEN_FILE, PORT and NODE_PATH as in docs/screens/README.md.
// The two intercepted Crash answers are synthetic; the served dashboard and sign-in are real.
const { chromium } = require('playwright');
const fs = require('fs');
const base = `http://127.0.0.1:${process.env.PORT || '8021'}`;
if (!process.env.TOKEN_FILE) throw Error('TOKEN_FILE must name the fixture server token');
const token = fs.readFileSync(process.env.TOKEN_FILE, 'utf8').trim();
const at = '2026-09-05T18:30:00.0000000Z';
const row = (id, eventId, provider, time) => ({ RecordId: id, Id: eventId, ProviderName: provider, Log: 'System', LevelDisplayName: 'Error', TimeCreated: time, Message: 'Synthetic stop evidence', Properties: [] });
const start = row(1000, 12, 'Microsoft-Windows-Kernel-General', at);
const marker = row(1002, 6008, 'EventLog', '2026-09-05T18:30:15.0000000Z');
const section = (name, data, cls = 'raw') => ({ name, class: cls, data });
function envelope(name, sections, outcome = 'ok', warnings = [], count = null) {
  return { reading: name, params: {}, asked_at: '2026-09-24T12:03:00Z', took_ms: 2, outcome, method: { kind: 'synthetic' }, count, sections, error: null, warnings, redacted: [] };
}
const stop = {
  started_at: '2026-09-05T18:29:58.500Z', announced_at: null, stopped_at: '2026-09-05T18:12:44.113Z', reported_at: null,
  down_seconds: 1034, bugcheck: null, no_bugcheck_recorded: false, power: null, dump: null, dump_inventory_complete: true,
  last_record_before: null, last_record_collection: { outcome: 'not_requested', returned: 0, error: 'No Kernel-Power 41 anchored a pre-start lookup for this stop.' }, quiet_seconds: null,
  records: { start: 1000, power_41: null, eventlog_6008: 1002, wer_1001: null, report: [] },
};
const valid = envelope('crash', [section('records', [marker, start]), section('stops', [stop], 'derived')], 'ok', [], 1);
const uncertain = envelope('crash', [section('records', [row(1003, 1001, 'Microsoft-Windows-WER-SystemErrorReporting', '2026-09-05T18:30:20.0000000Z'), start]), section('stops', [], 'derived')], 'empty', ['System bug-check record 1003 was returned without a stop anchor; its filing time cannot place the stop in this session.'], 0);

(async () => {
  const browser = await chromium.launch({ headless: true });
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    let mode = 'valid';
    let crashRequests = 0;
    await page.route('**/api/readings/**', async route => {
      const name = new URL(route.request().url()).pathname.split('/').pop();
      if (name === 'crash') { crashRequests++; await route.fulfill({ json: mode === 'valid' ? valid : uncertain }); return; }
      await route.fulfill({ json: envelope(name, [], 'empty', [], 0) });
    });
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#token') || document.querySelector('nav[aria-label="Views"]'), null, { timeout: 15000 });
    if (await page.locator('#token').count()) {
      await page.locator('#token').fill(token);
      await page.locator('button[type=submit]').click();
    }
    const nav = page.locator('nav[aria-label="Views"]:visible').first();
    await nav.waitFor();
    const button = nav.locator('button', { hasText: 'Crashes' }).first();
    if (!(await button.isVisible())) await nav.locator('summary').click();
    await button.click();
    const stopButton = page.locator('button[aria-controls="stop-detail-0"]');
    await stopButton.waitFor();
    await page.getByText('Read from EventLog 6008').waitFor();
    await page.getByText('No Kernel-Power 41 returned').waitFor();
    await stopButton.click();
    await page.getByText('No Kernel-Power 41 anchored a pre-start lookup for this stop.').waitFor();
    if (await page.getByText('No stop established from the returned records').count()) throw Error('valid marker stop looked empty');
    const stopOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (stopOverflow > 0) throw Error(`stop horizontal overflow at ${width}: ${stopOverflow}`);
    if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/crash-marker-${width}.png` });
    mode = 'uncertain';
    await page.getByRole('button', { name: 'Take again' }).first().click();
    await page.getByText('No stop established from the returned records').waitFor();
    await page.getByRole('list', { name: 'Warnings and limits' }).waitFor();
    await page.getByText(/System bug-check record 1003 was returned without a stop anchor/).waitFor();
    if (await page.locator('button[aria-controls="stop-detail-0"]').count()) throw Error('old stop remained after a new empty answer');
    if (crashRequests !== 2) throw Error(`expected two deliberate Crash requests, got ${crashRequests}`);
    if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/crash-marker-uncertain-${width}.png` });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (overflow > 0) throw Error(`horizontal overflow at ${width}: ${overflow}`);
    await page.close();
  }
  await browser.close();
  process.stdout.write('Crash marker and uncertain-empty glass passed at 1440 and 390 px.\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
