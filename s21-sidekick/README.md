# s21-sidekick — Claude에게 몸을 붙여준다

액정 깨진 채 서랍에 있던 갤럭시 S21을 **방 안에 있는 Claude의 눈·귀·입**으로 씁니다.

```
S21이 듣고 보면  ──▶  PC가 판단  ──▶  S21이 말하거나 / PC가 행동
 (마이크·카메라)       (트리거·LLM·VLM)     (TTS)          (미리 정한 명령)
```

뼈대는 하나이고, 그 위에 모듈 일곱 개가 꽂혀 있습니다.

| 모듈 | 폰이 하는 일 | PC가 하는 일 |
|---|---|---|
| 혼잣말 디버거 | "아 이거 왜 안 돼"를 듣는다 → 요약을 읽어준다 | 최근 에러 로그를 보거나 `claude -p`를 돌린다 |
| 침대 탈출 알람 | 시끄럽게 울린다 → 칫솔/세면대 사진을 찍는다 | 비전 모델로 판정: "반려. 아직 누워 있음." (승인되면 선택적으로 아침 브리핑 낭독) |
| 귀가 집사 | 현관 움직임(또는 버튼)을 감지 → 요약을 읽어준다 | 일정·Claude 상태 등 provider 결과를 모은다 |
| 현관 출발 체크 | "나 간다" 버튼/모션 → 체크리스트를 읽어준다 | 날씨·첫 일정·챙길 것·GPU 상태 (선택: 물건 기억·문 앞 노크·반품 마감) provider를 모은다 |
| 커밋 관중석 | 환호·야유·불꽃놀이 (Web Audio 합성) + 큰 이모지 | `git push`/CI/PR 머지 결과를 `notify`로 알린다 |
| 매크로 패드 | 큰 버튼 격자(또는 NFC 태그)를 누른다 | config에 미리 적어 둔 프로그램 실행·URL 열기·키 입력·텍스트 입력만 한다 |
| rm -rf 결재 보고 | "사장님, ... 해도 되겠습니까?" → 승인/반려 (버튼·음성) | Claude Code 훅이 위험 명령을 결재가 날 때까지 붙잡는다 |

- PC: Python 3.9+, **표준 라이브러리만** (PC 음성 인식을 원할 때만 `faster-whisper` 선택 설치)
- 폰: Chrome에서 여는 HTML 한 장 (빌드 없음). 음성 인식은 기본적으로 **폰 브라우저의 Web Speech API(ko-KR)**, 말하기는 `speechSynthesis`

## 1. PC 설정

```powershell
cd s21-sidekick
copy config.example.json config.json   # 그리고 아래 항목을 채운다
python -m sidekick serve --config config.json
```

`config.json` 주요 항목 (생략한 값은 기본값 사용, 전체 기본값은 `sidekick/config.py`의 `DEFAULTS`):

| 키 | 설명 |
|---|---|
| `server.host` / `port` | 기본 `127.0.0.1:8765`. `tailscale serve`로 감싸면 localhost 그대로 두면 됩니다 (권장). LAN/Tailscale IP에 직접 열려면 `0.0.0.0` |
| `server.token` | **필수.** 모든 `/api/*` 호출에 필요한 공유 토큰. `python -c "import secrets;print(secrets.token_urlsafe(24))"` |
| `llm.base_url` | 디버거 로그 요약용 OpenAI 호환 엔드포인트 (예: llama-server `http://127.0.0.1:8080/v1`). 비우면 요약 없이 마지막 에러 줄을 읽음 |
| `vision.base_url` | 알람 사진 판정용 VLM 엔드포인트 (로컬 VLM을 띄운 llama-server, 또는 호환 API). **비우면 수동 확인 모드** |
| `debugger.triggers` | 트리거 문구. 띄어쓰기·문장부호·돼/되·에러/오류 차이는 자동으로 흡수 |
| `debugger.cooldown_seconds` | 연속 혼잣말 스팸 방지 (기본 60초) |
| `debugger.mode` | `log_tail`(로그 파일 끝부분에서 에러 줄 수집) 또는 `command`(미리 정한 명령 실행) |
| `debugger.command` / `cwd` | 예: `["claude", "-p", "{prompt}"]`, 프로젝트 폴더. `{prompt}`/`{utterance}` 자리에 **데이터로만** 들어감 |
| `alarm.time` / `snooze_minutes` / `max_snoozes` | 알람 시각, 스누즈 길이·횟수 제한 |
| `alarm.emergency_code` | **필수.** 비상 해제 코드. 절대 갇히지 않기 위한 장치 |
| `alarm.max_ring_minutes` | 이 시간이 지나면 알람은 스스로 멈춤 (기본 60분) |
| `alarm.fallback_camera_seconds` | 수동 모드에서 카메라를 켜고 버텨야 하는 시간 |
| `alarm.morning_gate` | (선택, 기본 꺼짐) 알람 승인 시 `morning-brief`의 `brief-latest.json` 낭독 — 침대 탈출 알람 절 참고 |
| `butler.providers` | 귀가 요약에 들어갈 provider 목록 (아래 참고) |
| `departure.providers` | 출발 체크리스트 provider 목록 (날씨·일정·챙길 것·GPU, 선택: 물건 기억/문 앞 노크/반품 마감 — 아래 참고) |
| `stadium.max_per_minute` / `min_interval_seconds` / `big_pr_lines` | 관중석 레이트 리밋, "큰 PR" 기준 줄 수 |
| `approval.*` | 결재 보고 훅 설정 (시간 제한, 시간 초과/서버 없음 처리, 위험 패턴) — 아래 참고 |
| `say.*` | 외부 프로그램용 `POST /api/say` (분당 횟수, 최대 글자 수) |
| `forward_events` | (선택, 기본 빈 목록) 폰 버튼 이벤트를 보낼 로컬 주소 |
| `stt` | PC 음성 인식(faster-whisper) 설정. RTX 5080이면 `device: cuda`, `compute_type: float16` |

## 2. S21에서 열기 (Tailscale HTTPS 권장)

카메라·마이크(`getUserMedia`)와 Web Speech는 **HTTPS 또는 localhost**에서만 동작합니다. `http://100.x.x.x:8765`로 그냥 열면 막힙니다.

**방법 A — `tailscale serve` (권장)**: PC에서

```powershell
tailscale serve --bg 8765
```

그러면 `https://<pc이름>.<tailnet>.ts.net/` 이 tailnet 안에서만 HTTPS로 열립니다 (인증서 자동). 서버는 `127.0.0.1`에 두면 됩니다. (tailnet 관리 화면에서 MagicDNS·HTTPS 인증서가 켜져 있어야 함)

**방법 B — Chrome 플래그 (임시/테스트용)**: S21 Chrome에서 `chrome://flags/#unsafely-treat-insecure-origin-as-secure` 에 `http://100.x.x.x:8765` 를 넣고 재시작. 이 경우 서버 `host`를 `0.0.0.0` 또는 Tailscale IP로.

S21 Chrome에서 `https://<pc>.ts.net/#token=<토큰>` 으로 한 번 열면 토큰은 폰의 localStorage에 저장되고 주소창에서는 지워집니다. 홈 화면에 추가해 두면 편합니다.

열고 나서 상단 **시작**을 누르세요: 화면 켜짐 유지(Wake Lock), 소리 재생 권한, PC 이벤트 수신이 시작됩니다. 충전기에 꽂아 세워 두는 걸 추천합니다.

## 3. 모듈

### 혼잣말 디버거
1. 디버거 탭 → **듣기 시작**. 폰 Chrome이 연속으로 한국어 음성 인식을 하고, 확정된 문장만 PC로 보냅니다 (`POST /api/utterance`).
2. PC는 트리거 문구와 비교합니다: "아 이거 왜 안 돼", "왜 안되지", "에러 뭐야", "오류 뭐야", "또 터졌네" …
3. 걸리면 폰이 "잠깐, 볼게요."라고 하고, PC가 조사합니다.
   - `log_tail`: `log_file` 끝 `tail_lines`줄에서 `error_pattern`에 맞는 줄을 모아 LLM으로 2문장 요약 (LLM 없으면 마지막 에러 줄)
   - `command`: `["claude", "-p", "{prompt}"]` 처럼 **미리 정한 명령만** `cwd`에서 실행, 출력의 앞부분을 읽어줌
4. 쿨다운 동안은 다시 걸리지 않습니다.

PC에서 듣고 싶다면 `pip install faster-whisper` 후 "PC 음성 인식"을 고르면 5초 단위로 녹음해 `/api/transcribe`로 보냅니다 (오디오는 임시 파일로만 쓰고 바로 지움).

> Windows 참고: `claude`가 `claude.cmd`로 잡히면 cmd.exe가 인자를 다시 해석하는 문제가 있어서(BatBadBut), 이 경우 발화에서 `" % ! ^ & | < > ( )` 등을 자동으로 지웁니다. 가능하면 `["node", "C:/.../claude-code/cli.js", "-p", "{prompt}"]`처럼 실행 파일을 직접 지정하세요.

### 침대 탈출 알람
- 알람 탭에서 시각을 정하고, 밤에 **시작**을 눌러 둡니다. 폰은 5초마다 PC에 상태를 묻고, 울릴 시간이 되면 WebAudio로 크게 울리며 진동하고 전면 카메라를 켭니다.
- **비전 모드** (`vision.base_url` 있음): "사진 보내서 판정받기" → PC가 OpenAI 호환 chat completions에 `image_url`(data URI)로 사진을 보내고 `{"approved": ..., "reason": ...}`를 받습니다. 애매한 답은 **반려**로 처리. 결과를 폰이 말합니다: "반려. 아직 누워 있음." / "승인. 좋은 아침이에요!"
- **수동 모드** (VLM 없음): 카메라를 `fallback_camera_seconds`초 동안 켜 둬야(3초마다 프레임 전송, 서버가 시간 확인) 산수 문제가 나오고, 맞혀야 꺼집니다.
- 안전장치
  - 스누즈는 `max_snoozes`번까지
  - **비상 해제 코드**는 언제나 동작 (5번 틀리면 60초만 잠김)
  - VLM이 죽어 있으면 그렇게 알려주고 비상 코드를 안내
  - `max_ring_minutes`가 지나면 저절로 멈춤
  - 시각을 이미 지난 시간으로 바꾸거나 서버를 늦게 켜도 바로 울리지 않음
  - 결국 웹페이지이므로 탭을 닫으면 꺼집니다. 이건 의지력 보조 도구지 감옥이 아닙니다.

#### 아침 브리핑 연동 (`alarm.morning_gate`, 선택, 기본 꺼짐)
알람이 **승인되면**(사진 판정 통과, 또는 수동 모드에서 산수 정답) 같은 응답의 `say` 뒤에 `morning-brief`가 만들어 둔 `out/brief-latest.json`의 `speak_text`를 이어 붙여 폰이 읽어 줍니다: "승인. 좋은 아침이에요! 오늘 할 일은 …". 일어나야만 들을 수 있는 브리핑입니다.

```json
"alarm": { "morning_gate": {
  "enabled": true,
  "brief_path": "C:/dev/morning-brief/out/brief-latest.json",
  "stale_text": "오늘 브리핑이 아직 없어요.",
  "speak_reject": false, "reject_text": "브리핑은 일어나면 해 드립니다.", "max_chars": 300 } }
```

- **파일 계약**은 `morning-brief/mbrief/contract.py`에 있습니다. 여기서 쓰는 키는 `date` / `speak_text` / `reject_text` 뿐이고 모르는 키는 무시합니다. sidekick은 그 파일을 **읽기만** 합니다(쓰기·삭제·이름 변경 없음).
- `date`가 **오늘이 아니면 오래된 파일**로 보고 `speak_text`를 읽지 않고 `stale_text` 한 줄만 말합니다. 파일이 없거나, JSON이 깨졌거나, `speak_text`가 비어도 같습니다(알람 승인 자체는 영향 없음).
- **반려**되면 `speak_text`는 절대 읽지 않습니다. `speak_reject: true`일 때만 파일의 `reject_text`(없으면 설정의 `reject_text`) 한 줄을 반려 멘트 뒤에 덧붙입니다.
- 비상 코드로 끈 경우에는 브리핑을 읽지 않습니다. 총 길이는 `morning_gate.max_chars`(기본 300)로 자릅니다.
- 승인 응답에는 `brief` 필드(`ok`/`stale`/`missing`/`invalid`)가 함께 옵니다.

### 귀가 집사
- 집사 탭에서 **현관 모션 감지 켜기**: 후면 카메라 프레임을 32px로 줄여 이전 프레임과 비교, 변화가 `min_changed_ratio`를 넘으면 `/api/arrival`. (모션은 `butler.cooldown_seconds` 쿨다운, **나 왔어** 버튼은 항상 동작)
- PC는 provider들의 결과를 이어 붙여 폰에 말하게 합니다.

| provider | 상태 |
|---|---|
| `greeting` | 시간대별 인사 (`text`로 고정 가능) |
| `calendar_ics` | `path`의 .ics에서 오늘 일정 (단순 파서, 반복 일정 미지원). 구글 캘린더의 "비공개 iCal 주소"를 주기적으로 받아 두면 됨 |
| `claude_status` | 자리표시자: `path` 파일의 첫 줄을 읽음 (Claude Code 훅이 상태를 써 두는 용도) |
| `weather`, `delivery` | 자리표시자: 미구현. `text`가 있으면 그대로, 없으면 아무 말 안 함 |

새 provider는 `sidekick/butler.py`에 `def my_provider(opts, now) -> str | None` 을 만들고 `PROVIDERS`에 등록하면 끝입니다.

### 현관 출발 체크
- **출발** 탭 → **나 간다** 버튼, 또는 **출발 모션 감지 켜기** (귀가 집사와 같은 모션 감지, 카메라는 한 번에 하나만 켜짐). 모션은 `departure.cooldown_seconds` 쿨다운.
- PC는 `departure.providers`를 차례로 돌려 `intro` + 항목들 + `outro`("다녀오세요!")를 말하게 하고, 폰 화면에 항목 목록을 보여줍니다. 귀가 집사와 **같은 provider 구조**라 서로 바꿔 끼울 수 있고, 어떤 provider든 `"enabled": false`로 끌 수 있습니다.

| provider | 내용 |
|---|---|
| `open_meteo` | Open-Meteo 날씨 (API 키 불필요). **기본 꺼짐** — 네트워크가 필요하므로 `enabled: true`와 `latitude`/`longitude`를 넣어야 동작. 강수확률이 `umbrella_threshold`(기본 50%) 이상이면 "우산 챙기세요". 실패하면 조용히 생략 |
| `calendar_next` | 같은 .ics 파서로 **오늘 아직 시작 안 한 첫 일정** + 종일 일정 (`grace_minutes`분 전까지는 아직 '다가오는' 것으로 봄) |
| `reminders` | 고정 항목: `["지갑", "우산", "에어컨 껐는지"]`. 요일 한정은 `{"text": "분리수거", "weekdays": [0, 3]}` (0=월) |
| `gpu_busy` | (선택, 기본 꺼짐) `nvidia-smi --query-gpu=name,utilization.gpu ...`를 **인자 리스트**로 실행해 사용률이 `threshold`% 이상이면 "PC가 아직 일하는 중이에요". `nvidia_smi`로 실행 파일 경로 지정 가능. 없으면 생략 |

#### 물건 기억·문 앞·반품 provider (선택, 전부 기본 꺼짐)
다른 프로젝트의 데이터를 **읽기만** 하는 provider 세 개입니다. 각각 `enabled: true`로 켜야 하고, 실패(서버 꺼짐·파일 없음·토큰 오류 등)하면 **로그 한 줄만 남기고 조용히 건너뛰어** 나머지 항목은 그대로 읽습니다. 모든 요청에는 타임아웃이 있고 로그에는 토큰·물건/가게 이름을 남기지 않습니다.

| provider | 내용 |
|---|---|
| `objmem_items` | `object-memory`(기본 `http://127.0.0.1:8788`)의 `GET /api/ask`에 `items`(예: 지갑·이어폰·열쇠)마다 "지갑 어디 있어"를 물어 **마지막 목격 시각·위치**만 말합니다: "물건 기억 - 지갑: 오늘 21:40 책상 위에서 봤어요." 토큰은 `token_env`(기본 `OBJMEM_TOKEN`) 환경변수 또는 `token`, 헤더 `X-ObjMem-Token`. 기록이 `stale_hours`(기본 24)보다 오래됐거나 없거나 일치도가 낮으면 위치를 말하지 않고 **"모름"**이라고 합니다(거짓 안심 방지). 서버에 못 붙으면 "모름"이라고 하지 않고 통째로 생략합니다. `timeout` 기본 3초 |
| `soundwatch_door` | `sound-watch`의 sqlite(`db_path`, 보통 `<데이터 폴더>/soundwatch.db`)를 `mode=ro`로 열어 최근 `hours`(기본 6)시간의 `detections`에서 `kind`가 `kinds`(기본 `["knock"]`)이거나 `name`에 `names`(기본 `초인종`, `doorbell`)가 들어간 감지를 셉니다: "최근 6시간 안에 노크·초인종 소리가 2번 있었어요 (마지막 14:05). 문 앞 택배 있을 수도 있어요." 없으면 아무 말 안 함. ("집 비운 동안"이 아니라 최근 N시간 기준입니다) |
| `paperbutler_returns` | `paper-butler`의 sqlite(`db_path`, 보통 `<홈>/paperbutler.db`)를 `mode=ro`로 열어 `refunds`에서 처리 안 한(`done=0`) 마감이 오늘부터 `days`(기본 2)일 이내인 것을 말합니다: "반품 마감: 쿠팡 오늘, 무신사 내일." 금액은 말하지 않고 `max_items`(기본 3)개까지, 나머지는 "외 N건" |

```json
{"name": "objmem_items", "enabled": true, "items": ["지갑", "이어폰", "열쇠"], "token_env": "OBJMEM_TOKEN"},
{"name": "soundwatch_door", "enabled": true, "db_path": "C:/dev/sound-watch/data/soundwatch.db", "hours": 6},
{"name": "paperbutler_returns", "enabled": true, "db_path": "C:/Users/me/.paperbutler/paperbutler.db", "days": 2}
```

- **글자 수 한도**: 출발 체크 전체 문장이 `say.max_chars`(기본 300)를 넘으면 **`departure.providers`에서 뒤에 적은 항목부터 통째로 뺍니다**(문장 중간을 자르지 않음; `intro`/`outro`는 유지). 그래서 중요한 provider를 앞에 쓰세요. 응답에는 뺀 개수 `dropped`가 들어갑니다.
- 같은 provider를 귀가 집사(`butler.providers`)에 꽂아도 됩니다(같은 구조).

### 커밋 관중석
PC에서 이렇게 부르면 폰이 반응합니다 (토큰은 config에서 읽음):

```bash
python -m sidekick notify push_ok --detail main      # 환호 🎉
python -m sidekick notify push_fail                  # 야유 👎
python -m sidekick notify ci_fail --detail "unit"    # 야유 🍅
python -m sidekick notify ci_ok                      # 환호 ✅
python -m sidekick notify pr_merged --detail "PR 12" --lines 840   # 불꽃놀이 🎆 (big_pr_lines 미만이면 박수 👏)
```

- 소리는 폰에서 **Web Audio로 합성**합니다 (필터 건 노이즈 + 수십 개의 발진기로 만든 웅성임·박수·"우우우"·쉬익-펑). 녹음 파일이나 저작권 있는 음원은 없습니다. 소리 + 진동 + 화면 가득 이모지 애니메이션 + 한 줄 대사.
- **레이트 리밋**: 분당 `max_per_minute`회(기본 4), 최소 `min_interval_seconds`초(기본 3) 간격. 넘치면 서버는 429, CLI는 "건너뜀"을 출력하고 **종료 코드 0** (git 흐름을 깨지 않게). 서버가 없거나 토큰이 틀리면 1, 설정 오류는 2.
- 설정 파일은 `--config` > `$SIDEKICK_CONFIG` > **이 저장소 루트의 `config.json`** 순서로 찾습니다 (git 명령은 다른 저장소에서 돌기 때문). `$SIDEKICK_URL`, `$SIDEKICK_TOKEN` 또는 `--url`, `--token`으로 덮어쓸 수 있습니다.
- **git과 연결**: pre-push 훅은 push가 *성공했는지* 모릅니다. 그래서 `git push`를 감싸는 함수를 씁니다.
  - PowerShell: `$PROFILE`에 `. C:\dev\s21-sidekick\examples\git\sidekick-git.ps1` → `gpush`, `gci`(gh로 최근 Actions 결과 대기), `gmerge 12 --squash`
  - bash/zsh: `source .../examples/git/sidekick-git.sh` → 같은 함수들 (`SIDEKICK_DIR` 지정)
  - 가장 단순하게는 `git push && python -m sidekick notify push_ok || python -m sidekick notify push_fail`
- 관중석 탭의 테스트 버튼으로 소리를 미리 들어볼 수 있습니다 (레이트 리밋 적용).

### 매크로 패드 (`macro`)

폰을 PC용 단축키 패드로 씁니다. 폰 페이지의 **매크로** 탭에 큰 버튼 격자(라벨·이모지·색)가 뜨고, 누르면 `POST /api/macro {"id": "..."}`. **폰은 id만 보내고**, 실행할 프로그램·URL·키·글자는 전부 `config.json`의 `macro.items`에 있습니다 (폰이 보낸 글자는 절대 실행되지 않음). `config.example.json`에 예시가 있습니다.

```json
"macro": {
  "max_per_minute": 20, "min_interval_seconds": 0.5, "max_chain": 8,
  "audit_keep": 200, "audit_file": "",
  "items": [
    {"id": "vscode", "label": "VS Code", "emoji": "💻", "color": "#5aa9ff", "type": "launch",
     "program": "C:/.../Code.exe", "args": ["C:/dev/myapp"]},
    {"id": "playpause", "label": "재생/정지", "emoji": "⏯️", "type": "keys", "keys": "play/pause"},
    {"id": "lock", "label": "화면 잠금", "type": "keys", "keys": "win+l", "confirm": true}
  ]
}
```

공통 필드: `id`(영문·숫자·`_-`, 중복 불가), `label`, `emoji`, `color`(`#rgb`/`#rrggbb`), `confirm`, `nfc_key`, `hidden`(격자에서 숨김, NFC 전용).

| `type` | 필드 | 동작 |
|---|---|---|
| `launch` | `program`, `args`(문자열 목록), `cwd`? | 인자 목록 그대로 `subprocess.Popen(shell=False)`. 기다리지 않음 |
| `url` | `url` (`http`/`https`/`steam`/`vscode`/`spotify`만) | 기본 브라우저/앱으로 열기 (`webbrowser`) |
| `keys` | `keys` 예: `ctrl+shift+esc`, `win+d`, `alt+f4`, `play/pause`, `next`, `prev`, `volumeup`, `volumedown`, `mute` | 앞에 있는 창에 `SendInput`. 수정 키 `ctrl`/`alt`/`shift`/`win` + 키 1개(영문·숫자·`F1`~`F24`·방향키 등) |
| `text` | `text` (최대 500자, 줄바꿈=Enter) | 유니코드(`KEYEVENTF_UNICODE`)로 타이핑. 한글·이모지 가능 |
| `sidekick` | `action`: `departure`/`arrival`/`stadium`(+`event`) | 기존 모듈 재사용 (출발 체크·귀가 인사·관중석 연출) |
| `chain` | `steps`: `["id", {"id": "다른id", "delay": 2}]` | 순서대로 실행. 한 단계 지연 최대 10초(합계 30초), 최대 `max_chain`단계. **체인 안에 체인·자기 자신은 금지**(시작 시 설정 검사에서 오류). 한 단계가 실패하면 거기서 멈춤 |

- `keys`/`text`는 **Windows에서만** 동작합니다(`sidekick/winkeys.py`, ctypes). 다른 OS에서는 "Windows에서만 동작합니다" 오류를 돌려줍니다. 관리자 권한으로 뜬 창에는 일반 권한 서버가 키를 못 보낼 수 있습니다.
- 설정이 틀리면(알 수 없는 type, 필수 필드 없음, 없는 id를 부르는 체인, 키 이름 오류 등) 서버가 시작 시 오류 목록을 내고 멈춥니다.
- **확인 창**: `"confirm": true`인 매크로는 폰에서 확인 대화상자를 거쳐야 실행됩니다. 서버도 `confirmed: true` 없이는 실행하지 않습니다 (그래도 이건 *실수 방지*일 뿐 보안 장치가 아닙니다 — 토큰을 가진 사람은 `confirmed`를 직접 보낼 수 있음).
- **레이트 리밋**: `max_per_minute`/`min_interval_seconds` 초과 시 429 (`retry_after`).
- **감사 로그**: 시각·매크로 id·출처(`tab`/`nfc`/`chain:<id>`)·결과(`ok`/`error`/`needs_confirm`/`rate_limited`/`denied`/`unknown`)를 기록. 메모리에 최근 `audit_keep`개, `audit_file`을 지정하면 JSON Lines로도 덧붙입니다. 폰 매크로 탭의 "최근 실행 기록" 또는 `GET /api/macro/audit`(메인 토큰). 토큰·nfc_key·텍스트 내용은 기록하지 않습니다.

#### NFC 태그로 실행하기

태그 주소: `https://<pc이름>.<tailnet>.ts.net/#macro=<id>&nfc=<nfc_key>`

**경고: 태그에 메인 토큰(`#token=...`)을 쓰지 마세요.** NFC 태그는 근처의 누구나(폰만 있으면) 읽을 수 있고, 메인 토큰은 PC의 모든 API를 엽니다. 대신 매크로마다 따로 만든 `nfc_key`를 씁니다.

1. `python -m sidekick macro-key` 로 무작위 키를 만든다 (20자 이상 필수, 매크로마다 다르게, `server.token`과도 달라야 함).
2. `config.json`의 해당 매크로에 `"nfc_key": "<키>"`를 넣고 서버를 다시 시작.
3. 폰의 NFC 쓰기 앱(NFC Tools 등)으로 위 주소를 URL 레코드로 기록 (필요하면 태그를 읽기 전용으로 잠금).
4. 태그에 폰을 대면 페이지가 열리고, 해시를 읽어 `POST /api/macro`를 `X-Sidekick-Token: <nfc_key>`로 호출한 뒤 결과를 보여 줍니다. 주소창의 키는 즉시 지워지고 폰에 저장하지 않습니다.

`nfc_key`는 **그 매크로 하나에만** 유효합니다: 다른 매크로 id, 다른 `/api/*` 경로에는 401. 그래도 키가 읽히면 누구든 그 한 동작은 실행할 수 있으니 **태그에는 재생/정지·볼륨처럼 무해한 동작만** 연결하세요 (위험한 건 `confirm: true`로 두면 폰에서 확인을 거침). 취소(revoke)는 `nfc_key`를 지우거나 새 값으로 바꾸고 서버를 재시작 — 옛 태그는 즉시 401이 되고 감사 로그에 `denied`로 남습니다. 임시로 막으려면 `"nfc_enabled": false`.

### rm -rf 결재 보고
Claude Code가 위험한 명령을 실행하려 하면 폰이 묻습니다: **"사장님, 강제 재귀 삭제: rm -rf ./build 해도 되겠습니까?"** 화면에는 큰 **승인 / 반려** 버튼과 명령 전체가 뜨고, 말로도 답할 수 있습니다 ("ㅇㅇ / 응 / 해 / 승인" ↔ "안 돼 / 아니 / 반려"; 반려 단어가 섞이면 반려 우선, 못 알아들으면 다시 물음 — 자동 승인은 없음).

Claude Code `settings.json` (사용자 `~/.claude/settings.json` 또는 프로젝트 `.claude/settings.json`; 예시는 `examples/claude/settings.json`):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|PowerShell",
        "hooks": [
          { "type": "command",
            "command": "python C:/dev/s21-sidekick/hooks/claude_pretooluse.py",
            "timeout": 60 }
        ]
      }
    ]
  }
}
```

(`pip install -e .` 했다면 `"command": "python -m sidekick hook"`도 됩니다. 설정 파일을 따로 두려면 `--config C:/.../config.json` 또는 `SIDEKICK_CONFIG`.)

동작:
1. 훅은 stdin의 JSON에서 `tool_name`과 `tool_input.command`를 읽습니다. `approval.tools`(기본 `Bash`, `PowerShell`)가 아니거나 위험 패턴에 안 걸리면 **아무것도 출력하지 않고 바로 종료 코드 0** — 평소대로 진행되고 서버에도 안 붙습니다.
2. 걸리면 서버에 결재를 올리고(`/api/approval/request`) 답을 롱폴링으로 기다립니다(`/api/approval/wait`).
3. 결과 출력
   - **승인**: stdout에 `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "permissionDecisionReason": "..."}}`, 종료 코드 0. Claude Code 자체 확인 창도 건너뜁니다. 그것까지 보고 싶으면 `allow_style: "passthrough"` (출력 없이 0 → 원래 권한 흐름).
   - **반려 / 시간 초과 / 서버 없음**: 기본 `deny_style: "exit2"` = stderr에 이유 + **종료 코드 2**. 훅 JSON 형식이 버전마다 달라도 확실히 막히는 방식입니다 (이유는 Claude에게 전달됨). `deny_style: "json"`이면 `permissionDecision: "deny"` JSON + 종료 코드 0.
   - `on_timeout: "ask"` 또는 `fail_open: true`일 때는 `permissionDecision: "ask"` JSON → Claude Code의 원래 확인 창으로 넘깁니다. **시간 초과·서버 없음에서 자동 승인하는 옵션은 일부러 없습니다.**
4. 실패 시 닫힘: 위험 명령으로 판정된 뒤에는 서버 연결 실패, 토큰 오류, 훅 내부 예외까지 전부 **막음**(종료 코드 2). 훅 입력 JSON이 깨졌으면 원문 전체를 패턴 검사해서 위험해 보이면 막습니다.
5. `timeout_seconds` 기본 45초. **Claude Code의 훅 제한 시간(기본 60초)보다 짧아야 합니다** — 제한에 걸려 훅이 죽으면 차단으로 처리되지 않을 수 있으니, 늘릴 때는 settings.json의 `timeout`도 같이 늘리세요.

기본 위험 패턴 (`sidekick/hook.py`의 `DEFAULT_PATTERNS`; `approval.risky_patterns`로 교체, `extra_patterns`로 추가 — `"정규식"` 또는 `{"pattern": ..., "label": ...}`):

| 라벨 | 예 |
|---|---|
| 강제 재귀 삭제 | `rm -rf`, `rm -fr`, `rm -r -f`, `rm --recursive`, `sudo /bin/rm -Rf` (`rm -f 파일 하나`는 통과) |
| git 강제 푸시 | `git push --force`, `-f`, `--force-with-lease`, `+main` |
| git reset --hard / git clean | `git reset --hard`, `git clean -fdx` |
| DB 테이블 삭제 | `DROP TABLE/DATABASE/SCHEMA`, `TRUNCATE TABLE` (대소문자 무시) |
| 윈도우 강제 삭제 | `del /s`, `rd /s`, `rmdir /S /Q` |
| PowerShell 재귀 삭제 | `Remove-Item -Recurse`, `ri x -r` |
| 기타 | `find ... -delete`, `shutil.rmtree`, `mkfs`, `format D:`, `dd of=/dev/...` |

**매칭 방식에 대한 선택**: 셸 문법을 해석하지 않고 **원문 텍스트**에 정규식을 겁니다(`;`, `&&`, `|`, 줄바꿈으로 구간을 나누고, `\`+줄바꿈 이어쓰기는 합침). 그래서 `rm -rf ./build`는 물론 `echo "rm -rf"`도 걸립니다. 따옴표 안을 빼 주면 `bash -c "rm -rf x"`나 `eval "..."`처럼 실제로 실행되는 경우를 놓치기 때문에, **오탐(한 번 더 물어봄)을 미탐(그냥 지워짐)보다 택했습니다.** `git rm -r --cached`도 걸립니다. 반대로 `$RM -rf`, 스크립트 파일 안의 삭제, 패턴에 없는 도구는 못 잡습니다. 이건 보조 안전장치지 샌드박스가 아닙니다.

폰은 **승인/반려 표시만** 할 수 있습니다. 폰에서 온 어떤 글자도 실행되지 않고, 실행 여부는 Claude Code가 결정합니다. 첫 결정만 유효하며, 대기 중인 결재는 최대 `max_pending`개(기본 10).

### 다른 프로그램과 연결 (`/api/say`, 이벤트 전달)
- **폰에 말 시키기**: 다른 로컬 프로젝트(예: `life-nagger`)가

  ```
  POST /api/say   X-Sidekick-Token: <토큰>   (또는 Authorization: Bearer <토큰>)
  {"text": "물 마실 시간이에요.", "module": "nagger-mombot"}
  ```

  를 보내면 내부 `events.say(...)`와 똑같은 "say" 이벤트가 쌓이고 폰이 읽습니다. 응답 `{"ok": true, "id": <이벤트 번호>, "text": <정리된 문장>}`. 제어 문자·양방향 제어 문자는 공백으로 바꾸고, `say.max_chars`(기본 300자)에서 자르며, `module`은 영문/숫자/`_.-`만 남깁니다(기본 `external`). 분당 `say.max_per_minute`회(기본 12), 최소 1초 간격, 넘치면 429. `say.enabled: false`면 403.
- **폰 버튼 → 다른 프로그램 (선택, 기본 꺼짐)**: `config.json`에

  ```json
  "forward_events": [
    {"url": "http://127.0.0.1:8799/api/event", "token_env": "NAGGER_EVENTS_TOKEN", "types": ["sigh"]}
  ]
  ```

  를 넣으면 디버거 탭에 "😮‍💨 한숨" 같은 버튼이 생기고, 누르면 폰 → `POST /api/phone_event {"type": "sigh"}` → PC가 위 주소로 `{"type": "sigh", "n": 1}`을 `X-Sidekick-Token: $NAGGER_EVENTS_TOKEN` 헤더와 함께 보냅니다 (`token`으로 직접 적어도 됨, `types` 생략 시 `["sigh"]`). **보낼 주소는 PC 설정에서만** 정해지고 폰은 `types`에 있는 이름만 고를 수 있습니다. 비어 있으면 아무 데도 안 보냅니다.

## 4. API

모든 `/api/*`는 `X-Sidekick-Token: <token>` (또는 `Authorization: Bearer`) 헤더가 필요합니다. `/`(폰 페이지)는 비밀이 없으므로 토큰 없이 열립니다.

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/api/events?since=<id>&wait=25` | 롱폴링. PC → 폰 "이거 말해" |
| POST | `/api/utterance` `{"text"}` | 폰에서 인식한 문장 |
| POST | `/api/transcribe` (오디오 바이트) | PC 음성 인식 (faster-whisper) |
| GET | `/api/alarm/status` | 울리는 중인지, 남은 스누즈 |
| POST | `/api/alarm/check` `{"image": "data:image/jpeg;base64,..."}` | 사진 판정 |
| POST | `/api/alarm/snooze`, `/api/alarm/emergency {"code"}`, `/api/alarm/answer {"answer"}` | 스누즈 / 비상 해제 / 산수 답 |
| POST | `/api/alarm/set {"time":"07:00"}`, `/api/alarm/test` | 시각 변경 / 지금 울리기 |
| POST | `/api/arrival {"source":"motion"\|"button"}` | 귀가 |
| POST | `/api/departure {"source":"motion"\|"button"}` | 출발 체크 (`items` 목록도 반환) |
| POST | `/api/notify {"event", "detail"?, "lines"?}` | 커밋 관중석 (429 = 레이트 리밋) |
| POST | `/api/approval/request {"command","label","description","tool","cwd","timeout_seconds"}` | 결재 올리기 (훅이 사용) |
| GET | `/api/approval/wait?id=&wait=25` | 결재 결과 롱폴링: `pending`/`allow`/`deny`/`expired` |
| POST | `/api/approval/decide {"id", "decision":"allow"\|"deny"}` 또는 `{"id", "utterance"}` | 폰의 승인/반려 (음성은 서버가 해석, 못 알아들으면 `understood:false`) |
| GET | `/api/approval/pending` | 대기 중인 결재 (폰이 늦게 켜져도 보이게) |
| POST | `/api/macro {"id", "confirmed"?}` | 매크로 실행. 메인 토큰 또는 그 매크로 전용 `nfc_key` (200 / `needs_confirm` / 404 / 429 / 502) |
| GET | `/api/macro/audit?n=50` | 매크로 감사 로그 (메인 토큰만) |
| POST | `/api/say {"text", "module"?}` | 외부 프로그램이 폰에 말 시키기 |
| POST | `/api/phone_event {"type", "n"?}` | 폰 버튼 이벤트를 `forward_events`로 전달 |
| GET | `/api/config`, `/api/ping` | 폰 페이지 초기화 / 연결 확인 |

## 5. 프라이버시·보안

- 카메라·마이크 데이터는 **폰 ↔ 내 PC** (LAN 또는 tailnet) 안에서만 오갑니다. 외부로 나가는 건 `llm`/`vision`에 외부 API를 직접 설정했을 때뿐입니다. 로컬 llama-server를 쓰면 아무것도 밖으로 안 나갑니다.
- **기본적으로 아무것도 저장하지 않습니다.** 사진은 판정 후 버려지고, PC 음성 인식 오디오는 임시 파일을 바로 지우며, 서버 접속 로그에는 경로만 남고 발화 내용은 남지 않습니다. 이벤트 큐(최근 100개 문장)는 메모리에만 있습니다.
- 폰에서 온 글자는 **절대 명령으로 실행하지 않습니다.** 실행되는 건 `config.json`에 미리 적어 둔 명령뿐이고, 발화는 `shell=False`로 인자 하나 안의 데이터로만 들어갑니다 (`str.format`도 쓰지 않음).
- 토큰은 상수 시간 비교, 요청 크기 제한(`server.max_body_mb`), 기본 바인딩은 localhost.
- 매크로 패드: 폰은 id만 보내고 PC는 config의 동작만 실행합니다 (`launch`는 `shell=False` 인자 목록, `url`은 허용 스킴만). NFC 키는 매크로별 전용·취소 가능하며 다른 API엔 통하지 않습니다.
- 결재 보고: 폰은 승인/반려만 표시할 수 있고 아무것도 실행하지 않습니다. `notify`·훅은 **현재 폴더의 config.json을 읽지 않습니다** (훅은 남의 저장소 안에서 돌기 때문에, 그 저장소가 심어 둔 설정이 위험 패턴이나 `fail_open`을 바꾸지 못하게).
- 바깥으로 나가는 요청은 직접 켠 것뿐: `vision`/`llm`, `open_meteo`(기본 꺼짐), `forward_events`(기본 비어 있음), `objmem_items`(기본 꺼짐, 보통 localhost). `morning_gate`·`soundwatch_door`·`paperbutler_returns`는 다른 프로젝트의 파일을 **읽기 전용**으로만 엽니다(기본 꺼짐).
- `config.json`에는 토큰과 비상 코드가 있으니 커밋하지 마세요 (`.gitignore`에 포함).

## 6. 테스트

```bash
python3 -m unittest discover -s tests -v
```

외부 네트워크 없이 돌아갑니다 (날씨는 가짜 fetch, nvidia-smi는 가짜 runner, object-memory는 포트 0의 가짜 서버, sound-watch·paper-butler sqlite는 원본 스키마 텍스트로 만든 임시 DB, 서버는 포트 0). Node + Playwright(Chromium)가 있으면 `test_page_smoke`가 실제 브라우저로 폰 페이지를 띄워 JS 오류 없이 디버거·알람(가짜 카메라)·집사·출발·관중석·매크로 패드(탭 버튼, 확인 창, NFC 해시 진입 — 가짜 launcher) 흐름이 도는지 보고, 마지막에 **진짜 훅**으로 `rm -rf ./build` 결재를 올려 폰 화면에서 승인 버튼을 눌러 훅이 `allow` JSON을 내는지까지 확인합니다. 없으면 건너뜁니다.

## 7. 로드맵 (다음 모듈 후보)

- **전화 대행**: 스피커폰으로 걸려온 전화를 듣고 요약하거나, 정해진 대본으로 대신 응대 (예약 확인 등)
- **현실판 Screenpipe**: 책상 위 장면·대화를 (명시적으로 켰을 때만) 기록하고 "아까 내가 뭐라고 했지?"에 답하기
- 그 밖에: 실제 날씨·택배 provider, 반복 일정(RRULE) 지원, 음성 웨이크워드
