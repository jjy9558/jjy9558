// 사용법: node page_smoke.js <base_url> <token>   (test_page_smoke.py 가 호출)
// Chromium 가짜 마이크(--use-fake-device-for-media-stream: 주기적 삐 소리)로 폰 페이지를 실제로 돌려
// 청크가 서버에 도착하는지 본다.
const { chromium } = require("playwright");
(async () => {
  const [base, token] = process.argv.slice(2);
  const browser = await chromium.launch({
    args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"],
  });
  const ctx = await browser.newContext({ permissions: ["microphone"] });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });
  await page.goto(base + "/#token=" + token);
  const hashAfter = await page.evaluate(() => location.hash);
  await page.click("#startBtn");
  await page.waitForFunction(() => window.__sw && window.__sw.sent >= 3, null, { timeout: 20000 });
  const st = await page.evaluate(() => ({ sent: window.__sw.sent, failed: window.__sw.failed, rmsDb: window.__sw.rmsDb,
    serverDb: window.__sw.serverDb, secure: window.isSecureContext, worklet: !!(window.AudioWorkletNode) }));
  const stateText = await page.textContent("#state");
  await page.click("#startBtn");
  await browser.close();
  console.log(JSON.stringify({ ok: errors.length === 0, errors, hashAfter, stateText, ...st }));
})().catch((e) => { console.log(JSON.stringify({ ok: false, errors: [String(e)] })); process.exit(1); });
