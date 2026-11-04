# homelab-e2e

프로젝트끼리 실제로 말이 통하는지 확인하는 교차 프로젝트 종단 간(e2e) 통합 테스트

각 프로젝트는 자기 테스트(가짜 opener, 픽스처)로는 통과하지만, **서로 실제로 붙여 본 적은 없었습니다.**
여기서는 임시 HOME 안에서 형제 프로젝트의 **진짜 서버를 하위 프로세스로 띄우고**(`python -m <pkg> serve ...`),
**각 프로젝트 자기 CLI/API 로 데이터를 심은 뒤**, 문서에 적힌 계약이 끝까지 이어지는지 확인합니다.
찾은 문제는 [FINDINGS.md](FINDINGS.md) 에 적었습니다(다른 폴더는 고치지 않음).

<!-- demo:start -->
<!-- demo:end -->

## 실행

Python 3.12 이상, 외부 패키지 없음.

```
cd homelab-e2e
python -m homelabe2e projects                 # 시나리오별로 필요한 형제 프로젝트가 있는지
python -m unittest discover -s tests -v       # 전체 (약 1분)
python -m unittest tests.test_s3_approval -v  # 시나리오 하나만
# 저장소 루트에서: python run_all_tests.py --only homelab-e2e
```

형제 프로젝트 폴더가 없으면 그 시나리오는 `skip` 됩니다.

## 시나리오

| # | 파일 | 확인하는 계약 |
|---|---|---|
| 1 | `test_s1_morning.py` | voice-journal `POST /entry`(토큰) → `mbrief run` → `brief-latest.json`/`cards-latest.json` 이 `mbrief.contract` 통과 → phone-home `/desk` 에 같은 카드. `--speak` 가 sidekick `/api/say` 로. 알람 게이트(`morning_gate`)는 승인 때만 `speak_text` 낭독. phone-home 한 줄 일기 프록시 |
| 2 | `test_s2_paper.py` | paper-butler `add`(합성 가스 고지서 + 카드 결제 문자 + 온라인 영수증) → morning-brief `bill`/`refund` 카드, 카드 문구의 `done` 명령으로 사라짐. `export-csv --what ledger` → cool-off `import-ledger`(BOM, 중복 방지). cool-off 구독 → `subscription` 카드. sidekick `paperbutler_returns` |
| 3 | `test_s3_approval.py` | phone-home 런처 건강 점(초록/노랑/빨강), `/s/sidekick/api/ping` 프록시와 토큰 교체, HTML 경로 고쳐 쓰기, 경로 탈출 404. sidekick 훅 스크립트가 위험 명령에서 결재 → `/desk` 에 보임 → 책상 승인 → 훅이 `allow` JSON. `rm -rf` 는 책상에서 링크만(403) |
| 4 | `test_s4_jarvis.py` | jarvis 허브 + 워커, `shell.allowed` 허용 명령 → 완료 → `notify.say` 작업 → 진짜 sidekick 이벤트 큐. 승인 필요 작업을 phone-home 책상에서 승인. morning-brief jarvis 어댑터 `jobs-done` |
| 5 | `test_s5_nagger.py` | life-nagger `SidekickSay` 채널(`X-Sidekick-Token`) → sidekick `/api/say`, 틀린 토큰은 오류로 기록. sidekick `forward_events` → life-nagger 이벤트 수신기 → `report --week` |
| 6 | `test_s6_objmem.py` | object-memory `POST /api/frame`(합성 JPEG, 가짜 VLM) → sidekick 출발 체크 `objmem_items` 가 "지갑: 오늘 HH:MM 책상 위에서 봤어요", 모르는 물건은 "모름", 서버가 꺼지면 통째로 생략 |
| 7 | `test_s7_story.py` | life-nagger `count`, decision-pet `add/skip/import-vjournal`, steam-buddy → cool-off `import-steam` 로 심은 데이터 → story-factory `run`(가짜 LLM) → shorts-linter 하위 프로세스 결과(titles/promise/disclose/similar). kakao-wrapped `--anonymize --json` 만 받기. `--submit-jarvis` → 진짜 허브 `story.batch` |
| 8 | `test_s8_council.py` | decision-pet 결정 의회가 cool-off(쿨링오프 중) / steam-buddy(안 켜본 게임) / paper-butler(이번 달 승인 합계) / voice-journal(절약 선언) / life-nagger(엄마봇 경고) 실제 파일을 읽어 표결 |
| 9 | `test_s9_money.py` | 같은 날 같은 금액의 Steam 결제가 steam-buddy·paper-butler 양쪽에서 와도 cool-off 에 한 번만. 환불 게임 처리(F-2) |

`expectedFailure` 는 FINDINGS.md 에 적은 진짜 버그(F-1~F-3)에만 붙였습니다. 고쳐지면 "unexpected success" 로 알려 줍니다.

## 하네스 (`homelabe2e/`)

- `harness.py` — `Stack`(임시 HOME·로그·하위 프로세스·가짜 LLM 소유, `close()` 에서 terminate→kill 후 삭제),
  `free_port()`(실행 시점에 빈 포트, 충돌로 죽으면 다른 포트로 재시도), `token()`(실행 시점 생성),
  `http()/http_json()`(프록시 없음, 타임아웃), `wait_until()`, `FakeLLM`(127.0.0.1 port 0 의 OpenAI 호환 `/v1/chat/completions`).
- `services.py` — 각 서버를 그 프로젝트 CLI 로 띄우는 도우미(sidekick, voice-journal, phone-home, object-memory, jarvis 허브/워커).
- `seed.py` — 각 프로젝트 자기 CLI 로 데이터 심기. steam-buddy 는 오프라인 `buy` 경로가 없어(F-4) 그 프로젝트의
  주입점(`SteamClient(fetch=...)`)으로 네트워크를 막고 CLI `main` 을 실행.
- 계약 검사 함수(`mbrief.contract`), life-nagger 채널/수신기/가짜 플랫폼처럼 **그 프로젝트 테스트가 import 해서 쓰는 것**만 이 프로세스에서 import 합니다.

## 데이터와 개인정보

- 모든 데이터는 테스트마다 새로 만드는 임시 폴더(`HOME`/`USERPROFILE`/`APPDATA` 를 그쪽으로)에만 쓰고 끝나면 지웁니다.
  실제 `~/.vjournal`, `~/.paperbutler` 등은 건드리지 않습니다. 자식 프로세스 환경에서는 프록시 변수와 각 프로젝트의
  데이터 위치·토큰 환경변수(`VJOURNAL_DB`, `SIDEKICK_TOKEN` …)를 지웁니다.
- 외부로 나가는 것: 없음. 모든 서버는 `127.0.0.1`, LLM/VLM 은 테스트 안의 가짜 서버.
- 고지서·결제 문자·카톡 대화는 실행 시점에 만드는 합성 텍스트이고 실존 번호·이름이 아닙니다. 토큰도 실행 시점에 생성합니다.

## 한계

- life-nagger 엔진은 Windows 플랫폼만 있어 리눅스 CI 에서는 `nagger run` 대신 그 프로젝트의 `FakePlatform` 으로 엔진을 돌립니다.
- decision-pet `--llm`(F-6)과 시각대 차이(F-5)는 자동 검증하지 못했습니다.
- 폰 브라우저(Web Speech, 카메라)는 범위 밖입니다. 폰 페이지는 HTML 이 프록시로 고쳐 쓰이는 것까지만 봅니다.

## 공통 기반 (jjkit)

jjkit 모듈을 쓰지 않습니다(표준 라이브러리만).

## 테스트

```
cd homelab-e2e
python -m unittest discover -s tests -v
```

네트워크 없이 돕니다(127.0.0.1 하위 프로세스, 가짜 LLM, 임시 폴더). 모든 대기에는 타임아웃이 있습니다.
