// 사용법: node page_smoke.js <base_url> <token>  (test_page_smoke.py가 호출)
const { chromium } = require("playwright");
(async () => {
  const [base, token] = process.argv.slice(2);
  const browser = await chromium.launch({ args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });
  await page.goto(base + "/#token=" + token);
  await page.click("#startBtn");
  await page.waitForFunction(() => document.getElementById("conn").textContent === "연결됨", null, { timeout: 8000 });
  await page.click('nav button[data-tab="alarm"]');
  await page.waitForFunction(() => document.getElementById("alarmInfo").textContent.includes("알람"), null, { timeout: 8000 });
  await page.click('nav button[data-tab="dbg"]');
  await page.fill("#typed", "아 왜 안되지");
  await page.click("#typedSend");
  await page.waitForFunction(() => /볼게요|가짜 요약/.test(document.getElementById("lastSay").textContent), null, { timeout: 8000 });
  const said = await page.textContent("#lastSay");
  // 알람: 테스트 울림 -> 울림 패널 + (VLM 없음) 수동 모드 카메라 카운트다운
  await page.click('nav button[data-tab="alarm"]');
  await page.click("#alarmTest");
  await page.waitForSelector("#ringing", { state: "visible", timeout: 8000 });
  await page.waitForFunction(() => document.getElementById("manualInfo").textContent.includes("초 남음"), null, { timeout: 10000 });
  await page.click("summary");
  await page.fill("#emCode", "4242");
  await page.click("#emBtn");
  await page.waitForSelector("#ringing", { state: "hidden", timeout: 8000 });
  // 집사: 버튼
  await page.click('nav button[data-tab="butler"]');
  await page.click("#homeBtn");
  await page.waitForFunction(() => document.getElementById("lastSay").textContent.includes("어서"), null, { timeout: 30000 });
  // 출발: 버튼 -> 체크리스트 + 말하기
  await page.click('nav button[data-tab="depart"]');
  await page.click("#leaveBtn");
  await page.waitForFunction(() => document.getElementById("lastSay").textContent.includes("다녀오세요"), null, { timeout: 15000 });
  const checklist = await page.$$eval("#checklist li", (els) => els.map((e) => e.textContent));
  // 관중석: 테스트 버튼 -> 이모지 연출 + 대사
  await page.click('nav button[data-tab="stadium"]');
  await page.click('#stadium button[data-ev="pr_merged"]');
  await page.waitForSelector("#fx .big", { timeout: 8000 });
  await page.waitForFunction(() => document.getElementById("lastSay").textContent.includes("머지"), null, { timeout: 15000 });
  // 결재: 파이썬 쪽이 이 신호를 보고 진짜 훅을 실행한다 -> 카드가 뜨면 승인
  console.log("READY_FOR_APPROVAL");
  await page.waitForSelector("#approval.on", { timeout: 20000 });
  const apprLabel = await page.textContent("#apprLabel");
  const apprCmd = await page.textContent("#apprCmd");
  await page.click("#approveBtn");
  await page.waitForSelector("#approval", { state: "hidden", timeout: 8000 });
  const logText = await page.textContent("#log");
  await browser.close();
  console.log(JSON.stringify({ errors, said, checklist, apprLabel, apprCmd, log: logText }));
})().catch((e) => { console.log(JSON.stringify({ errors: ["fatal: " + e.message] })); process.exit(0); });
