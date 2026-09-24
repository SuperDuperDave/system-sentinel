// Run against fixture-server.py after building dashboard, with TOKEN_FILE, PORT and NODE_PATH.
// Only the intercepts below are synthetic; navigation, session and rendering use the real app.
const { chromium } = require('playwright');
const fs = require('fs');
if (!process.env.TOKEN_FILE) throw Error('TOKEN_FILE must name the fixture server token');
const token = fs.readFileSync(process.env.TOKEN_FILE, 'utf8').trim();
const base = `http://127.0.0.1:${process.env.PORT || '8021'}`;
const at = '2026-09-24T12:00:00.1234567Z';
const section = (name, data, cls = 'raw') => ({ name, class: cls, data });
function envelope(name, sections, outcome = 'ok') {
  return { reading: name, params: {}, asked_at: at, took_ms: 5, outcome, method: { kind: 'synthetic' },
    count: outcome === 'ok' ? 5 : 0, sections, error: outcome === 'failed' ? { kind: 'synthetic', detail: 'Synthetic failed retake' } : null,
    warnings: [], redacted: [] };
}
const stops = Array.from({ length: 5 }, (_, i) => ({ started_at: at, announced_at: at, stopped_at: at,
  reported_at: null, down_seconds: 10 + i, bugcheck: { code: `0x${i + 1}`, name: `Synthetic stop ${i + 1}`,
    parameters: [], source: 'synthetic', bucket: null }, no_bugcheck_recorded: false, last_record_before: null,
  quiet_seconds: null, records: { start: 420 + i, power_41: 430 + i, eventlog_6008: 440 + i,
    wer_1001: null, report: [] }, power: null, dump: null, dump_inventory_complete: true }));
stops[0].stopped_at = null;
stops[3].stopped_at = '2026-09-23T11:00:00.1234567Z';
stops[4].dump = { name: 'synthetic-five.dmp', path: 'synthetic-dump-five', bytes: 4096,
  modified: at, matched_by: 'synthetic' };
stops[3].dump = { name: 'synthetic-four.dmp', path: 'synthetic-dump-four', bytes: 4096,
  modified: at, matched_by: 'synthetic' };
const crash = envelope('crash', [section('records', []), section('stops', stops, 'derived')]);
const leads = Array.from({ length: 5 }, (_, i) => ({ id: `lead-${i}`, class: 'transitions',
  title: `Synthetic lead ${i + 1}`, summary: 'Synthetic returned evidence.',
  evidence: i === 4 ? { returned: i + 1, last: at } : { returned: i + 1 }, readings: ['power'] }));
const signals = envelope('signals', [section('signals', leads, 'inferred')]);
const faultRecord = { RecordId: 701, Log: 'Application', Id: 1000, ProviderName: 'Application Error',
  TimeCreated: at, Level: 2, LevelDisplayName: 'Error', MachineName: '<host>', TaskDisplayName: null,
  Message: 'Synthetic application fault' };
const fault = { RecordId: 701, Log: 'Application', kind: 'application crash',
  fields: { AppName: 'synthetic.exe', ModuleName: 'synthetic.dll' }, exception: null, process: null };
const faults = envelope('faults', [section('records', [faultRecord]), section('decoded', [fault], 'derived'),
  section('summary', { by_kind: { 'application crash': 1 }, applications: [], live_kernel: [] }, 'derived')]);
const changeRaw = { Log: 'System', RecordId: 810, Id: 19, ProviderName: 'Microsoft-Windows-WindowsUpdateClient',
  TimeCreated: '2026-09-23T12:00:00.000Z', Data: { updateTitle: 'Synthetic update KB1234567' } };
const changes = envelope('changes', [section('records', [changeRaw]),
  section('changes', [{ at: changeRaw.TimeCreated, source: 'windows_update', ref: { log: 'System', record_id: 810 },
    kind: 'update_installed', subject: 'Synthetic update KB1234567', version: null, publisher: null, outside_window: false }], 'derived'),
  section('collection', { windows_update: { outcome: 'ok', returned: 1, limit: 100, truncated: false },
    device_configuration: { outcome: 'denied', returned: 0, limit: 100, truncated: null },
    msi: { outcome: 'empty', returned: 0, limit: 100, truncated: false } }),
  section('coverage', { windows_update: { complete: true }, device_configuration: { complete: null }, msi: { complete: true } }, 'derived')]);
changes.count = 1;
changes.warnings = ['device_configuration did not answer: synthetic access denied'];
const dumps = envelope('dumps', [section('files', [])], 'empty');
const reliability = envelope('reliability', [section('days', { from: '2026-09-23', to: '2026-09-24', days: [
  { day: '2026-09-23', index_last: 8, index_min: 8, records: { information: 1 }, event_types: [] },
  { day: '2026-09-24', index_last: 7, index_min: 7, records: { information: 2 }, event_types: [] },
] }, 'derived'), section('records', [])]);
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));
function assert(ok, message) { if (!ok) throw Error(message); }
(async () => {
  const browser = await chromium.launch();
  for (const width of [1440, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    const requests = { crash: 0, faults: 0, dumps: 0, reliability: 0, signals: 0, dump_header: 0, changes: 0 };
    let crashMode = 'ok';
    await page.route('**/api/readings/**', async route => {
      const name = new URL(route.request().url()).pathname.split('/').pop();
      if (name in requests) requests[name]++;
      if (name === 'crash') {
        if (crashMode === 'slow-failed') { await delay(700); await route.fulfill({ json: envelope('crash', [], 'failed') }); }
        else if (crashMode === 'lost') await route.fulfill({ status: 503, json: { detail: 'Synthetic transport loss' } });
        else if (crashMode === 'closed') await route.fulfill({ status: 401, json: { detail: 'Synthetic session closed' } });
        else await route.fulfill({ json: crash });
        return;
      }
      if (name === 'changes') {
        const params = new URL(route.request().url()).searchParams;
        assert([at, stops[3].stopped_at].includes(params.get('before')) && params.get('hours') === '168' && params.get('count') === '100',
          'Changes did not use this stop’s estimated boundary and requested scope');
        await route.fulfill({ json: changes });
        return;
      }
      const answer = { faults, dumps, reliability, signals }[name];
      if (name === 'dump_header') {
        const format = new URL(route.request().url()).searchParams.get('path') === 'synthetic-dump-four'
          ? 'synthetic-four' : 'synthetic-five';
        await route.fulfill({ json: envelope('dump_header', [section('inspection', { format,
          header_status: 'ok', limit: 'synthetic fixture' }, 'derived')]) });
        return;
      }
      if (answer) { await route.fulfill({ json: answer }); return; }
      await route.continue();
    });
    await page.goto(base);
    await page.waitForFunction(() => document.querySelector('#token') || document.querySelector('nav[aria-label="Views"]'));
    if (await page.locator('#token').count()) {
      await page.locator('#token').fill(token);
      await page.locator('button[type=submit]').click();
    }
    const nav = page.locator('nav[aria-label="Views"]:visible').first();
    await nav.waitFor();
    async function choose(name) {
      await nav.waitFor();
      const button = nav.locator('button', { hasText: name }).first();
      if (!(await button.isVisible())) await nav.locator('summary').click();
      await button.click();
    }
    await choose('Crashes');
    const stop = page.locator('button[aria-controls="stop-detail-4"]');
    await stop.waitFor();
    if (await page.locator('#reliability-day').isVisible()) await page.locator('#reliability-day').selectOption('2026-09-23');
    else await page.getByRole('button', { name: /September 23, 2026 UTC/ }).click();
    await stop.scrollIntoViewIfNeeded();
    await stop.click();
    assert(requests.changes === 0, 'opening a stop eagerly took its change history');
    const changeButton = page.getByRole('button', { name: /What changed before Windows/ });
    await changeButton.scrollIntoViewIfNeeded();
    const changeTop = await changeButton.evaluate(el => el.getBoundingClientRect().top);
    await changeButton.click();
    await page.getByText('Synthetic update KB1234567', { exact: true }).waitFor();
    assert(requests.changes === 1, 'opening change history did not take exactly one reading');
    assert(Math.abs(await changeButton.evaluate(el => el.getBoundingClientRect().top) - changeTop) < 4,
      'opening change history moved its clicked control');
    assert(await page.getByText('not read · denied').count() === 1, 'a refused change source looked complete');
    assert(await page.getByText('window reach unknown').count() === 1, 'a refused change source lost its reach');
    await page.getByText('Source row · System record 810').click();
    const rawChange = page.locator('section[aria-label="Changes before the estimated stop"] ol pre').first();
    await rawChange.waitFor();
    assert((await rawChange.textContent()).includes('Microsoft-Windows-WindowsUpdateClient'), 'the returned raw row is not inspectable');
    assert(await page.getByText(/nearby change is a lead to inspect, not proof of a cause/).count() === 1,
      'the nearby-change panel claimed a cause');
    if (process.env.SCREENSHOT_DIR) await page.locator('section[aria-label="Changes before the estimated stop"]').screenshot({
      path: `${process.env.SCREENSHOT_DIR}/changes-near-stop-${width}.png`,
    });
    await page.getByText('synthetic-five', { exact: true }).waitFor();
    const moment = page.locator('#stop-detail-4 button', { hasText: 'The record before this' }).first();
    await moment.scrollIntoViewIfNeeded();
    const scrollBefore = await page.evaluate(() => scrollY);
    await moment.click();
    await page.getByRole('heading', { name: /The record around/ }).waitFor();
    assert(await page.getByRole('button', { name: 'Back to Crashes' }).count() === 1, 'Record lost its origin');
    assert(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth) === 0, 'Record frame overflows');
    const beforeReturn = { ...requests };
    await page.goBack();
    await stop.waitFor();
    const afterBack = await moment.evaluate(el => ({ top: el.getBoundingClientRect().top,
      focused: document.activeElement === el, scroll: scrollY }));
    assert(await stop.getAttribute('aria-expanded') === 'true' && afterBack.focused, 'Back lost the open stop or source-link focus');
    assert(afterBack.top >= -2 && afterBack.top < 900, `Back left the source link offscreen: ${JSON.stringify({ afterBack, scrollBefore })}`);
    assert(Math.abs(afterBack.scroll - scrollBefore) < 4, 'Back lost the exact investigation position');
    assert(await page.locator('#reliability-day').inputValue() === '2026-09-23', 'Back lost selected reliability day');
    assert(requests.crash === beforeReturn.crash && requests.faults === beforeReturn.faults &&
      requests.dumps === beforeReturn.dumps && requests.reliability === beforeReturn.reliability &&
      requests.dump_header === beforeReturn.dump_header && requests.changes === beforeReturn.changes,
      'Back re-asked held Crashes readings');
    assert(await changeButton.getAttribute('aria-expanded') === 'true', 'Back closed the open change history');
    assert(await page.getByText(/held reading/).count() >= 1, 'held evidence is not labelled');
    await delay(900);
    assert(Math.abs(await moment.evaluate(el => el.getBoundingClientRect().top) - afterBack.top) < 4, 'late jump after Back');
    const explicitScroll = await page.evaluate(() => scrollY);
    await moment.click();
    await page.getByRole('button', { name: 'Back to Crashes' }).click();
    await stop.waitFor();
    assert(await stop.getAttribute('aria-expanded') === 'true', 'explicit Back lost the open stop');
    assert(requests.crash === beforeReturn.crash, 'explicit Back re-asked the crash reading');
    assert(await moment.evaluate(el => document.activeElement === el), 'explicit Back lost source-link focus');
    assert(Math.abs(await page.evaluate(() => scrollY) - explicitScroll) < 4, 'explicit Back lost source-link position');
    await page.goForward();
    await page.getByRole('heading', { name: /The record around/ }).waitFor();
    await page.goBack();
    await stop.waitFor();

    if (width === 1440) {
      await stop.evaluate(el => scrollTo(0, scrollY + el.getBoundingClientRect().top - 40));
      const nearTop = await stop.evaluate(el => el.getBoundingClientRect().top);
      // Activate the desktop navigation without Playwright scrolling it into view first.
      await nav.locator('button', { hasText: 'Signals' }).first().evaluate(el => el.click());
      await page.getByRole('heading', { name: 'Signals', exact: true }).waitFor();
      await nav.locator('button', { hasText: 'Crashes' }).first().evaluate(el => el.click());
      await stop.waitFor();
      const returnedTop = await stop.evaluate(el => el.getBoundingClientRect().top);
      assert(Math.abs(returnedTop - nearTop) < 4,
        `nav return moved a stop that was near the top: ${nearTop} -> ${returnedTop}`);
    }

    const faultButton = page.locator('button[data-row-id="Application:701"]');
    await faultButton.click();
    assert(await stop.getAttribute('aria-expanded') === 'true', 'opening a fault closed the stop');
    const faultMoment = faultButton.locator('xpath=following-sibling::*').locator('button[data-moment-source]').first();
    await faultMoment.scrollIntoViewIfNeeded();
    const faultScroll = await page.evaluate(() => scrollY);
    await faultMoment.click();
    await page.getByRole('heading', { name: /The record around/ }).waitFor();
    await page.goBack();
    assert(await faultMoment.evaluate(el => document.activeElement === el), 'Back focused the earlier stop instead of the fault source link');
    assert(Math.abs(await page.evaluate(() => scrollY) - faultScroll) < 4, 'fault source link lost its exact position');
    await choose('Signals');
    await choose('Crashes');
    const faultReturn = await faultButton.evaluate(el => ({ top: el.getBoundingClientRect().top,
      bottom: el.getBoundingClientRect().bottom, focused: document.activeElement === el }));
    assert(faultReturn.focused && faultReturn.top >= -2 && faultReturn.bottom <= 902,
      `nav return focused an offscreen fault: ${JSON.stringify(faultReturn)}`);
    assert(await stop.getAttribute('aria-expanded') === 'true', 'fault return closed the earlier stop');

    await choose('Signals');
    const lead = page.getByRole('button', { name: /Synthetic lead 5/ }).first();
    await lead.waitFor();
    await lead.scrollIntoViewIfNeeded();
    await lead.click();
    await choose('Crashes');
    assert(await stop.getAttribute('aria-expanded') === 'true', 'nav return lost the open stop');
    assert(await faultButton.evaluate(el => document.activeElement === el && el.getBoundingClientRect().top >= -2), 'nav return lost the last focused fault');
    await choose('Signals');
    const signalReturn = await lead.evaluate(el => ({ top: el.getBoundingClientRect().top,
      expanded: el.getAttribute('aria-expanded'), focused: document.activeElement === el }));
    assert(signalReturn.expanded === 'true' && signalReturn.focused, 'Signals lost open lead or focus');
    assert(signalReturn.top >= 0 && signalReturn.top < 900, 'Signals open lead is offscreen');
    assert(requests.signals === 1, 'Signals return re-asked the machine');
    const signalMoment = lead.locator('xpath=following-sibling::*').locator('button[data-moment-source]').first();
    await signalMoment.scrollIntoViewIfNeeded();
    const signalScroll = await page.evaluate(() => scrollY);
    await signalMoment.click();
    await page.getByRole('heading', { name: /The record around/ }).waitFor();
    await page.goBack();
    assert(await signalMoment.evaluate(el => document.activeElement === el), 'Signals Back lost the source link');
    assert(Math.abs(await page.evaluate(() => scrollY) - signalScroll) < 4, 'Signals Back lost its exact position');
    assert(requests.signals === 1, 'Signals source-link return re-asked its held reading');

    await choose('Crashes');
    await faultButton.click();
    await stop.click();
    assert(await stop.getAttribute('aria-expanded') === 'false', 'test could not close its selected stop');
    await choose('Record');
    await page.getByRole('heading', { name: 'Record', exact: true }).waitFor();
    await page.goBack();
    await page.getByRole('heading', { name: 'Crashes', exact: true }).waitFor();
    assert(await page.getByRole('heading', { name: 'Crashes', exact: true }).evaluate(el => document.activeElement === el),
      'keyboard Back with no selected row left focus on the page body');
    await stop.click();
    await page.getByText('synthetic-five', { exact: true }).waitFor();
    const otherStop = page.locator('button[aria-controls="stop-detail-3"]');
    await otherStop.click();
    await page.getByText('synthetic-four', { exact: true }).waitFor();
    assert(await page.getByText('synthetic-five', { exact: true }).count() === 0,
      'another dump file inherited the previous file header');
    assert(requests.dump_header === 2, 'opening another dump file did not ask for its header');
    await page.getByRole('button', { name: /What changed before Windows/ }).click();
    assert(requests.changes === 2, 'a different stop boundary inherited the first change history');
    await stop.click();
    await page.getByText('synthetic-five', { exact: true }).waitFor();
    assert(requests.dump_header === 2, 'returning to the exact dump file re-asked its held header');
    const noEstimate = page.locator('button[aria-controls="stop-detail-0"]');
    await noEstimate.click();
    assert(await page.getByText(/change window cannot be placed before this stop/).count() === 1,
      'a stop without an estimated stop time offered a false before-stop window');
    assert(await page.getByRole('button', { name: /What changed before Windows/ }).count() === 0,
      'a stop without an estimated stop time offered a change request');
    await stop.click();
    crashMode = 'slow-failed';
    await page.getByRole('button', { name: 'last 20' }).click();
    assert(await stop.count() === 1, 'changing count collapsed held stops');
    await page.getByText(/Showing last 5 while asking for last 20/).waitFor();
    await page.getByText(/Latest take was not observed/).waitFor();
    assert(await stop.count() === 1, 'failed retake erased held stops');
    await choose('Signals');
    await choose('Crashes');
    const returnTitle = page.getByRole('heading', { name: 'Crashes', exact: true });
    assert(await returnTitle.evaluate(el => document.activeElement === el), 'unanswered new scope restored focus against a partial view');
    await delay(900);
    assert(await returnTitle.evaluate(el => document.activeElement === el), 'unanswered new scope moved focus after its retake');
    assert(await stop.count() === 1, 'unanswered new scope erased the earlier observed rows');
    crashMode = 'lost';
    await page.getByRole('button', { name: 'Take again' }).first().click();
    await page.getByText(/Latest take could not reach Sentinel/).waitFor();
    assert(await stop.count() === 1, 'transport loss erased held stops');
    crashMode = 'ok';
    await page.getByRole('button', { name: 'Take again' }).first().click();
    await page.getByText(/5 stops · taken/).waitFor();
    await page.waitForFunction(() => !document.body.textContent.includes('Showing last 5 while asking for last 20'));
    crashMode = 'closed';
    await page.getByRole('button', { name: 'Take again' }).first().click();
    await page.locator('#token').waitFor();
    crashMode = 'ok';
    await page.locator('#token').fill(token);
    await page.locator('button[type=submit]').click();
    await choose('Crashes');
    await stop.waitFor();
    assert(requests.crash >= 5, 'new session reused old held crash reading');
    assert(await page.getByText(/held reading/).count() === 0, 'new session mislabelled fresh reading as held');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert(overflow === 0, `horizontal overflow ${overflow}`);
    console.log(JSON.stringify({ width, backScrollDelta: afterBack.scroll - scrollBefore, signalReturn,
      requests, overflow }));
    await page.close();
  }
  await browser.close();
})().catch(e => { console.error(e.stack || e); process.exit(1); });
