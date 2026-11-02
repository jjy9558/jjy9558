# 결정해주는 데스크톱 펫 (decision-pet)

결정 장애가 있는 Claude Code 헤비 유저를 위한 작은 데스크톱 펫입니다.
화면 구석에 앉아서 **"오늘은 이거 해"** 라고 말해 줍니다. Python 3.9+ 표준 라이브러리(tkinter)만 사용합니다.

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

## Windows 시작 프로그램 등록

1. `Win + R` → `shell:startup` 입력 후 엔터
2. 열린 폴더에서 우클릭 → 새로 만들기 → 바로 가기
3. 대상: `pythonw.exe -m decision_pet gui` (경로가 필요하면 `C:\...\pythonw.exe -m decision_pet gui`)
4. 바로 가기 속성의 "시작 위치"를 이 프로젝트 폴더(`decision-pet`)로 설정

`pythonw`는 콘솔 창 없이 실행합니다. 문제가 있으면 먼저 `python -m decision_pet gui`로 오류를 확인하세요.

## 데이터와 설정

저장 위치: `%APPDATA%\decision-pet` (없으면 `~/.decision-pet`). 환경변수 `DECISION_PET_HOME`으로 변경할 수 있습니다.

- `state.json` — 할 일, 결정 기록, 설정
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

GUI 폰트는 Malgun Gothic → Apple SD Gothic Neo → Noto Sans CJK KR → 기본 폰트 순으로 고릅니다.

## 테스트

```
python3 -m unittest discover -s tests -v
```

핵심 로직(`decision_pet/core.py`, `decide.py`, `storage.py`, `experiments.py`)은 GUI와 분리되어 있으며, `gui.py`는 `gui` 명령 실행 시에만 import 됩니다.
