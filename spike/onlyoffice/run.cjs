const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1280, height: 800 } });
  p.on('console', m => console.log('console:', m.type(), m.text().slice(0, 200)));
  await p.goto('http://localhost:8090/index.html');
  const t0 = Date.now();
  await p.waitForFunction(() => window.events.some(e => e === 'documentReady' || e.startsWith('error')), null, { timeout: 180000 }).catch(() => console.log('timeout'));
  console.log('events', JSON.stringify(await p.evaluate(() => window.events)), 'ms', Date.now() - t0);
  await p.waitForTimeout(3000);
  await p.screenshot({ path: 'oo.png' });
  await b.close();
})();
