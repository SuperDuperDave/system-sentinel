// Run against fixture-server.py after building dashboard, with TOKEN_FILE, PORT and NODE_PATH.
// Only Signals is synthetic. The shell, sign-in and navigation remain the real dashboard.
const { chromium } = require('playwright');
const fs = require('fs');
if (!process.env.TOKEN_FILE) throw Error('TOKEN_FILE must name the fixture server token');
const token = fs.readFileSync(process.env.TOKEN_FILE, 'utf8').trim();
const base = `http://127.0.0.1:${process.env.PORT || '8021'}`;
const names = ['hardware', 'pcie', 'power', 'constraints', 'events', 'crash', 'reliability'];
const classContentInputs = {
  suppressions: ['hardware', 'power', 'constraints'], gaps: ['pcie', 'constraints'], pressure: ['events'],
  transitions: ['power', 'crash', 'reliability'], mismatches: ['hardware', 'pcie'],
};
const inputs = names.map(name => ({ name, params: {}, outcome: name === 'events' ? 'failed' : name === 'crash' ? 'timeout' : 'ok',
  asked_at: '2026-09-24T12:00:00Z', count: name === 'events' || name === 'crash' ? null : 0, took_ms: 3,
  warnings: name === 'power' ? ['Synthetic transition source did not answer.'] : name === 'crash' ? ['Synthetic warning before timeout.'] : [],
  warnings_total: name === 'power' || name === 'crash' ? 1 : 0 }));
const gap = { id: 'gap:inputs', class: 'gaps', title: 'Part of the evidence was not observed',
  summary: 'These readings did not answer.', evidence: { not_observed: { events: 'failed', crash: 'timeout' } }, readings: names };
const envelope = {
  reading: 'signals', params: {}, outcome: 'ok', count: 1, error: null, warnings: ['events was not observed', 'crash was not observed'],
  redacted: [], asked_at: '2026-09-24T12:00:01Z', took_ms: 10,
  method: { kind: 'readings', readings: inputs, class_content_inputs: classContentInputs },
  sections: [{ name: 'signals', class: 'inferred', data: [gap], basis: 'Synthetic inputs; only the global missing-input lead fired.' }],
};
function assert(ok, message) { if (!ok) throw Error(message); }
(async () => {
  const browser = await chromium.launch();
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    page.setDefaultTimeout(10000);
    let legacy = false;
    let asks = 0;
    await page.route('**/api/readings/signals', async route => {
      asks++;
      const answer = structuredClone(envelope);
      if (legacy) delete answer.method.class_content_inputs;
      await route.fulfill({ json: answer });
    });
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#token') || document.querySelector('nav[aria-label="Views"]'));
    if (await page.locator('#token').count()) {
      await page.locator('#token').fill(token);
      await page.locator('button[type=submit]').click();
    }
    const nav = page.locator('nav[aria-label="Views"]:visible').first();
    await nav.waitFor();
    const signalNav = nav.locator('button', { hasText: 'Signals' }).first();
    if (!(await signalNav.isVisible())) await nav.locator('summary').click();
    await signalNav.click();
    const map = page.locator('section[aria-labelledby="signal-map-title"]');
    await map.getByText('Could not assess · events failed').waitFor();
    assert(await map.getByText('1 lead to inspect.', { exact: false }).count() === 1, 'The one-lead heading used the wrong number');
    assert(await map.getByText('No lead from answered inputs · crash timeout · power warned').count() === 1,
      'A partially observed class lost the missing and warned input context');
    assert(await map.getByText(/crash warned/).count() === 0, 'A timed-out input was labelled as an answered warning');
    assert(await map.getByText('No lead · power warned').count() === 1,
      'A class that answered with a warning was shown as fully quiet');
    const pressure = map.locator('div').filter({ hasText: '03 / pressure' }).last();
    assert(await pressure.getByText('—', { exact: true }).count() === 1, 'Unobserved pressure was shown as zero');
    assert(await page.getByText(/^No signal in/).count() === 0, 'The broad false-clean line returned');
    if (process.env.SCREENSHOT_DIR) await map.screenshot({ path: `${process.env.SCREENSHOT_DIR}/signals-silence-${width}.png` });

    legacy = true;
    await page.getByRole('button', { name: 'Take again' }).click();
    await map.getByText('No lead returned · class input reach unrecorded').first().waitFor();
    assert(asks === 2, `Signals was unexpectedly queried ${asks} times`);
    assert(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth) === 0,
      `Signals overflowed at ${width}px`);
    console.log(`signals silence ${width}px: passed; two explicit synthetic takes`);
    await page.close();
  }
  await browser.close();
})().catch(error => { console.error(error); process.exitCode = 1; });
