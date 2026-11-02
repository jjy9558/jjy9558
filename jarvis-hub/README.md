# jarvis-hub — S21 + 미니PC + 5080을 하나로 묶는 자비스

남는 갤럭시 S21(귀·눈·입), 항상 켜 둔 미니PC(두뇌·교환대), 가끔 켜는 RTX 5080 메인PC(근육)를
**하나의 작업 큐**로 묶습니다. 폰에 대고 "자비스, 이 PDF 요약해줘"라고 하면, 미니PC 허브가 작업을 만들고,
5080이 꺼져 있으면 Wake-on-LAN으로 깨우고, 5080 워커가 로컬 LLM으로 처리한 뒤, 끝나면 폰이 소리 내어 알려줍니다.

- Python 3.9+, **표준 라이브러리만** (설치할 것 없음)
- 허브 = HTTP JSON API + sqlite 작업 큐 + 대시보드 한 장
- 워커 = 허브에서 자기 능력(caps)에 맞는 작업을 빌려(lease) 와서 처리하는 프로세스
- 모든 기기는 Tailscale로 연결 (허브는 tailnet 안에만 엽니다)

## 1. 구조

```
                         Tailscale (100.x.x.x, 기기마다 토큰)
 ┌──────────────┐  "자비스, 이 PDF 요약해줘"     ┌──────────────────────────────────────────┐
 │  Galaxy S21  │ ───── POST /api/say ─────────▶│ 미니PC  (항상 켜짐)                        │
 │  마이크/스피커 │                              │  ┌────────────── jarvis hub ────────────┐ │
 │  카메라       │ ◀─ GET /api/events (say) ─── │  │ 의도 파서(규칙) → 라우터             │ │
 │  대시보드 / ← │                              │  │ sqlite 작업 큐 (우선순위·caps·임대)   │ │
 │  s21-sidekick │ ◀─ notify.say ──────┐       │  │ 기기 레지스트리 (하트비트 → 온/오프)  │ │
 └──────────────┘                      │       │  │ 이벤트 스트림 · ntfy · 대시보드 GET / │ │
        ▲ ntfy 푸시 (선택)              │       │  └───────────────▲──────────────────────┘ │
        │                              │       │                  │ lease / complete         │
        │                              └───────│── jarvis worker (minipc): wake, notify.say │
        │                                      └──────────────────┼───────────────┬─────────┘
        │                                         lease (롱폴링)   │               │ WOL 매직 패킷
        │                                                         │               ▼ (같은 LAN 브로드캐스트)
        │                                      ┌──────────────────┴──────────────────────────┐
        └──────────────────────────────────────│ 메인PC RTX 5080 (필요할 때만 켜짐)           │
                                               │  jarvis worker (main): llm.chat, shell.allowed│
                                               │                        claude.task(선택)      │
                                               │  llama-server / vLLM (OpenAI 호환 :8080/v1)  │
                                               └──────────────────────────────────────────────┘
```

작업 하나의 일생:

```
submit ──▶ queued ──lease──▶ leased ──complete──▶ done ──▶ 알림(say 이벤트, notify.say, ntfy)
   │          ▲                 │  └──fail(retry, 시도 남음)──▶ queued (재시도 지연 후)
   │          │                 ├──fail(재시도 불가 / 시도 소진)──▶ failed ──▶ 알림
   │          └─임대 시간 초과(워커 죽음)─┘
   └─(승인 필요 타입)─▶ waiting_approval ──승인──▶ queued   /  ──거절──▶ cancelled
```

## 2. 기기 역할

| 기기 | 역할 | 돌리는 것 | caps 예시 |
|---|---|---|---|
| **미니PC** | 항상 켜진 허브. 큐·기기 상태·알림의 단일 진실 공급원. 메인PC와 같은 LAN이라 WOL 담당 | `jarvis hub`, `jarvis worker`(wake, notify.say), (선택) s21-sidekick 서버 | `lan`, `speaker` |
| **메인PC (5080)** | 무거운 일: 로컬 LLM, (로드맵) STT·이미지·Screenpipe 인덱싱, Claude Code | `jarvis worker` | `gpu`, `llm`, `stt`, `windows`, `claude` |
| **S21** | 입·귀·눈. 말로 명령, 대답·완료 알림을 읽어 줌 | 브라우저: 허브 대시보드(🎤 버튼) 또는 s21-sidekick 페이지 | (워커 아님) |

**caps(능력 태그)** 는 "이 작업은 어떤 기기가 필요한가"입니다. 작업은 `caps ⊆ 워커 caps` **이고** 워커에
그 타입의 핸들러가 켜져 있을 때만 그 워커에게 갑니다. `hub.json`의 기기별 `caps`는 **허용 목록**이라,
워커가 실행 인자로 그보다 많은 caps를 주장하면 허브가 거부(403)합니다.

## 3. 설치 · 설정

### 3-1. 공통: 토큰 만들기

```bash
python -m jarvis token     # 기기마다 하나씩 (16자 이상, 서로 달라야 함)
```

### 3-2. 미니PC — 허브

```bash
cd jarvis-hub
cp hub.example.json hub.json        # 토큰, MAC, Tailscale IP 채우기
python -m jarvis hub --config hub.json
```

`hub.json` 핵심 항목 (전체 기본값은 `jarvis/config.py`의 `HUB_DEFAULTS`):

| 키 | 설명 |
|---|---|
| `server.host` / `port` | 기본 `127.0.0.1:8800`. 미니PC의 **Tailscale IP(100.x)** 에 바인딩하거나, `127.0.0.1`로 두고 `tailscale serve --bg 8800`으로 HTTPS 공개(권장: 폰 마이크가 HTTPS 필요) |
| `devices.<id>.token` | 기기 토큰. 등록 안 된 id는 전부 401 |
| `devices.<id>.worker` | `true`여야 작업을 빌려 갈 수 있음 |
| `devices.<id>.caps` | 이 기기가 주장할 수 있는 caps 허용 목록 |
| `devices.<id>.submit` | 넣을 수 있는 작업 타입(glob). 기본 `["*"]` |
| `devices.<id>.approve` | 승인/거절 권한 (폰에 주면 "자비스, 3번 승인"이 됨) |
| `devices.<id>.trusted` | `true`면 이 기기가 넣은 작업은 승인 없이 바로 큐로 |
| `approval.required_types` | 신뢰 기기가 아닌 곳에서 넣으면 승인 대기로 가는 타입. 기본 `["claude.task"]` |
| `wake.*` | 메인PC MAC, 브로드캐스트 주소(예 `192.168.0.255`), `for_caps`(기본 `gpu`), 쿨다운 |
| `notify.say_events` / `say_job` | 완료 시 이벤트 스트림에 `say` / `notify.say` 작업 생성 |
| `notify.ntfy` | `enabled`, `server`, `topic`(추측 어려운 이름), `token` |
| `router.shell_aliases` | 말 → 명령 이름. `"백업": {"name": "backup"}` 이면 "자비스, 백업 실행해" |
| `router.llm_fallback` | 모르는 말은 로컬 LLM에 질문으로 넘김 (기본 꺼짐) |
| `schedule.path` | 오늘 일정 파일 (`schedule.example.json` 형식, 또는 `.ics`) |

### 3-3. 미니PC — 워커 (WOL + 폰 알림)

```bash
cp worker.minipc.example.json worker.json
python -m jarvis worker --config worker.json --caps lan,speaker
```

- `handlers.wake.enabled: true` → 허브가 넣은 `wake` 작업을 받아 매직 패킷 전송
- `handlers.notify.say` → s21-sidekick으로 전달 (아래 5절)

### 3-4. 메인PC (5080) — 워커

1. OpenAI 호환 서버를 띄움 (예: `llama-server -m qwen2.5-14b-instruct-q5_k_m.gguf --port 8080 -ngl 99`)
2. BIOS/NIC에서 Wake-on-LAN 켜기, Windows 장치 관리자 → 네트워크 어댑터 → 전원 관리 → "매직 패킷으로만 깨우기"
3. 작업 스케줄러로 로그온 시 워커 자동 실행:

```powershell
copy worker.main.example.json worker.json
python -m jarvis worker --config worker.json --caps gpu,llm,windows,claude
```

| 핸들러 | 켜는 조건 | 하는 일 |
|---|---|---|
| `llm.chat` | `base_url` 설정 | `{base_url}/chat/completions` 호출. `file`이 있으면 `files_dir` 안의 문서(pdf/txt/md, `@latest`=가장 최근)를 붙임. PDF는 `pdftotext`(poppler) 필요 |
| `shell.allowed` | `commands`에 하나 이상 | **이름 붙인 명령만** 실행. argv 목록 그대로, 셸 없음. 인자는 `{param}` 자리(항목 전체)에만, `choices` 또는 `pattern`으로 검증 |
| `claude.task` | `enabled: true` (기본 **꺼짐**) | `command` 템플릿(`["claude","-p","{prompt}"]`)을 `repos`에 등록한 폴더에서 실행. 프롬프트는 인자 하나로 |
| `notify.say` | `enabled: true` | s21-sidekick에 말하기 요청 |
| `wake` | `enabled: true` | WOL 매직 패킷 (UDP 브로드캐스트, 102바이트 = FF×6 + MAC×16) |

> Windows 주의: `claude`가 `claude.cmd`로 잡히면 cmd.exe가 인자를 다시 해석합니다(BatBadBut).
> 그래서 실행 파일이 `.cmd/.bat`이거나 Windows에서 확장자 없이 쓴 경우 `" % ! ^ & | < > ( )` 등을 지웁니다.
> 가능하면 `["node", "C:/.../@anthropic-ai/claude-code/cli.js", "-p", "{prompt}"]`처럼 직접 지정하세요.

### 3-5. S21

가장 간단한 방법: 허브 대시보드를 폰에서 엽니다.

```
https://<미니PC>.<tailnet>.ts.net/#device=s21&token=<s21 토큰>
```

토큰은 폰 localStorage에 저장되고 주소창에서는 지워집니다. 🎤 버튼(Web Speech, ko-KR, HTTPS 필요)이나 입력창으로
명령하고, "대답·완료 알림 소리로 읽기"를 켜면 이벤트 스트림의 `say`를 `speechSynthesis`로 읽습니다.
대시보드는 호출어 없이 바로 처리합니다(`direct: true`).

### 3-6. 아무 PC에서 폰 흉내 내기

```bash
cp client.example.json client.json
python -m jarvis say "자비스, 오늘 일정이랑 작업 상태 알려줘"
python -m jarvis say --direct "컴퓨터 켜"
python -m jarvis status
python -m jarvis approve 3            # --reject 로 거절
python -m jarvis submit llm.chat --payload '{"prompt":"안녕"}' --caps gpu --notify
python -m jarvis events               # 이벤트 실시간 보기
python -m jarvis wol AA:BB:CC:DD:EE:FF --broadcast 192.168.0.255   # WOL 단독 테스트
```

## 4. 예시 흐름

**① "자비스, 오늘 일정이랑 작업 상태 알려줘"** — LLM 없음, 허브 상태로 즉답

```
폰 → POST /api/say → 의도 [schedule, status]
대답: "오늘 일정 2개: 14:00 치과, 19:30 LCK 결승. 작업 3개 진행 중, 1개 승인 대기, 5080 꺼져 있음."
```

**② "자비스, 이 PDF 요약해줘"** — 5080 꺼져 있음

```
1. 라우터: llm.chat 작업 #12 (caps=[gpu], file=@latest, notify) 생성
2. 허브: gpu 작업인데 처리 가능한 온라인 워커 없음 → wake 작업 #13 (쿨다운 5분)
3. 대답: "PDF 요약 작업 12번 넣었어요. 5080이(가) 꺼져 있어서 켜는 중이에요. 켜지면 시작해요."
4. 미니PC 워커가 #13 처리 → 매직 패킷 → 5080 부팅 → 워커 자동 실행 → #12 lease
5. 5080 워커: JarvisInbox 최신 PDF → pdftotext → 로컬 LLM 요약 → complete
6. 허브: say 이벤트 + notify.say 작업 + ntfy → 폰: "작업 12번 문서 요약 끝났어요. (요약 앞부분)"
```

(폰에서 PDF를 보내는 방법: Taildrop으로 5080에 보내고 받은 폴더를 `files_dir`로 지정, 또는 Syncthing 폴더)

**③ "자비스, 컴퓨터 켜"** → `wake` 작업 → "5080 켜는 신호 보낼게요." (이미 켜져 있으면 "이미 켜져 있어요.")

**④ "자비스, 클로드한테 README 오타 고쳐달라고 해줘"**

```
claude.task #14 → 폰은 신뢰 기기가 아니므로 waiting_approval
대답: "클로드 작업 14번 만들었어요. '자비스, 14번 승인'이라고 하면 시작해요."
"자비스, 승인" (대기가 하나면 번호 생략 가능) → queued → 5080 워커가 repos.default 에서
claude -p "README 오타 고쳐달라고" 실행 → 결과 요약을 폰이 읽어 줌
```

**⑤ "자비스, 백업 실행해"** → `router.shell_aliases` → `shell.allowed {"name":"backup"}` → 5080의 `robocopy ...`

인식하는 말(일부): 작업 상태 / 진행 상황 / 지금 뭐 하고 있어 / 큐에 몇 개 있어 / 기기 상태 / 컴퓨터 켜져 있어? /
오늘 일정·스케줄·약속 / 컴퓨터·PC·본컴·5080 켜·깨워·부팅 / 이 PDF·파일 요약 / report.pdf 요약 /
N번 승인·취소 / 클로드한테 … 시켜줘 / 로컬 모델한테 … 물어봐 / 몇 시야 / 뭐 할 수 있어.
호출어: "자비스", "자비스야", "헤이 자비스", "쟈비스", "jarvis" (폰 CLI/API에서 `direct`면 생략 가능).

## 5. S21 사이드킥 연동 (`notify.say`)

`s21-sidekick` 코드를 확인한 결과: 폰 페이지는 `GET /api/events`를 롱폴링해서 `kind == "say"`인 이벤트를 읽어 주지만,
**외부에서 say 이벤트를 넣는 범용 엔드포인트는 아직 없습니다** (`/api/utterance`는 혼잣말 트리거 판정용,
`/api/arrival`은 귀가 집사용). 그래서 `notify.say` 핸들러는 `POST {sidekick_url}/api/say`
(헤더 `X-Sidekick-Token`, 본문 `{"text": "...", "module": "jarvis"}`)로 보내 보고, URL이 없거나 404면
**로그만 남기고** `delivered: false`로 끝냅니다 (재시도 안 함).

사이드킥 쪽에 필요한 코드 (그 프로젝트 담당자가 추가할 것 — 이 저장소에서는 건드리지 않음):

```python
# sidekick/server.py, Handler 안
def r_say(self, q):
    body = self._json()
    text = str(body.get("text") or "").strip()[:500]
    if not text:
        raise ValueError("text가 비었습니다")
    ev = self.app.events.say(text, module=str(body.get("module") or "jarvis")[:32])
    return 200, {"ok": True, "id": ev["id"]}

# ROUTES 에 추가
("POST", "/api/say"): Handler.r_say,
```

그 전까지는 허브 대시보드를 폰에서 열어 두면(소리 읽기 켬) 같은 알림을 허브 이벤트 스트림으로 직접 받습니다.

## 6. HTTP API

인증: 모든 `/api/*`(ping 제외)에 `X-Jarvis-Device: <id>` + `X-Jarvis-Token: <token>`
(또는 `Authorization: Bearer <id>:<token>`). 본문은 JSON, 최대 256KB.

| 메서드 · 경로 | 설명 |
|---|---|
| `GET /` | 대시보드 (인증 없음, 데이터는 토큰으로 API 호출) |
| `GET /api/ping` | 살아 있나 |
| `POST /api/jobs` | 작업 넣기 `{type, payload, caps, priority(-100~100), max_attempts, lease_seconds, notify, title}` |
| `GET /api/jobs?status=&limit=` · `GET /api/jobs/<id>` | 목록 · 단건 |
| `POST /api/lease` | 워커 전용. `{caps, types, wait(≤30s 롱폴링), info}` → `{job: null | {...}}` |
| `POST /api/heartbeat` | `{caps, types, jobs:[임대 중인 id]}` → 임대 연장 + 온라인 표시 |
| `POST /api/jobs/<id>/complete` · `/fail` | `{result}` · `{error, retry}` (빌려 간 기기만, 아니면 409) |
| `POST /api/jobs/<id>/approve` · `/reject` | 승인(`approve` 권한) · 거절/취소(권한 또는 만든 기기) |
| `GET /api/devices` | 기기 목록 + 온라인 여부 (`presence.offline_after_seconds`, 기본 45초) |
| `GET /api/events?since=&wait=` | 이벤트 롱폴링: `job.submitted/leased/done/failed/retry`, `device.online/offline`, `wake.requested`, `say` |
| `GET /api/status` | 대시보드용 요약 |
| `POST /api/say` | `{text, direct, file}` → `{answer, intents, jobs, ignored}` |

큐 규칙: 우선순위 높은 것 먼저, 같으면 먼저 들어온 것. 임대 시간(`lease_seconds`, 기본 300초)이 지나도
하트비트로 연장되지 않으면 다시 queued (시도 횟수 소진 시 failed). 일시적 오류는 `retry_delay_seconds` 후 재시도,
잘못된 요청(허용 안 된 명령 등)은 즉시 failed.

## 7. 보안

- 기기별 토큰, 등록 안 된 기기 id 거부, 상수 시간 비교. 다른 기기 토큰으로 사칭 불가.
- 기기별 권한: 워커 여부, 주장 가능한 caps, 넣을 수 있는 타입, 승인 권한, 신뢰 여부.
- **임의 명령 실행 경로 없음**: `shell.allowed`는 설정에 이름 붙인 argv만, `claude.task`는 고정 템플릿 + 등록된 폴더만.
  `shell=True`/`os.system`/`eval` 없음 (`tests/test_safety.py`가 소스 전체를 검사).
- `llm.chat`의 파일은 `files_dir` 안의 파일 이름만 (경로·`..` 거부).
- 크기 제한: 요청 256KB, payload 64KB, result 256KB, 활성 작업 500개, 발화 500자.
- `claude.task`는 기본 꺼짐 + 폰에서 넣으면 승인 필요. 음성 승인은 `approve` 권한이 있는 기기만.
  (음성은 누구나 낼 수 있으니 폰을 거실에 두면 `s21.approve`는 끄고 대시보드 버튼으로만 승인하는 걸 권장)
- 허브는 tailnet 안에서만 여세요. 공용 인터넷에 직접 노출 금지.

## 8. 테스트

```bash
cd jarvis-hub && python3 -m unittest discover -s tests -v
```

큐(임대/caps 매칭/우선순위/만료·재시도/연장), 하트비트 온·오프라인, WOL 패킷 바이트(실제 UDP 루프백 포함),
한국어 의도 파싱(40여 개 표현), 상태 기반 대답, 핸들러 안전성(허용 안 된 이름 거부, 인자 검증, 셸 없음),
claude.task 기본 꺼짐·인자 하나 전달, 허브+워커 스레드를 localhost 포트 0에 띄운 HTTP 종단 간 테스트.

## 9. 로드맵

| 단계 | 내용 |
|---|---|
| v0.2 **STT on 5080** | `stt.transcribe` 작업 타입 (caps `gpu,stt`): 폰이 녹음 업로드 → faster-whisper large-v3(cuda, float16) → 텍스트를 다시 라우터로. 5080이 꺼져 있으면 폰 Web Speech로 폴백 |
| v0.2 **TTS** | `tts.speak` (caps `gpu,tts`): 5080에서 한국어 TTS 생성 → 사이드킥이 오디오 재생 |
| v0.3 **카메라 작업** | `camera.snapshot` (caps `camera`): 사이드킥 페이지가 허브 워커처럼 lease → 사진 촬영 → `vision.describe` (caps `gpu`) 체인. "자비스, 현관 좀 봐줘" |
| v0.3 **작업 체인** | 결과를 다음 작업 payload로 넘기는 `then` 필드 (STT → 라우터 → LLM → TTS) |
| v0.4 **현실판 Screenpipe** | `screen.capture` (메인PC, caps `windows`): 일정 간격 스크린샷 → `ocr.index`(gpu) → sqlite FTS. "자비스, 아까 보던 그 에러 뭐였지?" → `memory.search` 작업. 원본은 메인PC에만, 허브에는 요약만 |
| v0.4 **일정 연동** | 구글 캘린더 비공개 iCal 주기적 다운로드 (`schedule.path`가 이미 `.ics` 지원) |
| 이후 | 메인PC 원격 종료(`shell.allowed`의 `shutdown` 별칭 + 승인), 작업별 비용/시간 통계, 대시보드 작업 상세 |

## 10. 파일

```
jarvis/
  cli.py         python -m jarvis {hub,worker,say,submit,status,approve,events,wol,token}
  config.py      기본값 병합 + 검증
  store.py       sqlite 큐 / 기기 / 이벤트
  hub.py         허브 로직 + HTTP 서버
  router.py      의도 → 대답/작업
  intents.py     한국어 규칙 기반 의도 파서
  worker.py      워커 루프 (lease 롱폴링, 하트비트 스레드)
  client.py      허브 HTTP 클라이언트
  wol.py         매직 패킷
  notifier.py    ntfy
  schedule.py    오늘 일정 (JSON / ICS)
  handlers/      llm.chat, shell.allowed, claude.task, notify.say, wake
  static/dashboard.html
hub.example.json  worker.main.example.json  worker.minipc.example.json  client.example.json  schedule.example.json
```
