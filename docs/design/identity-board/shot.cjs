const { chromium } = require('@playwright/test');
(async () => {
  const b = await chromium.launch(); const file = 'file://' + __dirname + '/system-sentinel-directions.html';
  for (const [name, w] of [['desk', 1280], ['phone', 390]]) {
    const p = await b.newPage({ viewport: { width: w, height: 900 }, deviceScaleFactor: 1 });
    await p.goto(file); await p.waitForTimeout(3000);
    const h = await p.evaluate(() => document.documentElement.scrollHeight); const sw = await p.evaluate(() => document.documentElement.scrollWidth);
    console.log(name, 'height', h, 'scrollWidth', sw);
    // slices of 1400px so each is readable
    for (let y = 0, i = 0; y < h; y += 1400, i++) {
      await p.screenshot({ path: `board-${name}-${i}.png`, clip: { x: 0, y, width: w, height: Math.min(1400, h - y) }, fullPage: true });
    }
    await p.close();
  }
  await b.close();
})();
