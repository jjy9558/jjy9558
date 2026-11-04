# morning-brief

아침 방송국: 흩어진 아침 정보를 한 판으로 합쳐 읽어 주는 브리핑

게임 점검·서비스 종료, 납부·환불 기한, 구독 갱신, 어제 선언 결과, 밤새 Claude 작업, 자비스 승인 대기, 밤사이 소음,
커뮤니티 핵심 3줄을 **우선순위와 말할 시간 예산**으로 잘라서 (1) 한 문장 묶음(`speak_text`)으로 읽어 주고
(2) md/json 문서와 (3) 폰 카드 목록으로 남깁니다. 다른 프로젝트의 데이터는 **읽기만** 합니다.

<!-- demo:start -->
<!-- demo:end -->

## 설치

Python 3.12 이상, 외부 패키지 없음. 폴더에서 바로 실행합니다. 선택: `pip install .` 하면 `morning-brief ...` 로도 쓸 수 있습니다.

```
cd morning-brief
cp mbrief.example.json mbrief.json      # 경로를 내 환경에 맞게 고치기 (mbrief.json 은 .gitignore 대상)
python -m mbrief sources                # 어댑터 상태표: 어디가 연결됐나
python -m mbrief run                    # 오늘 브리핑을 만들어 out/ 에 쓰기
```

## 명령

```
python -m mbrief run [--date YYYY-MM-DD] [--dry-run] [--speak] [--push] [--json]
python -m mbrief sources                      # 어댑터 상태표 (정상/꺼짐/건너뜀/연결 안 됨/오류)
python -m mbrief preview --seconds 120        # 말할 시간에 맞춰 무엇이 읽히나 (파일을 쓰지 않음)
python -m mbrief check [--out 폴더]            # out/ 의 계약 파일 두 개 검사
```

- `--date` 는 그 날짜 `default_time`(기본 07:30) 아침으로 만듭니다. 설정 파일은 `--config` > `$MBRIEF_CONFIG` > `./mbrief.json` 순이고, 없으면 기본값입니다.
- `--speak` 는 s21-sidekick `POST /api/say`(헤더 `X-Sidekick-Token`)로 `speak_text` 를 읽게 합니다.
- `--push` 는 ntfy 로 제목 목록을 보냅니다. **금액·선언 같은 개인 정보가 들어가므로** 자기 서버나 추측 불가능한 topic 을 쓰세요.
- 전달에 실패하면 종료 코드 3, 토큰/topic 이 없으면 "건너뜀"(종료 코드 0)입니다.
- `--dry-run`: 네트워크 호출 0(자비스 어댑터는 건너뜀), 파일 쓰기 0, `--speak/--push` 무시. 로컬 파일 읽기는 합니다.
- 종료 코드: 0 정상, 1 `sources`/`check` 에서 오류 발견, 2 설정 오류, 3 전달 실패.

## 출력과 계약 파일

`out/` (설정의 `out_dir`)에 매번 다음을 씁니다. 같은 날 두 번 돌려도 입력이 같으면 바이트까지 같습니다(시각 필드 없음).

| 파일 | 내용 |
|---|---|
| `brief-YYYY-MM-DD.md` / `.json` | 사람이 읽는 문서 / 같은 내용의 JSON |
| **`brief-latest.json`** | **계약 1.** s21-sidekick 알람 게이트(`morning_gate`)가 이 파일만 읽습니다 |
| **`cards-latest.json`** | **계약 2.** phone-home 사장님 책상이 이 파일만 읽습니다 |

폴더끼리는 이 두 파일로만 결합합니다. 읽는 쪽은 모르는 키를 무시하고, `date` 가 오늘이 아니면 낡은 파일로 보세요.

`brief-latest.json`
```
{"version": 1, "date": "2026-11-02", "quiet_day": false,
 "speak_text": "좋은 아침입니다. 11월 2일 월요일. 오늘 점검: 로스트아크 오전 10시. ...",   # 300자 이내, 항상
 "tz": "+09:00",                                                                          # date 를 계산한 UTC 오프셋 (참고용)
 "reject_text": "일어나면 해 드립니다",                                                    # 한 줄. 반려 상태일 때 이것만 말하기
 "speak_budget_chars": 300, "dropped": 15,
 "sections": [{"id": "paper_butler", "title": "납부·기한",
               "items": [{"source", "kind", "text", "detail", "priority", "due", "spoken"}]}],
 "sources": {"paper_butler": {"status": "ok|disabled|skipped|missing|error", "ok": true, "items": 3, "note": ""}}}
```

`cards-latest.json` — 배열, **마감(`due`) 오름차순**, `due` 없는 카드는 맨 뒤. 키는 정확히 다섯 개.
```
[{"kind": "bill", "title": "서울도시가스 가스요금 32,400원", "detail": "납부기한 2026-11-04 (모레)",
  "due": "2026-11-04",              # "YYYY-MM-DD" | "YYYY-MM-DDTHH:MM+HH:MM"(브리핑 시각대 오프셋) | null
  "actions": [{"id": "paperbutler.done:1", "label": "완료 처리",
               "text": "완료 처리: python -m paperbutler done 1"},   # 실행 가능한 명령에 대응하는 action 객체
              "공지 열기: https://..."]}]                          # 문자열 = 표시 전용 문장
```
`actions` 원소는 **문자열(표시 전용 문장) 또는 `{id, label[, text]}` 객체** 입니다. `id` 는 안정적인 action 키
(현재는 `paperbutler.done:<서류 번호>`), `label` 은 버튼 글자, `text` 는 사람이 읽는 전체 문장입니다.
읽는 쪽은 둘 다 받아야 하며 자기 화이트리스트에 없는 `id` 는 실행하지 않습니다(phone-home 은 문자열을 글자로만 보여 주고
`desk.actions` 에 맞는 객체 `id` 만 버튼으로 만듭니다). 예전 읽는 쪽은 모르는 형태를 무시하면 됩니다.

**날짜와 시각대.** `date` 는 기본적으로 브리핑을 만든 **PC 의 로컬 날짜**입니다(cool-off, s21-sidekick, paper-butler 와 같은 기준이라
UTC 미니PC 에서도 "기준일이 달라요" 경고가 없습니다). 설정 `timezone`: `"local"`(기본) | `"+09:00"` 같은 고정 오프셋 | `"UTC"` |
IANA 이름(`"Asia/Seoul"`, zoneinfo 가 있을 때). 어댑터와 출력(`date`, `due` 의 오프셋, 파일 이름)이 모두 이 시각대를 씁니다.
`tz` 는 그 오프셋을 알려 줄 뿐이니 소비자는 `date` 문자열을 자기 로컬 날짜와 비교하세요. 예전 `tz_offset_minutes` 는
`timezone` 을 적지 않은 설정에서만 계속 적용됩니다. sound-watch 의 자체 `utc_offset_minutes` 가 있으면 그 밤 구간은 그 값을 따릅니다.
`kind`: `maintenance shutdown event bill refund subscription cooloff-ended followup approved declaration first-task
blocked goal-due flag claude-blocked claude-night job-approval job-failed jobs-done device-offline noise sound-detect news`.
낭독 전용 줄(커뮤니티 3줄 등)은 카드가 아닙니다. 스키마 검사는 `mbrief/contract.py`(`validate_brief`, `validate_cards`).

## 우선순위와 말할 시간 예산

- 어댑터가 항목마다 우선순위(0~100)와 낭독 문장(`speak`, 모자라면 `speak_short`)을 냅니다. 예: 오늘 점검 90, 오늘 납부 90(자동이체는 -15),
  승인 대기 72, 구독 갱신 D-3 60, 어제 선언 미달 57, 커뮤니티 26~30.
- 설정의 `weights` 로 어댑터별 가중치를 바꾸고, **조용한 날**(`quiet_days.weekdays`, 기본 토·일)에는 `quiet_days.weights` 를 곱합니다(게임·커뮤니티 위주).
- 예산 = `speak.seconds × speak.chars_per_sec`(기본 60초 × 5자 = 300자), 상한 `speak.max_chars`(300 고정 상한: 설정으로 못 올림).
  높은 순으로 채우고 못 담은 건 "그 밖에 N건은 화면에서 확인하세요."로 접습니다. 빠듯하면 인사말, 꼬리말 순으로 빼서라도 가장 급한 항목은 온전히 말합니다.
- 낭독문은 마지막에 `speechify` 를 거칩니다: `D-2`→"2일 뒤", `2026-11-05`→"11월 5일", `10:30`→"오전 10시 30분", `45,200원`→"4만 5천 2백원", URL/마크다운 제거.
- `preview --seconds 120` 은 상한 없이 120초(600자) 기준으로 어디까지 읽히는지 보여 줍니다(계약 파일에는 300자만 담깁니다).

## 어댑터 (JSON 계약 우선, 실제 저장 형식 직접 읽기는 대비책)

각 어댑터는 `collect(cfg, ctx) -> list[Item]` 이고 하나가 죽어도 나머지는 나옵니다(상태 `error`, `sources` 에 ❌).
상태: ✅ ok / ➖ 꺼짐 / ⏸ 건너뜀(dry-run) / ⚠ 연결 안 됨(파일·설정 없음) / ❌ 오류.

| 어댑터 | 읽는 것 | 경로 설정 |
|---|---|---|
| `game_calendar` | `state.json` 의 `entries` 중 일정이 확인된(`is_notice` 아닌) 점검(오늘~2일), 서비스 종료(30일 이내), 이벤트(시작일/마감 임박) | `sources.game_calendar.state` (필수) |
| `community_briefing` | `out/YYYY-MM-DD[-N].json` 의 `overview` 상위 3줄(없으면 군집 제목). 어제까지만 | `sources.community_briefing.dir` (필수) |
| `paper_butler` | sqlite `mode=ro`: `documents.due_date`(미처리, 7일 전~14일 후), `refunds.deadline`(환불·반품) | `db` 또는 `$PAPERBUTLER_HOME`(기본 `~/.paperbutler`) |
| `voice_journal` | sqlite `mode=ro`: 어제 선언 판정(once/day/주간), 퇴근 3문답의 "내일 첫 할 일/막힌 것", 마감 임박 선언, 표시(flag)한 메모 | `db` 또는 `$VJOURNAL_DB`(기본 `~/.vjournal/journal.db`) |
| `claude_night` | `~/.claude/projects/**/*.jsonl` 을 jjkit `claude_logs` 로 읽어 전날 23시~오늘 9시 작업을 프로젝트별 요청/완료/막힘 3줄로. 결과는 jjkit `redact` 로 마스킹. voice-journal 의 야간 보고(`claude_ingest`)는 DB 에 저장되지 않아 같은 아이디어를 여기서 다시 만듦 | `projects_dir` (기본 Claude 로그 폴더) |
| `cool_off` | `data.json`: 구독 갱신(기본 3일 이내, 없는 날은 말일), 쿨링오프 끝난 위시, 30일 지난 사후 확인(수동 구매만), 구매 허가 후 미구매 | `data` 또는 `$COOLOFF_HOME`(기본 `~/.cool-off`) |
| `jarvis` | `GET /api/status`(헤더 `X-Jarvis-Device`/`X-Jarvis-Token`): 승인 대기, 최근 12시간 실패/완료 작업, `watch_devices` 오프라인 | `url`, `device`, 토큰(`token_env`=`JARVIS_TOKEN`) |
| `sound_watch` | sqlite `mode=ro`: 밤 창(sound-watch 설정의 `night`, 기본 22~06시)의 소음 이벤트와 노크/초인종 감지. 보정 전 dB 는 낭독하지 않음 | `data_dir` 또는 `$SOUNDWATCH_HOME` |

다른 프로젝트 코드는 import 하지 않고 형식만 따랐습니다. 형식이 바뀌면 해당 어댑터가 `error` 로 표시되며 나머지는 계속 갑니다.
토큰은 설정 파일의 `token` 이나 환경변수(`SIDEKICK_TOKEN`, `NTFY_TOKEN`, `JARVIS_TOKEN`)로 줍니다. 저장소에 올리지 마세요.

### JSON 계약 모드 (`paper_butler`, `game_calendar`, `voice_journal`, `cool_off`)

네 형제 프로젝트는 안정된 JSON 출력(schema v1, 각자 `<pkg>/contract.py` 의 `validate()`)을 내므로, 이 네 어댑터는 파일 형식을 직접 읽기보다
그 명령을 먼저 씁니다. 어댑터 설정의 `mode`:

| mode | 동작 |
|---|---|
| `auto` (기본) | JSON 명령을 먼저 쓰고, 실패하면 경고를 남기고 기존 직접 읽기로 되돌아갑니다 |
| `json` | JSON 명령만 씁니다. 실패하면 상태 `error` |
| `direct` | 명령을 부르지 않고 기존처럼 파일/DB 를 직접 읽습니다 (동작 변화 없음) |

| 어댑터 | 부르는 명령 | 데이터 위치를 넘기는 법 |
|---|---|---|
| `paper_butler` | `paperbutler --today D due --json --days N --overdue-days M` (납부), `... refunds --json --days K` (환불) | `db` 가 있으면 그 폴더를 `PAPERBUTLER_HOME` 으로 |
| `game_calendar` | `gamecal alerts --json --days N [--config <gamecal_config>]` | gamecal 의 설정(`gamecal_config`, 기본은 실행 폴더의 `gamecal.json`). `state` 는 direct 전용 |
| `voice_journal` | `vjournal morning --json --date D` | `db` 가 있으면 `VJOURNAL_DB` |
| `cool_off` | `cool_off check --json` (비대화형, 읽기 전용) | `data` 가 있으면 그 폴더를 `COOLOFF_HOME` |

- 실행은 인자 목록, `shell=False`, `timeout`(기본 20초)으로만 합니다. 환경변수는 그대로 물려주고(데이터 폴더 변수 포함) 위 표의 값만 덮어씁니다.
- 명령 찾기: 설정 `command: ["python3", "-m", "pkg"]` 가 있으면 그것, 없으면 `[현재 파이썬, "-m", <pkg>]`. 작업 폴더(`cwd`)는 설정값, 없으면 형제 폴더
  `../<프로젝트>` 가 있을 때 그곳, 그것도 없으면 설치된 패키지에 맡깁니다.
- 출력은 JSON 이고 `version == 1` 이어야 합니다. 모르는 버전, 깨진 JSON, 모양 불일치, 비정상 종료, 시간 초과, **기준일이 mbrief 의 오늘과 다름**(`--date` 로 다른
  날 브리핑을 만들 때 `gamecal`/`cool_off` 는 날짜를 못 받습니다)이면 `auto` 에서는 직접 읽기로 되돌아갑니다.
- **어느 방식이 쓰였는지**는 `sources.<이름>.note` 에 남습니다: `모드: json`, `모드: direct`, `모드: direct (json 실패: 이유)`. `brief-latest.json` 의 키는 그대로입니다.
- `game_calendar`: 진행 중(`ongoing`) 이벤트는 `ends_in_days` 로 "이벤트 이틀 뒤 종료"처럼 말합니다(종일 일정의 `ends_in_days` 는 배타적 끝 기준이라 마지막 날은 하루 적게 셉니다).
  서비스 종료는 gamecal 쪽이 60일 고정이라 `shutdown_days` 가 그보다 크면 60일까지만 보입니다. `patch` 는 브리핑 대상이 아닙니다.
- 직접 읽기와의 차이: `voice_journal` 은 JSON 에 퇴근 3문답의 "막힌 것"이 없어 json 방식에서는 빠지고, 첫 할 일은 어제 기록한 것만 나옵니다.
  `cool_off` 의 쿨링오프 끝 항목은 JSON 에 사유(`why`)와 시각이 없습니다. 원본 파일이 없을 때 직접 읽기는 "연결 안 됨"이지만 JSON 명령은 빈 목록을 돌려줍니다.

## 데이터와 개인정보

- 읽는 곳: 위 표의 로컬 파일/DB(전부 읽기 전용, 원본 mtime 불변을 테스트로 확인), 자비스 허브 한 번(`--dry-run` 이면 안 함).
- 쓰는 곳: `out/` 만. 파일은 임시 파일에 쓴 뒤 교체합니다.
- 외부로 나가는 것: `--speak`(로컬 sidekick), `--push`(ntfy 서버). 둘 다 옵션이고 기본은 아무것도 보내지 않습니다.
- 일기·선언·Claude 대화 요약에는 `redact` 를 적용하지만 정규식 기반이라 완벽하지 않습니다. 밖으로 나가는 `--push` 는 제목 목록만 보냅니다.

## 공통 기반 (jjkit)

`mbrief/_jjkit/`에 jjkit 복사본(claude_logs, llm, notify, redact, textwidth)이 들어 있습니다. **직접 고치지 말고** 원본 `jjkit/jjkit/`을 고친 뒤 저장소 루트에서 다시 복사하세요.

```
python tools/jjkit_sync.py morning-brief
python tools/jjkit_sync.py --check morning-brief
```

(`llm` 은 plan 의 기본 모듈 세트라 복사해 두었지만 이번 버전에서는 쓰지 않습니다. 요약은 전부 규칙 기반이라 로컬 LLM 없이 돕니다.)

## 테스트

```
cd morning-brief
python -m unittest discover -s tests -v
```

네트워크 없이 돕니다(가짜 opener, 임시 폴더, 가짜 JSON 명령 스크립트). 기존 어댑터 테스트는 `mode: direct` 로 고정하고, `tests/test_json_mode.py` 가 json/되돌아가기/direct 를 어댑터마다 확인하며 실제 형제 프로젝트의 `--json` 을 빈 데이터 폴더로 한 번씩 부릅니다(형제 폴더가 없으면 건너뜀). 어댑터별 픽스처는 `tests/fixtures/` 에 있고 sqlite 는 원본 프로젝트의 `SCHEMA` 를 그대로 가져온 `.sql` 을 임시 DB 로 만들어 씁니다.
plan-v2 D-1 인수 테스트(우선순위·예산 / 어댑터 격리 / mode=ro 불변 / dry-run 네트워크 0 / 같은 날 재실행 동일)와 낭독 변환, 계약 스키마 테스트가 `tests/test_acceptance.py` 에 있습니다.
