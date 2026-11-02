# object-memory — 현실판 Screenpipe, "내 물건 기억"

남는 갤럭시 S21을 방에 세워 두면 몇 분마다 사진을 찍고, **내 PC의 로컬 비전 모델(VLM)** 이 사진 속 물건과 위치를 글로 적어 둡니다.
나중에 "리모컨 어디 뒀지?", "어제 택배 상자 어디 놨더라" 하고 물으면 **찍힌 시각 · 카메라 이름 · 위치 설명 · 사진 경로**를 알려 줍니다.

- Python 3.9+, **표준 라이브러리만** 사용 (설치할 것 없음)
- 이미지는 `base_url`(기본 `http://127.0.0.1:8080/v1`, OpenAI 호환, 예: llama-server + VLM)로만 전송. **프록시는 쓰지 않고**, 어디에도 업로드하지 않습니다.
- 이미지를 디코딩하지 않습니다 (JPEG 바이트를 그대로 저장·base64 전송).

## 반드시 읽어 주세요 (개인정보)

- **내가 사는 내 공간에서만** 쓰세요. 남의 집, 숙소, 직장, 공용 공간에 두지 마세요.
- **같이 사는 사람과 손님에게 미리 알리세요.** 카메라가 있다는 것, 사진이 어디에 얼마나 보관되는지 말해 주세요. 동의하지 않는 사람이 있으면 쓰지 마세요.
- **욕실·화장실·탈의 공간·다른 사람의 침실은 비추지 마세요.** 내 침실도 가족이 쓰는 방이라면 피하세요. 잘 때/집에 손님이 올 때는 **일시정지** 하거나 `privacy_hours` 를 쓰세요.
- 타인을 촬영·녹화하는 것에는 나라마다 법과 규범이 있습니다(국내는 개인정보 보호법·주거/사생활 관련 법). 가족·세입자·방문객이 찍힐 수 있다면 법적 책임이 생길 수 있으니 확인하세요. 이 도구는 법률 자문이 아닙니다.
- 사진은 기본 **7일**, 설명(글)은 **90일** 뒤 자동 삭제 대상입니다(`purge`). 사진에는 서류·화면·사람이 찍힐 수 있으니 더 줄이는 것을 권합니다.
- 서버는 기본 `127.0.0.1` 에만 열립니다. 폰에서 쓰려면 Tailscale 같은 **신뢰하는 사설망**에서만 여세요. 토큰은 남에게 보이지 마세요.

## 구조

```
 S21 (충전기에 세워 둠)                       PC
 ┌───────────────────┐   POST /api/frame      ┌───────────────────────────────┐
 │ phone.html        │  (JPEG + 8x8 평균해시) │ objmem serve                  │
 │ N분마다 getUser-  │ ─────────────────────▶ │  토큰·크기 검사 → 개인정보 시간│
 │ Media→canvas→JPEG │                        │  → 해시 중복 → 저장 → VLM 설명 │
 └───────────────────┘                        │  → sqlite(FTS5/bigram) 색인   │
                                              └──────────────┬────────────────┘
 objmem ask "리모컨 어디 뒀지?"  /  GET /api/ask  ◀───────────┘   llama-server (VLM)
```

## 빠른 시작

```bash
cd object-memory
python3 -m objmem config --init          # ~/.objmem/config.json 생성 (랜덤 토큰 포함)
llama-server -m model.gguf --mmproj mmproj.gguf --port 8080     # 비전 모델 (별도 실행)
python3 -m objmem serve                  # http://127.0.0.1:8787/
python3 -m objmem ask "리모컨 어디 뒀지?"
```

### 폰 페이지

폰에서 `http://<PC>:8787/#token=<토큰>` 을 열면 토큰은 폰 localStorage 에 저장되고 주소창에서 지워집니다.
카메라 이름(거실, 책상 …)과 간격(분)을 정하고 **시작**. 화면 켜짐 유지(Wake Lock), **일시정지**(카메라까지 끔), 마지막 업로드 시각 표시가 있습니다.

`getUserMedia` 는 **HTTPS 또는 localhost** 에서만 동작합니다. 방법 (s21-sidekick README와 같은 요령):

1. **adb reverse (가장 간단)**: PC에 USB로 연결 후 `adb reverse tcp:8787 tcp:8787`, 폰에서 `http://localhost:8787/#token=...`. 서버는 `127.0.0.1` 그대로.
2. **Tailscale HTTPS**: `tailscale serve --bg --https=443 http://127.0.0.1:8787` → `https://<pc>.<tailnet>.ts.net/#token=...`
3. **Chrome 플래그(테스트용)**: `chrome://flags/#unsafely-treat-insecure-origin-as-secure` 에 주소 등록, 서버 `--host`를 Tailscale IP로.

### 폰에서 동기화한 사진 가져오기

```bash
python3 -m objmem ingest ~/Sync/Camera --camera 거실            # 하위 폴더 포함
python3 -m objmem ingest ~/Sync/Camera --no-describe && python3 -m objmem describe   # 나중에 설명
```
촬영 시각은 파일명(`IMG_20261101_214030.jpg`, `PXL_...`)에서, 없으면 파일 수정 시각을 씁니다. `privacy_hours` 는 **사진의 촬영 시각** 기준으로 적용됩니다. 폴더 수집에는 해시가 없어 같은 바이트(sha256)만 중복으로 건너뜁니다.

## 중복 프레임 건너뛰기 (이미지 디코딩 없이)

순수 표준 라이브러리로 JPEG 을 디코딩하는 것은 비현실적이라, **폰 페이지가 캔버스로 8x8 그레이스케일 평균 해시(aHash, 64비트)를 계산해** 16자리 hex 로 JPEG 과 함께 보냅니다 (`POST /api/frame?camera=거실&hash=<hex16>`).
서버는 **같은 카메라의 마지막으로 저장된 프레임** 해시와 해밍 거리를 비교해 `hash_threshold`(기본 4) 이하면 `similar` 로 건너뜁니다 — 저장도, VLM 호출도 하지 않습니다.
마지막 "저장된" 프레임과 비교하므로 조명이 서서히 바뀌는 식의 느린 변화도 누적되면 결국 저장됩니다. 해시가 없거나 형식이 틀리면 무시하고 sha256 완전 일치만 중복 처리합니다.
한계: 작은 물건이 옮겨진 정도는 8x8 해시로 안 보일 수 있습니다. 놓침이 잦으면 `hash_threshold` 를 낮추세요(0이면 해시가 완전히 같을 때만 건너뜀).

## 질문

```bash
python3 -m objmem ask "리모컨 어디 뒀지?"
python3 -m objmem ask "어제 택배 상자 어디 놨더라" --json
```
```
오늘 21:40 거실 카메라에서 소파 왼쪽 쿠션 위에 있었어요
  사진: /home/me/.objmem/data/photos/2026-11-02/1793637600_ab12cd34ef.jpg
```
- 질문 해석은 규칙 기반: 의문/동사 어미(어디, 뒀지, 놨더라, 있었어 …), 대명사(내), 조사(을/를/이/가/은/는/에서 …)를 걷어 내고 남은 말을 물건으로 봅니다. 영어 `where is my remote?` 도 됩니다.
- 시간 힌트: 오늘/어제/그저께/N일 전/N시간 전/아까/지난주/이번주/어젯밤, 오전/오후/아침/저녁. 해당 시간대 기록이 있으면 그 안에서 찾고, 없으면 "그저께에는 못 봤고, 가장 최근 기록이에요" 로 안내합니다.
- 동의어(작은 표 `objmem/synonyms.py`): 리모컨/리모콘/remote, 지갑/wallet, 열쇠/키/key, 택배/상자/박스, 휴대폰/핸드폰/폰 …
- 순위: `일치도(이름 1.0 / 부분 0.7 / 위치문장 0.4) × (0.4+0.6×신뢰도) × 0.5^(경과시간/48h)`. 같은 카메라·같은 위치 설명은 하나로 합쳐 최대 3건.
- 24시간(`stale_hours`)보다 오래됐으면 "마지막으로 본 게 3일 전이에요". 사진이 보관 기간으로 삭제됐으면 그렇게 알려 줍니다.
- `--llm` 또는 `llm_phrase: true` 이면 기록만 근거로 VLM 서버(텍스트)가 한두 문장으로 다듬습니다. 실패하면 규칙 답변을 그대로 씁니다.

## 설명 · 색인

- VLM 에게 한국어 프롬프트로 `{"objects":[{"name","where","confidence"}],"scene"}` JSON 을 요청합니다 (`objmem/vlm.py`).
- 파서는 코드펜스, `<think>…</think>`(닫는 태그만 있어도), 앞뒤 잡담, 끝쉼표, 잘린 출력(마지막 쉼표까지 되감아 닫기), `"80%"`/`85` 같은 신뢰도를 처리하고 형식이 틀린 항목은 버립니다.
- 같은 사진(sha256)은 설명을 **캐시**해 VLM 을 다시 부르지 않습니다. VLM 이 꺼져 있으면 사진만 저장(`pending`)하고 나중에 `objmem describe` 로 처리합니다.
- 색인: 런타임에 sqlite 의 FTS5 지원을 확인해 쓰고(`config` 출력의 `fts5_available`), 없거나 `use_fts:false` 면 자체 문자 bigram 테이블로 동일하게 동작합니다. 한국어는 형태소 분석 없이 **문자 bigram** 으로 색인하므로 "충전" 으로도 "충전기" 가 걸립니다. 색인 대상은 물건 이름 + 동의어 + 위치 설명입니다.

## 보관 · 개인정보 설정

| 키 | 기본 | 설명 |
|---|---|---|
| `photo_days` | 7 | 이 일수가 지난 사진 파일 삭제 (설명은 유지) |
| `desc_days` | 90 | 이 일수가 지난 설명·목격·캐시·사진 삭제 |
| `privacy_hours` | `[]` | 예 `["23:00-07:00"]` (자정 넘김 가능, `"00:00-00:00"` 은 하루 종일). **이 시간대에는 저장도 VLM 전송도 하지 않음** (서버는 `status: privacy` 로 응답) |

`python3 -m objmem purge` 를 cron/작업 스케줄러에 걸어 두세요 (예: 매일 새벽).

## 설정 (`~/.objmem/config.json`, `OBJMEM_CONFIG`/`--config`, 토큰은 `OBJMEM_TOKEN` 으로도)

`base_url, model, api_key, vlm_timeout, data_dir, token, host(127.0.0.1), port(8787), max_frame_bytes(6MB), hash_threshold(4), photo_days, desc_days, privacy_hours, stale_hours, llm_phrase, use_fts`

## HTTP API

모든 `/api/*` 는 `X-ObjMem-Token: <토큰>` (또는 `Authorization: Bearer`) 필요. `/`(폰 페이지)만 토큰 없이 열립니다.

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/frame?camera=거실&hash=<hex16>` (본문 JPEG) | 200 `{status: stored\|similar\|duplicate\|privacy, described, ...}`, 401 토큰, 411 길이 없음, 413 크기 초과(본문을 읽지 않고 끊음), 415 JPEG 아님 |
| GET | `/api/ask?q=리모컨 어디 뒀지?&llm=0\|1` | `{found, answer, details, target, sightings:[{name,where,when,age,camera,photo,frame_id,confidence}]}` |
| GET | `/api/photo?id=<frame_id>` | 사진 JPEG |
| GET | `/api/status`, `/api/ping` | 저장 현황 / 연결 확인 |

## 다른 프로젝트와의 관계 (읽기만 했고 수정하지 않음)

- **s21-sidekick**: 폰 페이지가 카메라 프레임을 보내는 곳은 `/api/alarm/check`(알람 사진 판정, data URI) 정도이고 **프레임을 저장·보관하는 업로드 엔드포인트는 없습니다.** 그래서 object-memory 가 자체 `POST /api/frame` 과 `phone.html` 을 갖습니다. 토큰/HTTPS 안내는 같은 방식입니다. 한 폰에서 두 페이지를 동시에 켜면 카메라를 서로 가져가므로 하나만 쓰세요.
- **game-diary**: VLM 호출(OpenAI 호환 `chat/completions` + base64 data URI, `ProxyHandler({})`), JSON 관대 파싱, sha256 캐시 패턴을 그대로 따랐습니다.
- **jarvis-hub** — `memory.ask` 작업 타입 연동 방법 (hub 코드는 건드리지 않았으므로 아래는 적용 시의 설계입니다):
  1. 5080/미니PC 워커에 핸들러를 추가합니다. `jarvis/handlers/memory.py` 예시:
     ```python
     class MemoryAskHandler(Handler):
         type = "memory.ask"          # payload: {"q": "리모컨 어디 뒀지?"}
         def run(self, job):
             q = (job.get("payload") or {}).get("q", "")
             if not isinstance(q, str) or not q.strip() or len(q) > 300:
                 raise PermanentError("q 가 필요합니다")
             url = self.cfg["url"].rstrip("/") + "/api/ask?" + urllib.parse.urlencode({"q": q})
             req = urllib.request.Request(url, headers={"X-ObjMem-Token": self.cfg["token"]})
             data = json.loads((self.opener or urllib.request.urlopen)(req, timeout=30).read())
             return {"text": data["answer"], "summary": data["answer"][:300]}
     ```
     `build_handlers` 에 `h.get("memory.ask")` 가 `url`+`token` 을 가질 때만 등록하고, 워커 설정에 `"handlers": {"memory.ask": {"url": "http://127.0.0.1:8787", "token": "..."}}` 와 caps `memory` 를 둡니다 (`token` 은 환경변수로 읽는 방식을 권장).
  2. `intents.py` 규칙에 "어디 (뒀|놨|있)" 패턴 → `{type: "memory.ask", payload: {q: 발화}, notify: say}` 를 추가하면 "자비스, 리모컨 어디 뒀지?" 가 작업 → 완료 시 폰이 `answer` 를 읽어 줍니다.
  3. 질의 서버(objmem)는 사진이 있는 PC에만 두고, 허브는 tailnet 안에서 그 PC 워커에게 작업을 넘기면 됩니다.

## 테스트

```bash
cd object-memory && python3 -m unittest discover -s tests -v
```
가짜 VLM + SOI/EOI 마커만 있는 합성 JPEG 바이트를 쓰며, 토큰은 실행 시점에 생성합니다. Node Playwright(`require('playwright')`)와 Chromium 이 있으면 가짜 카메라(`--use-fake-device-for-media-stream`)로 폰 페이지를 열어 실제 업로드까지 확인하고, 없으면 그 테스트만 건너뜁니다.

## 한계

- 실제 VLM 의 물건 인식·위치 묘사 품질은 모델에 달렸습니다. 작은 물건은 놓칠 수 있고, 모델이 없는 물건을 말할 수도 있어 `confidence` 와 사진 경로를 함께 보여 줍니다.
- 8x8 평균 해시는 거친 중복 제거용입니다. 폰 화면이 꺼지거나 Chrome 이 백그라운드로 가면 촬영이 멈춥니다(Wake Lock + 충전 + 화면 켜 두기 권장).
- 질문 해석은 규칙 기반이라 낯선 말투는 물건을 못 뽑을 수 있습니다. 그땐 물건 이름만 짧게 물어보세요 ("리모컨").
