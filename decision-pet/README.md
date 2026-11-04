# 결정해주는 데스크톱 펫 (decision-pet)

결정 장애가 있는 Claude Code 헤비 유저를 위한 작은 데스크톱 펫입니다.
화면 구석에 앉아서 **"오늘은 이거 해"** 라고 말해 줍니다. Python 3.12+ (3.12에서 검증) 표준 라이브러리(tkinter)만 사용합니다.

## 컨셉

- **지금 뭐 해?** — 할 일 풀에서 하나만 골라 줍니다. 규칙은 투명합니다:
  - 15분짜리 +3점, 5분짜리 +1.5점
  - 오래 미룬 일은 가점 (3일째 미뤄서 +2점, 최대 +8)
  - 미룬 횟수 가점 (회당 +0.5, 최대 +2)
  - 최근에 추천한 일은 감점 (1시간 내 -4, 6시간 내 -2.5, 24시간 내 -1)
  - 작은 무작위 (0~0.5점)
  - 결과에는 항상 이유가 붙습니다. 예: `15분짜리라 부담이 적어서 +3점, 3일째 미뤄서 +2점 (합계 5.3점)`
- **오늘의 15분 실험** — 매일 같은 날엔 같은 실험 하나. 기본 20개 제공, 직접 편집 가능.
- **결정해줘** — 선택지 2~5개를 주면 짧은 이유와 함께 정해 줍니다.
  - 동전: 무작위
  - 점수표: 기준 2~3개(이름:가중치)와 선택지별 1~5점 → 가중 평균으로 계산
  - LLM(선택): OpenAI 호환 로컬 엔드포인트. 연결 실패 시 동전으로 자동 폴백
  - 다음 날 "어제 결정 만족?"을 물어 만족도 기록을 쌓습니다.
- **안 한 일 묘지** — 5번 미루거나 30일이 지난 할 일은 묘비와 함께 묻힙니다.
  `여기 잠들다: 블로그 시작하기 (2026.10.20~2026.11.20, 미룬 횟수 7)` — 부활도 가능.

## 설치/실행

```
python -m decision_pet gui          # 펫 실행 (우클릭 메뉴)
python -m decision_pet add "블로그 초안 다듬기" --effort 15   # effort: 5/15/60
python -m decision_pet now          # 지금 뭐 해?
python -m decision_pet done         # 끝냈어 (id 생략 시 현재 할 일)
python -m decision_pet skip         # 미룰래
python -m decision_pet list
python -m decision_pet decide "라면" "김밥" "굶기"
python -m decision_pet decide "맛있는 거" "싼 거" --mode score --criteria "맛:3,가격:2"
python -m decision_pet decide "라면 vs 김밥" --mode llm
python -m decision_pet followup     # 어제 결정 만족도 입력
python -m decision_pet history      # 모드별 평균 만족도
python -m decision_pet graveyard [--resurrect ID]
python -m decision_pet experiment   # 오늘의 15분 실험 (experiment-done 으로 완료 표시)
python -m decision_pet import-todos TODO.md
python -m decision_pet import-vjournal ~/.vjournal/journal.db   # 포기한 선언을 묘지로
python -m decision_pet decide --mode council "이 게임 살까?"      # 결정 장애 종결 의회
python -m decision_pet lunch --menu "김치찌개,라면,돈까스,비빔밥" # 점심 운명 로딩
```

GUI 우클릭 메뉴: 지금 뭐 해? / 할 일 추가 / 결정해줘 / 오늘의 실험 / 묘지 보기 / 끝냈어 / 미룰래 / 종료.
펫은 드래그로 옮길 수 있고, 더블클릭하면 "지금 뭐 해?"가 실행됩니다.
Linux에서는 `python3-tk` 패키지가 필요합니다. CLI와 테스트는 tkinter 없이 동작합니다.

### Claude Code 연동

`TODO.md` 같은 마크다운의 미완료 항목(`- [ ]`)을 가져옵니다. 완료(`- [x]`) 항목은 무시하고, 같은 제목은 중복 추가하지 않습니다.
`[5m]`, `(15분)` 같은 표기로 시간을, `#태그`로 태그를 지정할 수 있습니다.

```
python -m decision_pet import-todos TODO.md
```

## 묘지 가져오기: voice-journal (`import-vjournal`)

```
python -m decision_pet import-vjournal <journal.db>      # 기본 DB: $VJOURNAL_DB 또는 ~/.vjournal/journal.db
```

voice-journal의 sqlite를 **읽기 전용**(`file:...?mode=ro`)으로 열어 `goals` 테이블을 읽습니다. 파일은 수정되지 않습니다.
아래 선언이 "포기"로 묘지에 묻힙니다.

- `status='dropped'` (명시적으로 접음)
- `status='open'` 인데 `deadline`이 오늘보다 과거이고 진행 합계(`goal_events`)가 목표(`target`, 없으면 1)에 못 미침

묘비: `여기 잠들다: 선언했다가 포기 (2026-10-01, 이번 달 안에 블로그 글 쓰기로 했다)` (선언한 날짜, 원문).
선언 id로 중복을 막으므로 여러 번 실행해도 한 번만 묻힙니다(`source_key`: `vjournal:<id>`). `done` 선언과 기한이 남은 선언은 건드리지 않고, 부활(`graveyard --resurrect`)도 됩니다.

## 결정 장애 종결 의회 (`decide --mode council`)

```
python -m decision_pet decide --mode council "이 게임 살까?" [--llm] [--cool-off PATH] [--steam-buddy PATH]
                                              [--life-nagger DIR] [--voice-journal PATH] [--paper-butler PATH]
```

"의원"들이 근거 데이터에서 한 줄씩 말하고(찬성/반대/기권) 표결합니다. 의원은 **데이터가 있을 때만** 출석하고, 파일이 없거나 깨졌으면 조용히 빠집니다. 모든 소스는 읽기 전용입니다.

| 의원 | 읽는 곳 (기본 위치) | 근거 | 투표 |
|---|---|---|---|
| 쿨링오프 | cool-off `data.json` (`$COOLOFF_HOME` 또는 `~/.cool-off`) | `wishes`(참은 것=아낀 돈, 쿨링오프 중), `purchases`(만족도 2 이하·거의 안 씀=후회) | 질문에 쿨링오프 중 항목 이름이 있거나 대기/후회가 있으면 반대, 깨끗하면 찬성 |
| 스팀 | steam-buddy `data.json` (`$STEAM_BUDDY_HOME` 또는 `~/.steam-buddy`) | 안 켜본 게임 수(0.1시간 미만, 환불 제외), 전체 시간당 비용 | 게임 질문일 때만: 3개 이상 반대, 0개 찬성, 그 외 기권 |
| 엄마봇 | life-nagger 상태 폴더 (`~/.life-nagger`) `status.json`, `work.json` | 현재 새벽 경고 단계, 이번 달 게임 시간 | 게임 질문일 때만: 경고 중이거나 한도(기본 40시간) 이상이면 반대, 한도의 1/4 미만 찬성 |
| 일기 | voice-journal DB (`$VJOURNAL_DB` 또는 `~/.vjournal/journal.db`) | 열린 목표·이번 달 일기 중 "안 산다/충동구매/지출 줄이기/절약" 류 문장 | 있으면 반대(원문 인용), 없으면 기권 |
| 가계부 | paper-butler `paperbutler.db` (`$PAPERBUTLER_HOME` 또는 `~/.paperbutler`) | 이번 달 `payments` 중 승인(approve)이고 취소되지 않은(active) 금액 합 | 예산(기본 50만원) 초과 반대, 절반 미만 찬성, 그 외 기권 |
| 의장 | (항상 출석) | 동수(0표 포함)일 때만 `crc32(질문)` 홀짝으로 캐스팅보트 | 같은 질문엔 항상 같은 판결 |

**주의: life-nagger는 새벽 경고 "횟수"를 파일로 남기지 않습니다.** 엄마봇 상태는 메모리에만 있고, 파일에는 현재 상태(`status.json`의 `mombot.state/stage`)와 날짜별 게임 초(`work.json`)만 있습니다. 그래서 "지금 경고 중인지"와 "이번 달 게임 시간"을 근거로 씁니다.

경로 우선순위: 명령줄 옵션 > `state.json`의 `config.sources` > 환경변수/기본 위치.
`config.council.monthly_budget`, `config.council.game_hours_limit`로 기준을 바꿉니다.

`--llm`을 주면 설정된 로컬 LLM(OpenAI 호환, 위 `llm.*`)에 발언 말투 다듬기를 한 번 요청합니다. 연결 실패·형식 오류면 그대로 템플릿 발언을 씁니다. 표결 결과는 LLM이 바꾸지 못합니다.

결과는 `땅땅땅! 안건은 부결! (찬성 1 : 반대 3, 기권 1)`처럼 표 수와 함께 발표되고, 결정 기록(`followup`/`history`의 `council` 모드)에도 남습니다.
**부결이고 질문이 구매처럼 보이면**(`살까`, `사야`, `구매` 등) 쿨링오프 명령을 **제안만** 합니다(실행하지 않음):

```
python -m cool_off want '게임' --price <가격> --why '결정 의회 부결'
```

## 점심 운명 로딩 (`lunch`)

```
python -m decision_pet lunch --menu "김치찌개,라면,돈까스,비빔밥,국밥"   # 후보는 저장됨
python -m decision_pet lunch                                              # 저장된 후보로 다시
python -m decision_pet lunch --no-anim                                    # 애니메이션 없이 (테스트용)
```

`██████░░░░░░░░░░░░░░  47%... 위장과 협상 중` 같은 가짜 로딩바가 불규칙하게 오르다 99%에서 한 번 멈칫한 뒤 메뉴를 공개합니다. 최근 3번 고른 메뉴는 피하고(후보가 4개 미만이면 후보가 남는 만큼만 피함), 기록은 `state.json`의 `lunch`에 저장됩니다.

## Windows 시작 프로그램 등록

1. `Win + R` → `shell:startup` 입력 후 엔터
2. 열린 폴더에서 우클릭 → 새로 만들기 → 바로 가기
3. 대상: `pythonw.exe -m decision_pet gui` (경로가 필요하면 `C:\...\pythonw.exe -m decision_pet gui`)
4. 바로 가기 속성의 "시작 위치"를 이 프로젝트 폴더(`decision-pet`)로 설정

`pythonw`는 콘솔 창 없이 실행합니다. 문제가 있으면 먼저 `python -m decision_pet gui`로 오류를 확인하세요.

## 데이터와 설정

저장 위치: `%APPDATA%\decision-pet` (없으면 `~/.decision-pet`). 환경변수 `DECISION_PET_HOME`으로 변경할 수 있습니다.

LLM 주소는 환경변수 `DECISION_PET_LLM_URL` 이 있으면 그것, 없으면 `state.json` 의 `llm.endpoint`, 그것도 없으면 `http://localhost:1234/v1` 을 씁니다.

- `state.json` — 할 일, 결정 기록, 점심 후보/기록, 설정
- `experiments.txt` — 오늘의 실험 목록. `experiment` 명령을 한 번 실행하면 기본 20개로 만들어집니다. 한 줄에 하나, `#` 시작은 주석. 이 파일을 고치면 목록이 바뀝니다.

`state.json`의 `config`:

| 키 | 기본값 | 의미 |
|---|---|---|
| `nudge_minutes` | 30 | 진행 중인 할 일이 있을 때 펫이 슬쩍 말 거는 간격(분) |
| `max_skips` | 5 | 이 횟수만큼 미루면 묘지행 |
| `max_age_days` | 30 | 이 일수를 넘기면 묘지행 |
| `llm.endpoint` | `http://localhost:1234/v1` | OpenAI 호환 서버 (`/chat/completions` 자동 추가) |
| `llm.model` | `local-model` | 모델 이름 |
| `llm.api_key` | `""` | 필요하면 입력 |
| `llm.timeout` | 8 | 초 |
| `sources.*` | `""` | 의회가 읽을 경로: `cool_off`, `steam_buddy`, `life_nagger`, `voice_journal`, `paper_butler` (빈 값=기본 위치) |
| `council.monthly_budget` | 500000 | 가계부 의원 반대 기준(원) |
| `council.game_hours_limit` | 40 | 엄마봇 의원 반대 기준(이번 달 시간) |

GUI 폰트는 Malgun Gothic → Apple SD Gothic Neo → Noto Sans CJK KR → 기본 폰트 순으로 고릅니다.

## 테스트

```
python3 -m unittest discover -s tests -v
```

핵심 로직(`decision_pet/core.py`, `decide.py`, `storage.py`, `experiments.py`)은 GUI와 분리되어 있으며, `gui.py`는 `gui` 명령 실행 시에만 import 됩니다.
