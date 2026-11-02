# life-nagger — 생활 잔소리 엔진

> "개웃기네 ㅋㅋㅋ" 소리 들었던 그 목록을, 진짜로 돌아가게 만들었습니다.

Windows PC(게임 + 코딩)에서 돌면서 **새벽 게임, 굳은 엉덩이, 쌓인 설거지**를 한국어로 잔소리하는 데몬입니다.
원하면 서랍 속 갤럭시 S21(`s21-sidekick`)이 대신 말해줍니다.

- Python 3.9+ , **표준 라이브러리만** (pip 설치 없음)
- 네트워크는 *내가 켠 출력 채널*(ntfy, sidekick, 로컬 LLM)에만 씁니다. 기본은 전부 꺼짐
- 모든 외부 명령은 **인자 리스트(shell=False)** 로만 호출

## 빠른 시작 (Windows PowerShell)

```powershell
cd life-nagger
copy config.example.json config.json     # 게임/작업 프로그램 이름을 내 걸로 고치세요
python -m nagger run                     # 데몬 (Ctrl+C로 종료)
python -m nagger status                  # 지금 상태
python -m nagger report --month          # 이번 달 출근 도장 리포트
python -m nagger excuse --late 20 --to 팀장
python -m nagger count sigh              # 한숨 +1
```

`config.json`이 현재 폴더에 있으면 자동으로 읽습니다(`--config 경로`로 지정도 가능). 상태 파일은 기본 `~/.life-nagger/`.

## 모듈

### 1. 새벽 게임 엄마봇
설정한 게임 프로세스가 `02:00`~`06:00` 사이에 실행 중이면:

| 시점 | 대사 | 
|---|---|
| 처음 감지 | "지금 몇 시니? (03:12)" |
| +10분 | "엄마 화났다. {game} 당장 끄자." |
| +20분 | "마지막 경고. 진짜로 마지막이다." |

게임을 끄면 즉시 리셋됩니다. 대사/간격/시간대/게임 목록은 전부 `config.json`.

**잠자기(선택, 기본 OFF)**: `"mombot": {"allow_sleep": true}` 를 *직접* 써야만 켜집니다(문자열 `"true"`는 오류).
켜면 마지막 경고 후 5분(저장 시간) 뒤 **60초 카운트다운**(30초/10초 남았을 때 한 번 더 알림)을 하고 PC를 재웁니다.

- 취소: 다른 터미널에서 `python -m nagger cancel-sleep` (또는 게임을 끄면 자동 취소). 카운트다운 중에는 폴링이 5초로 빨라집니다
- 실행 명령: `rundll32.exe powrprof.dll,SetSuspendState 0,1,0` (인자 리스트로 호출). `sleep_method: "hibernate"`면 `shutdown /h`
- 최대한 시도하는 건 "절전"이지만, Windows에서 최대 절전이 켜져 있으면 `SetSuspendState`가 최대 절전으로 동작하는 알려진 특성이 있습니다. 환경에 따라 다르니 **처음엔 `run --dry-run`으로 연습**하세요
- 한 번 재우거나 취소하면, 그 게임이 꺼지기 전에는 다시 재우지 않습니다 (깨우자마자 또 재워지는 루프 방지)
- **게임을 강제 종료하지 않습니다.** 코드에 프로세스를 죽이는 기능 자체가 없습니다

### 2. 엉덩이 경보
입력(키보드/마우스)이 있는 상태로 **2시간** 동안 5분 이상 쉬지 않으면
"엉덩이 굳는다. 일어나서 물 한 잔. (벌써 2시간 10분째)" → **10분마다** 반복, 5분 이상 입력이 없으면 해제("오, 일어났네").
4분짜리 쉬엄쉬엄은 휴식으로 안 칩니다. PC가 절전에 들었다 깨도 휴식으로 칩니다.

### 3. 재택 출근 도장
포그라운드 앱과 입력 여부로 시간을 나눕니다: `work_apps`(에디터/터미널/claude…) → **작업**, `games` → **게임**, 그 외 → 기타. 입력이 3분(`present_idle_seconds`) 없으면 자리 비움으로 아무것도 세지 않습니다.

- 첫 작업 감지 시 "출근 도장 쾅", 30분간 작업이 없으면 "퇴근 도장 쾅. 오늘 순수 작업 N시간"
- 자정을 넘기면 날짜별로 정확히 쪼개 계산
- `python -m nagger report --month [YYYY-MM] [--html r.html] [--md r.md]` → "이번 달 순수 작업 시간 112시간" + 일별 막대 (HTML은 외부 리소스 없는 한 장짜리)

### 4. 지각 변명 생성기
```powershell
python -m nagger excuse --late 20 --to 팀장 [--where remote] [--llm] [--seed 1]
```
정중한 초안 3개 + **정직 옵션**(`늦잠 잤습니다, 죄송합니다.`)이 항상 나옵니다. `--llm`은 `config.json`의 `llm.base_url`(OpenAI 호환, 예: 로컬 llama-server)을 시도하고, 실패/빈 응답이면 조용히 템플릿으로 돌아갑니다.
초안은 초안일 뿐, 보내기 전에 사실인지는 본인이 책임지세요. 솔직한 쪽이 보통 이깁니다.

### 5. 카운터 (한숨 / 푸시업 / 설거지)
```powershell
python -m nagger count sigh            # 한숨 +1
python -m nagger count pushup -n 20
python -m nagger count dishes          # 설거지 했음! 방치 일수 리셋
python -m nagger report --week [--last]   # "이번 주 한숨 147회, 화요일 오후 3시 최고점"
```
설거지 방치 일수는 마지막 `dishes` 기록으로 계산하고, 매일 `dishes.remind_hour`(기본 20시) 이후 한 번 대사가 점점 심해집니다
(1일째 "그릇이 기다려요" → 3일째 "싱크대가 생태계" → 7일 넘으면 "문화재 보존").
기록은 `counters.jsonl`에 한 줄씩 추가되므로 데몬과 CLI가 동시에 써도 안전합니다.

## 출력 채널

| 채널 | 설정 | 비고 |
|---|---|---|
| console | 기본 ON | |
| toast | 기본 ON, `warn` 이상 | PowerShell **BurntToast** 모듈이 있으면 토스트(`Install-Module BurntToast`), 없으면 `msg *`, 그것도 안 되면 눈에 띄는 출력. 텍스트는 환경변수로 전달해 PowerShell 인젝션 없음 |
| ntfy | `outputs.ntfy.topic` | 토픽 이름이 곧 비밀번호니 길게. 기본 OFF |
| sidekick | `outputs.sidekick` | 아래 참고. 기본 OFF |

각 채널에 `min_level`(`info`/`warn`/`urgent`)을 줄 수 있습니다.

### s21-sidekick 연동 (수정 없이 읽기만 했습니다)

sidekick의 API(`X-Sidekick-Token` 헤더 필요)를 읽어 본 결과, 외부 프로그램이 폰에 말을 시키는 엔드포인트가 **아직 없습니다** (`events.say()`는 서버 내부 모듈 전용).
그래서 nagger는 아래 엔드포인트를 호출하도록 만들어 두었고, **sidekick 쪽에 없으면 404를 받고 그 채널을 스스로 꺼서** 다른 채널엔 영향이 없습니다.

> **TODO (s21-sidekick, 이 프로젝트에서는 수정하지 않음)**
> `POST /api/say` — 바디 `{"text": "...", "module": "nagger-mombot"}` → `self.app.events.say(text, module=module)` 후 `{"ok": true}`.
> (`r_arrival`이 `events.say(...)`를 부르는 모양 그대로.)

토큰은 환경변수 `SIDEKICK_TOKEN` 또는 `outputs.sidekick.token`.

**한숨을 폰에서 세기**: nagger는 선택적으로 로컬 수신기를 엽니다 (`events.enabled: true` + `events.token` 필수, 토큰 없으면 시작 거부, 기본 `127.0.0.1:8799`).

```
POST http://127.0.0.1:8799/api/event     X-Sidekick-Token: <events.token>
{"type": "sigh"}            (선택: "n": 3)       type은 sigh | pushup | dishes
```
sidekick(또는 폰 페이지)이 한숨을 감지했을 때 위 주소로 보내면 됩니다. sidekick 쪽의 전달 코드는 TODO입니다.

## 설정 팁
- `games`, `work_apps`: 프로세스 이름(`.exe` 유무/대소문자 무관). 작업관리자 → 세부 정보에서 확인
- 전체 기본값은 `nagger/config.py`의 `DEFAULTS`
- 잘못된 설정(시간 형식, `allow_sleep`이 불리언이 아님 등)은 시작할 때 바로 알려줍니다
- 데몬을 재시작하면 엄마봇/엉덩이 경보의 진행 상태는 처음부터입니다 (근무 시간·카운터는 파일에 남아 유지)

## 안전/프라이버시 메모
- **자동 재우기는 기본 OFF**, 켜도 60초 카운트다운 + 취소 가능, 게임 강제 종료 없음
- 저장하는 것: 날짜별 작업/게임 초, 출퇴근 시각, 카운터 시각. **창 제목·키 입력 내용·화면은 보지도 저장하지도 않습니다** (포그라운드 프로세스 *이름*과 마지막 입력 후 경과 시간만 사용)
- 상태 파일에 토큰을 쓰지 않습니다. `config.json`엔 ntfy 토픽/토큰이 들어갈 수 있으니 커밋하지 마세요(.gitignore 포함)
- 이벤트 수신기는 토큰 필수 + 크기 제한 + 기본 localhost 바인딩

## 구조

```
nagger/platform.py   Windows 전용 호출(ctypes, tasklist, 절전, 토스트) — 유일한 OS 의존 지점
nagger/fakes.py      테스트용 가짜 시계/플랫폼
nagger/rules.py      엄마봇, 엉덩이 경보, 설거지 잔소리
nagger/worktime.py   출근 도장 (작업 시간 누적)
nagger/counters.py   카운터 + 주간 리포트
nagger/report.py     월간 리포트 (Markdown/HTML)
nagger/excuse.py     지각 변명
nagger/outputs.py    console/toast/ntfy/sidekick
nagger/engine.py     틱 루프, cli.py 명령줄
```

## 테스트

```powershell
python -m unittest discover -s tests -v
```
가짜 시계 + 가짜 플랫폼으로 엄마봇 단계/리셋/잠자기 안전장치, 엉덩이 타이밍, 자정 넘는 근무시간, 리포트, 변명 폴백, 카운터·주간 최고점 등을 검증합니다. 네트워크 없이 돌고 Windows가 아니어도 됩니다.
**Windows 실제 API(ctypes 유휴 시간/포그라운드 창, tasklist, rundll32, BurntToast)는 자동 테스트하지 못했으니**, 처음엔 `python -m nagger run --dry-run`으로 확인하세요.
