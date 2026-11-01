// Phone-width contact sheet of the sample report using Playwright (Chromium must be installed).
// usage: node examples/screenshot.js examples/sample.html examples/sample.png [cardIdx,...]
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

(async () => {
  const [html, out] = [path.resolve(process.argv[2]), path.resolve(process.argv[3])];
  const W = 390, H = 780;
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, reducedMotion: 'reduce' });
  const page = await ctx.newPage();
  await page.goto('file://' + html);
  const ids = await page.$$eval('section.card', (els) => els.map((e) => e.id));
  const shots = [];
  for (const id of ids) {
    await page.evaluate((i) => document.getElementById(i).scrollIntoView(), id);
    await page.waitForTimeout(120);
    shots.push((await page.screenshot()).toString('base64'));
  }
  // compose a grid page from the individual screenshots
  const cols = 5;
  const sheet = await ctx.newPage();
  const imgs = shots.map((b) => `<img src="data:image/png;base64,${b}" width="${W}" height="${H}">`).join('');
  await sheet.setViewportSize({ width: cols * (W + 12) + 12, height: 100 });
  await sheet.setContent(`<body style="margin:0;padding:12px;background:#888;display:grid;grid-template-columns:repeat(${cols},${W}px);gap:12px">${imgs}</body>`);
  await sheet.screenshot({ path: out, fullPage: true });
  if (process.env.SPLIT) {
    const dir = process.env.SPLIT; fs.mkdirSync(dir, { recursive: true });
    shots.forEach((b, i) => fs.writeFileSync(path.join(dir, `card${String(i).padStart(2, '0')}_${ids[i]}.png`), Buffer.from(b, 'base64')));
  }
  await browser.close();
  console.log('wrote', out, ids.length, 'cards');
})();
