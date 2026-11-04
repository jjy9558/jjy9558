# homelab-e2e 발견 사항 (FINDINGS)

e2e 테스트를 돌리면서 찾은 **프로젝트 사이 계약 불일치·버그·문서 어긋남**입니다.
이 폴더에서는 다른 프로젝트를 고치지 않았습니다. 각 항목의 "제안 수정"은 해당 프로젝트 담당이 반영할 내용입니다.

- 버그가 진짜이고 여기 적힌 것만 테스트에서 `expectedFailure` 로 표시했습니다 (F-1, F-2, F-3).
  고쳐지면 그 테스트가 "unexpected success" 로 실패하므로, 그때 데코레이터를 지우세요.
- `unittest.skip` 은 비네트워크/비수동 경로가 없어 검증할 수 없었던 것입니다 (F-6).
- 줄 번호는 2026-11-04 기준 코드입니다.

> **상태 (2026-11-04 갱신):** F-1~F-5, F-7은 각 프로젝트에서 고쳐졌고, 해당 테스트는 이제 올바른 동작을 확인하도록 바뀌었습니다.
> F-6(decision-pet LLM 주소)은 `DECISION_PET_LLM_URL`로 설정할 수 있게 고쳐졌지만, 가짜 LLM으로 council `--llm`을 확인하는 테스트는 아직 추가하지 않아 skip으로 남아 있습니다.

| ID | 심각도 | 요약 | 테스트 |
|---|---|---|---|
| F-1 | 중 | story-factory 가 decision-pet 폴더에서 `data.json` 을 찾지만 실제 파일은 `state.json` | `test_s7_story.test_decision_pet_folder_as_documented` (xfail) |
| F-2 | 중 | cool-off `import-steam` 이 steam-buddy 에서 환불한 게임을 구매로 가져옴 | `test_s9_money.test_refunded_steam_game_not_imported_as_spending` (xfail) |
| F-3 | 하 | morning-brief 카드 `actions`(문장) ↔ phone-home 책상 action(id) 형식이 안 맞아 실행 버튼을 만들 수 없음 | `test_s2_paper.test_bill_card_action_can_be_whitelisted_on_desk` (xfail) |
| F-4 | 하 | steam-buddy `buy` 는 `--name/--price` 를 줘도 먼저 스토어 API 를 부름 (오프라인 경로 없음) | 우회: `homelabe2e/seed.py` `STEAM_OFFLINE` |
| F-5 | 하 | morning-brief(고정 +09:00)와 s21-sidekick 알람 게이트(PC 로컬 시각)의 "오늘" 기준이 다름 | 테스트 없음 (시각에 따라 달라 결정적 재현 불가) |
| F-6 | 하 | decision-pet LLM 주소를 CLI/환경변수로 줄 수 없음 → council `--llm` 을 가짜 LLM 으로 검증 불가 | `test_s8_council.test_council_llm_phrasing_with_fake_llm` (skip) |
| F-7 | 문서 | jarvis-hub·life-nagger 문서가 "sidekick 에 /api/say·이벤트 전달이 없다"고 함 (이미 있고 e2e 로 동작 확인) | 통과하는 테스트가 반증: s4, s5 |

---

## F-1. story-factory ↔ decision-pet: 저장 파일 이름 불일치 (`data.json` vs `state.json`)

- **어떤 시나리오**: 시나리오 7 — decision-pet 을 자기 CLI(`add` → `skip` ×5, `import-vjournal`)로 묘지를 만든 뒤
  story-factory README 예시대로 `storyfactory run --decision-pet ~/.decision-pet ...` (폴더 지정).
- **기대**: 묘지 카드(`graveyard_count`, `graveyard_tomb`, `declared_quit`)가 소재로 나온다.
- **실제**: `run.json` 경고에 `decision-pet: 파일이 없어 건너뜀`, decision-pet 카드 0장. 파일을 직접 주면
  (`--decision-pet ~/.decision-pet/state.json`) 정상으로 3장 나온다(내용 형식은 맞음).
- **원인 추정 파일:줄**
  - `story-factory/storyfactory/seeds.py:112` — `_resolve(path, "data.json")`: 폴더면 `data.json` 을 붙임.
  - `decision-pet/decision_pet/storage.py:81` — `self.path = self.dir / "state.json"`: 실제 저장 파일.
  - `story-factory/README.md:67` 표와 `seeds.py:4, 29` 주석도 `data.json` 으로 적혀 있음.
  - story-factory 테스트 픽스처(`tests/helpers.py:38,52`)가 `data.json` 을 직접 만들어 써서 단위 테스트에선 안 드러남.
- **제안 수정**: `from_decision_pet` 에서 폴더면 `state.json` 을 먼저 찾고, 없으면 예전 이름 `data.json`
  (`_resolve` 가 후보 목록을 받게). README 표·주석을 `state.json` 으로 고치고, 픽스처도 decision-pet 의
  `Store().save()` 가 만드는 이름을 쓰도록.

## F-2. cool-off `import-steam` 이 환불한 게임(`refunded: true`)을 그대로 구매로 가져옴

- **어떤 시나리오**: 추가 시나리오(돈, `test_s9_money`) — steam-buddy CLI 로 `buy` → `rate 1` → `refunded`, 그다음 `cool-off import-steam`.
- **기대**: steam-buddy 는 "환불 처리로 표시했어요. 돌아보기 통계에서 빠져요."라고 하고, decision-pet 의회도
  환불 게임을 뺀다(`decision-pet/decision_pet/council.py:77`). cool-off 도 환불 게임은 지출·후회에서 빼야 한다.
- **실제**: cool-off `purchases` 에 30,000원 구매(만족도 1)로 들어가고 `report` 에 "지출 30,000원", "후회 목록: 환불한 게임"이 나온다.
  그 결과 story-factory 가 cool-off 를 읽어 "후회한 소비" 사연을 만들고, decision-pet 쿨링오프 의원도 "후회 목록"으로 반대표를 던진다.
- **원인 추정 파일:줄**: `cool-off/cool_off/importers.py:90-105` — `games` 를 돌며 `refunded` 를 보지 않음
  (무료 게임만 건너뜀, `if not name or price <= 0`).
- **제안 수정**: 루프 안에 `if game.get("refunded"): result["skipped"] += 1; continue` 추가. 이미 가져온 뒤 환불된 경우를 위해
  같은 키의 기존 스팀 구매가 있으면 `refunded: true` 표시(또는 제거)하고 `report` 합계·후회 목록에서 빼기. README 71행에 한 줄 명시.

## F-3. morning-brief 카드 `actions` 와 phone-home 책상 action 형식 불일치

- **어떤 시나리오**: 시나리오 2 — paper-butler 고지서 → morning-brief 납부 카드 → phone-home 책상.
- **기대**: phone-home README: "실행 버튼은 설정 `desk.actions` 화이트리스트에 있는 id 만". 사용자가 화이트리스트에
  넣으면 납부 카드의 "완료 처리"를 책상에서 실행할 수 있어야 한다.
- **실제**: morning-brief 는 `actions` 를 "사람이 읽는 다음 행동 문구"(`"완료 처리: python -m paperbutler done 1"`)로 내보내고,
  phone-home 은 문자열 action 을 **id** 로 해석한다. 화이트리스트 키는 `^[a-z0-9][a-z0-9_-]{0,31}$` 만 허용하므로
  그 문장을 키로 넣으면 설정 검증이 `ValueError` 로 거부한다. 책상에는 항상 비활성 버튼(라벨=문장)만 보인다.
  (카드 표시 자체는 정상: 시나리오 1·2 통과. 문구대로 CLI 를 실행하면 카드가 사라지는 것도 확인)
- **원인 추정 파일:줄**
  - `morning-brief/mbrief/sources/paper_butler.py:59, 72` — 문장 하나짜리 `actions`.
  - `morning-brief/README.md:66` — 계약: 문자열 배열(사람이 읽는 문구).
  - `phone-home/phonehome/desk.py:58-60` — 문자열이면 `{"id": 문자열}`.
  - `phone-home/phonehome/config.py:135` — action id 정규식 검사.
- **제안 수정** (둘 중 하나, 두 README 의 계약 설명을 같이 고침)
  1. phone-home: 문자열 action 은 **라벨 전용**(id 없음, 버튼 대신 글)으로 보여 주고, 실행은 객체 `{id, label}` 만. (계약 변경 없음, 권장)
  2. morning-brief: `actions` 를 `[{"id": "paperbutler-done", "label": "완료 처리: ..."}]` 처럼 객체로 내고
     `contract.validate_cards` 를 갱신(이 경우 `version` 올림).

## F-4. steam-buddy `buy` 에 오프라인 경로가 없음 (`--name`·`--price` 를 줘도 네트워크 먼저)

- **어떤 시나리오**: 시나리오 7·8 시딩 — steam-buddy 구매 기록을 자기 CLI 로 넣기.
- **기대**: `buy <appid> --name X --price N` 처럼 필요한 값을 다 주면 스토어를 부르지 않는다(외부 네트워크 금지 환경, CI).
- **실제**: 항상 `self.client.app_details(appid)` 를 먼저 호출하고 실패(`SteamError`)할 때만 입력값을 쓴다. 네트워크가 있으면
  store.steampowered.com 으로 요청이 나가고, 막힌 환경에선 타임아웃(15초)까지 기다린다.
- **원인 추정 파일:줄**: `steam-buddy/steam_buddy/cli.py:169-178` (`cmd_buy`), 타임아웃 `steam_buddy/steam.py:22`.
- **제안 수정**: `--name` 과 `--price` 가 모두 있으면 `app_details` 를 건너뛰거나 `--offline` 플래그 추가.
  (e2e 는 steam-buddy 가 문서화한 주입점 `SteamClient(fetch=...)` 로 '오프라인' fetch 를 넣고 CLI `main` 을 그대로 실행해 우회)

## F-5. "오늘" 기준 시각대 차이: morning-brief(+09:00 고정) ↔ s21-sidekick 알람 게이트(PC 로컬)

- **어떤 시나리오**: 시나리오 1(알람 게이트) — PC 시간대가 KST 가 아닐 때 `mbrief run`(날짜 생략) 뒤 알람 승인.
- **기대**: 같은 아침에 만든 `brief-latest.json` 을 sidekick 이 "오늘 것"으로 읽는다.
- **실제**: morning-brief 는 `tz_offset_minutes`(기본 540)로 날짜를 정하고, sidekick `morning_gate` 는 PC 로컬 날짜로 비교한다.
  예: UTC PC 에서 15:00~24:00 UTC 에 만든 브리핑은 `date` 가 다음 날이라 sidekick 이 `stale`(낡은 파일)로 보고 낭독하지 않는다.
  (테스트는 `--date` 로 로컬 날짜를 명시해서 통과시킴. 실제 시각에 따라 결과가 달라 xfail 로 고정하지 않음)
- **원인 추정 파일:줄**: `morning-brief/mbrief/cli.py:47,56` (고정 오프셋 시각대), `s21-sidekick/sidekick/morning_gate.py:72`
  (`self.clock().date()` 로컬 날짜). paper-butler·phone-home·voice-journal 도 로컬 날짜를 씀.
- **제안 수정**: morning-brief 기본값을 PC 로컬 시각대(`datetime.now().astimezone()`)로 하고 `tz_offset_minutes` 는 명시했을 때만 쓰기.
  또는 `brief-latest.json` 에 `tz_offset_minutes` 를 넣고 sidekick 이 그 시각대로 "오늘"을 계산.

## F-6. decision-pet 의 LLM 주소를 CLI/환경변수로 줄 수 없음

- **어떤 시나리오**: 시나리오 8 — `decide --mode council ... --llm` 을 테스트 안에서 띄운 가짜 OpenAI 호환 서버로.
- **기대**: 다른 프로젝트처럼 환경변수(`*_LLM_URL`) 또는 옵션으로 엔드포인트를 지정.
- **실제**: `state.json` 의 `config.llm.endpoint`(기본 `http://localhost:1234/v1`)만 읽고, 이를 바꾸는 명령이 없다.
  상태 파일을 손으로 고치지 않고는(= e2e 규칙 위반) 검증할 수 없어 skip. (`--llm` 없는 의회 표결은 전부 통과)
- **원인 추정 파일:줄**: `decision-pet/decision_pet/storage.py:11-16` (DEFAULT_CONFIG.llm), `decision_pet/cli.py:12-57` (설정 명령 없음).
- **제안 수정**: `DECISION_PET_LLM_URL`/`DECISION_PET_LLM_MODEL` 환경변수 또는 `config set llm.endpoint ...` 명령 추가
  (jjkit `llm.client_from_env` 와 같은 방식이면 루프백 제한도 함께 얻음).

## F-7. 문서가 오래됨: "sidekick 에는 외부 say/이벤트 전달 엔드포인트가 없다"

- **어떤 시나리오**: 시나리오 4(jarvis notify.say → sidekick), 5(life-nagger sidekick 채널, sidekick forward_events → nagger 수신기).
- **기대**: 문서와 코드가 같은 상태를 말함.
- **실제**: 두 경로 모두 **실제로 동작**한다(`POST /api/say` 200, 이벤트 큐에 `module=jarvis`/`nagger-mombot`;
  `/api/phone_event` → nagger `/api/event` → `counters.jsonl`). 그런데 문서·주석은 아직 "없음/TODO" 라고 안내한다.
- **원인 추정 파일:줄**
  - `jarvis-hub/README.md:193-212` (5절, "아직 없습니다" + sidekick 에 추가할 코드 예시)
  - `jarvis-hub/jarvis/handlers/notify.py:3-9` (모듈 docstring)
  - `life-nagger/README.md:95-100, 110` ("TODO (s21-sidekick)", "전달 코드는 TODO")
  - `life-nagger/nagger/outputs.py:62-68` (`SidekickSay` docstring TODO)
- **제안 수정**: "s21-sidekick v0.1 부터 `POST /api/say`(X-Sidekick-Token, `say.max_per_minute`)와 `forward_events` 가 있음"으로
  바꾸고 예시 코드 블록 삭제. jarvis 는 429(분당 제한) 때 재시도 없이 `delivered:false` 로 끝난다는 점도 적기.
