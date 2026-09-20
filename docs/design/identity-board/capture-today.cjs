const { chromium } = require('@playwright/test');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  await p.goto('http://localhost:4322/'); await p.waitForTimeout(1500);
  const sec = await p.$('#system-sentinel'); await sec.screenshot({ path: 'today-home.png' });
  await p.goto('http://localhost:4322/work/system-sentinel/'); await p.waitForTimeout(1500);
  await p.screenshot({ path: 'today-page.png' });
  await p.screenshot({ path: 'today-page-full.png', fullPage: true });
  const m = await b.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  await m.goto('http://localhost:4322/work/system-sentinel/'); await m.waitForTimeout(1500);
  await m.screenshot({ path: 'today-phone.png' });
  await b.close(); console.log('captured');
})();
