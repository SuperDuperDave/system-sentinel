// Run against fixture-server.py with TOKEN_FILE, PORT and NODE_PATH set as in docs/screens/README.md.
// Intercepts only these synthetic reading envelopes, so the real dashboard and session boundary run.
const { chromium } = require('playwright');
const fs = require('fs');
const base = `http://127.0.0.1:${process.env.PORT || '8021'}`;
if (!process.env.TOKEN_FILE) throw Error('TOKEN_FILE must name the fixture server token');
const token = fs.readFileSync(process.env.TOKEN_FILE, 'utf8').trim();
const at = '2026-09-24T12:00:00.1234567Z';
const row = { RecordId: 420, Id: 4101, Log: 'System', ProviderName: 'Display', Level: 3, LevelDisplayName: 'Warning', MachineName: '<host>', TaskDisplayName: null, TimeCreated: at, Message: 'Synthetic display reset' };
const heldBefore = { RecordId: row.RecordId, TimeCreated: at, ProviderName: row.ProviderName, Id: row.Id, LevelDisplayName: row.LevelDisplayName, Message: row.Message };
const rawStopRows = [
  { ...row, RecordId: 421, Id: 12, ProviderName: 'Microsoft-Windows-Kernel-General', Message: 'Synthetic start' },
  { ...row, RecordId: 422, Id: 41, ProviderName: 'Microsoft-Windows-Kernel-Power', Message: 'Synthetic restart announcement' },
  { ...row, RecordId: 423, Id: 6008, ProviderName: 'EventLog', Message: 'Synthetic previous shutdown estimate' },
  { ...row, RecordId: 423, Id: 6008, ProviderName: 'EventLog', Message: 'Synthetic ambiguous row' },
  { ...row, RecordId: 424, Id: 1001, ProviderName: 'Microsoft-Windows-WER-SystemErrorReporting', Message: 'Synthetic bug check report' },
  { ...row, RecordId: 425, Id: 1001, Log: 'Application', ProviderName: 'Windows Error Reporting', Message: 'Synthetic application report' },
];
function envelope(name, sections, outcome = 'ok') { return { reading: name, params: {}, asked_at: '2026-09-24T12:03:00Z', took_ms: 2, outcome, method: { kind: 'synthetic' }, count: sections.length ? 1 : 0, sections, error: null, warnings: [], redacted: ['MachineName'] }; }
const section = (name, data, cls = 'raw') => ({ name, class: cls, data });
const stop = { started_at: at, announced_at: at, stopped_at: at, reported_at: null, down_seconds: 18, bugcheck: null, no_bugcheck_recorded: true, last_record_before: heldBefore, quiet_seconds: 10, records: { start: 421, power_41: 422, eventlog_6008: 423, wer_1001: 424, report: [425, 426] }, power: null, dump: null, dump_inventory_complete: true };
const crash = envelope('crash', [section('records', rawStopRows), section('stops', [stop], 'derived')]);
const signal = { id: 'transition:display-reset', class: 'transitions', title: 'Display restarted', summary: 'A display reset was returned.', readings: ['power'], evidence: { sample: { returned: 1 }, refs: [{ role: 'display_reset', reading: 'event_record', params: { log: 'System', record_id: 420, time_created: at } }], refs_total: 1, refs_missing: 0, refs_omitted: 0 } };
const groupedSignal = { id: 'transition:unexpected-shutdown', class: 'transitions', title: 'One stop returned', summary: 'A stop was composed from returned rows.', readings: ['crash'], evidence: { stops: [{ anchor_at: at, refs: [
  { role: 'start', reading: 'event_record', params: { log: 'System', record_id: 421, time_created: at } },
  { role: 'power_41', reading: 'event_record', params: { log: 'System', record_id: 0, time_created: at } },
], refs_total: 2, refs_missing: 0, refs_omitted: 0 }] } };
const signals = { ...envelope('signals', [section('signals', [signal, groupedSignal], 'inferred')]), count: 2 };
const exact = (status, id, time) => status === 'failed' || status === 'denied'
  ? { ...envelope('event_record', [], status), error: { kind: status, detail: 'Synthetic lookup failure' } }
  : envelope('event_record', [section('records', status === 'same' ? [{ ...row, RecordId: Number(id), TimeCreated: time }] : []), section('reference', { status, retention: status === 'not_returned' ? 'before_retained' : 'within_retained', found_time_created: status === 'id_reused' ? '2026-09-24T12:01:00Z' : undefined }, 'derived')], status === 'same' ? 'ok' : 'empty');
(async () => {
  const browser = await chromium.launch({ headless: true });
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    const lookups = [];
    let status = 'same';
    let signalTime = at;
    await page.route('**/api/readings/**', async route => {
      const url = new URL(route.request().url());
      const name = url.pathname.split('/').pop();
      if (name === 'event_record') {
        lookups.push(url.search);
        if (status === 'lost') await route.fulfill({ status: 503, json: { detail: 'Synthetic transport failure' } });
        else await route.fulfill({ json: exact(status, url.searchParams.get('record_id'), url.searchParams.get('time_created')) });
        return;
      }
      if (name === 'crash') { await route.fulfill({ json: crash }); return; }
      if (name === 'signals') {
        const next = structuredClone(signals);
        next.sections[0].data[0].evidence.refs[0].params.time_created = signalTime;
        await route.fulfill({ json: next });
        return;
      }
      await route.continue();
    });
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#token') || document.querySelector('nav[aria-label="Views"]'), null, { timeout: 15000 });
    if (await page.locator('#token').count()) {
      await page.locator('#token').fill(token);
      await page.locator('button[type=submit]').click();
    }
    await page.locator('nav[aria-label="Views"]:visible').first().waitFor();
    const nav = page.locator('nav[aria-label="Views"]:visible').first();
    async function choose(name) {
      const button = nav.locator('button', { hasText: name }).first();
      if (!(await button.isVisible())) await nav.locator('summary').click();
      await button.click();
    }
    await choose('Crashes');
    const stopButton = page.locator('button[aria-controls="stop-detail-0"]');
    await stopButton.waitFor();
    await stopButton.scrollIntoViewIfNeeded();
    const topBefore = await stopButton.evaluate(el => el.getBoundingClientRect().top);
    await stopButton.click();
    const topAfter = await stopButton.evaluate(el => el.getBoundingClientRect().top);
    if (Math.abs(topAfter - topBefore) > 3) throw Error(`stop moved ${topBefore} -> ${topAfter}`);
    if (lookups.length) throw Error('crash inspection queried exact row before request');
    await page.getByText('Raw record held in this crash reading').first().click();
    await page.getByText(/These raw fields were returned with the crash reading/).first().waitFor();
    await page.getByText('Derived last-record projection').waitFor();
    await page.getByText(/more than one matching raw row was returned/).waitFor();
    await page.getByText(/no matching raw row was returned/).waitFor();
    await page.getByText(/Rebooted from a bug check/).waitFor();
    await page.getByText(/Application log error report/).waitFor();
    const crashOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (crashOverflow) throw Error(`crash horizontal overflow: ${crashOverflow}`);
    await page.getByRole('button', { name: 'Check this exact record in the current log' }).first().click();
    await page.getByText(/Same record returned/).waitFor();
    if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/cited-crashes-${width}.png` });
    if (lookups.length !== 1) throw Error('crash explicit exact request count');
    await choose('Signals');
    const lead = page.getByRole('button', { name: /Display restarted/ }).first();
    await lead.waitFor();
    await lead.scrollIntoViewIfNeeded();
    const leadBefore = await lead.evaluate(el => el.getBoundingClientRect().top);
    await lead.click();
    const leadAfter = await lead.evaluate(el => el.getBoundingClientRect().top);
    if (Math.abs(leadAfter - leadBefore) > 3) throw Error(`signal moved ${leadBefore} -> ${leadAfter}`);
    if (lookups.length !== 1) throw Error('signal inspection queried exact row before request');
    await page.getByRole('button', { name: 'Check this exact record in the current log' }).click();
    await page.getByText(/Same record returned/).waitFor();
    status = 'id_reused';
    await page.getByRole('button', { name: 'Check current log again' }).click();
    await page.getByText(/This ID now names a different event/).waitFor();
    await page.getByRole('button', { name: 'Stack this exact check' }).waitFor();
    status = 'not_returned';
    await page.getByRole('button', { name: 'Check current log again' }).click();
    await page.getByText(/The cited row was not returned/).waitFor();
    for (const [next, expected] of [
      ['failed', /The exact lookup failed/],
      ['denied', /The exact lookup was denied by Windows/],
      ['lost', /The exact lookup did not complete/],
    ]) {
      status = next;
      await page.getByRole('button', { name: 'Check current log again' }).click();
      await page.getByText(expected).waitFor();
    }
    signalTime = '2026-09-24T12:01:00.1234567Z';
    await page.getByRole('button', { name: 'Take again' }).first().click();
    await page.getByText(new RegExp(`System record 420 · ${signalTime.replaceAll('.', '\\.')}`)).waitFor();
    await page.getByRole('button', { name: 'Check this exact record in the current log' }).waitFor();
    if (lookups.length !== 7) throw Error('changed citation triggered an unrequested exact lookup');
    const groupedLead = page.getByRole('button', { name: /One stop returned/ }).first();
    await groupedLead.click();
    await page.getByText(/Stop 1 · 2026-09-24/).waitFor();
    await page.getByText(/1 returned reference is incomplete/).waitFor();
    if (lookups.length !== 7) throw Error('opening grouped citations triggered an exact lookup');
    if (process.env.SCREENSHOT_DIR) await page.screenshot({ path: `${process.env.SCREENSHOT_DIR}/cited-signals-${width}.png` });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (overflow) throw Error(`horizontal overflow: ${overflow}`);
    if (lookups.length !== 7) throw Error(`unexpected exact request count: ${lookups.length}`);
    console.log(JSON.stringify({ width, stopPositionDelta: topAfter - topBefore, leadPositionDelta: leadAfter - leadBefore, lookups: lookups.length, crashOverflow, overflow }));
    await page.close();
  }
  await browser.close();
})().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
