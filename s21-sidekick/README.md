# s21-sidekick — Claude에게 몸을 붙여준다

액정 깨진 채 서랍에 있던 갤럭시 S21을 **방 안에 있는 Claude의 눈·귀·입**으로 씁니다.

```
S21이 듣고 보면  ──▶  PC가 판단  ──▶  S21이 말하거나 / PC가 행동
 (마이크·카메라)       (트리거·LLM·VLM)     (TTS)          (미리 정한 명령)
```

뼈대는 하나이고, 그 위에 모듈 세 개가 꽂혀 있습니다.

| 모듈 | 폰이 하는 일 | PC가 하는 일 |
|---|---|---|
| 혼잣말 디버거 | "아 이거 왜 안 돼"를 듣는다 → 요약을 읽어준다 | 최근 에러 로그를 보거나 `claude -p`를 돌린다 |
| 침대 탈출 알람 | 시끄럽게 울린다 → 칫솔/세면대 사진을 찍는다 | 비전 모델로 판정: "반려. 아직 누워 있음." |
| 귀가 집사 | 현관 움직임(또는 버튼)을 감지 → 요약을 읽어준다 | 일정·Claude 상태 등 provider 결과를 모은다 |

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
| `butler.providers` | 귀가 요약에 들어갈 provider 목록 (아래 참고) |
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
| GET | `/api/config`, `/api/ping` | 폰 페이지 초기화 / 연결 확인 |

## 5. 프라이버시·보안

- 카메라·마이크 데이터는 **폰 ↔ 내 PC** (LAN 또는 tailnet) 안에서만 오갑니다. 외부로 나가는 건 `llm`/`vision`에 외부 API를 직접 설정했을 때뿐입니다. 로컬 llama-server를 쓰면 아무것도 밖으로 안 나갑니다.
- **기본적으로 아무것도 저장하지 않습니다.** 사진은 판정 후 버려지고, PC 음성 인식 오디오는 임시 파일을 바로 지우며, 서버 접속 로그에는 경로만 남고 발화 내용은 남지 않습니다. 이벤트 큐(최근 100개 문장)는 메모리에만 있습니다.
- 폰에서 온 글자는 **절대 명령으로 실행하지 않습니다.** 실행되는 건 `config.json`에 미리 적어 둔 명령뿐이고, 발화는 `shell=False`로 인자 하나 안의 데이터로만 들어갑니다 (`str.format`도 쓰지 않음).
- 토큰은 상수 시간 비교, 요청 크기 제한(`server.max_body_mb`), 기본 바인딩은 localhost.
- `config.json`에는 토큰과 비상 코드가 있으니 커밋하지 마세요 (`.gitignore`에 포함).

## 6. 테스트

```bash
python3 -m unittest discover -s tests -v
```

외부 네트워크 없이 돌아갑니다. Node + Playwright(Chromium)가 있으면 `test_page_smoke`가 실제 브라우저로 폰 페이지를 띄워 JS 오류 없이 디버거·알람(가짜 카메라)·집사 흐름이 도는지 확인하고, 없으면 건너뜁니다.

## 7. 로드맵 (다음 모듈 후보)

- **전화 대행**: 스피커폰으로 걸려온 전화를 듣고 요약하거나, 정해진 대본으로 대신 응대 (예약 확인 등)
- **현실판 Screenpipe**: 책상 위 장면·대화를 (명시적으로 켰을 때만) 기록하고 "아까 내가 뭐라고 했지?"에 답하기
- **커밋 관중석**: Claude Code 훅이 테스트 통과/커밋/PR 머지를 알리면 폰이 환호하거나 야유하는 관중이 되기
- 그 밖에: 실제 날씨·택배 provider, 반복 일정(RRULE) 지원, 음성 웨이크워드
