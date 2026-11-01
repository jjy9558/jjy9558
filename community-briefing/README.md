# community-briefing — 나한테만 맞춘 커뮤니티 브리핑 봇

내가 자주 보는 커뮤니티/뉴스 사이트의 글을 모아서, **내 관심사에 맞는 것만** 골라
**짧은 한국어 일일 브리핑**(Markdown + JSON)으로 만들어 주는 작은 봇입니다.

- 파이썬 3.9+ **표준 라이브러리만** 사용 (설치할 패키지 없음)
- RSS 2.0 / Atom 피드, 간단한 HTML 목록 페이지 지원
- 관심 키워드(한국어+영어) 가중치 점수, 뮤트 단어, 댓글 수 가산점
- SQLite로 지난 실행과 중복 제거 + 서로 다른 소스의 같은 이야기 합치기 (출처 모두 표시)
- 요약은 **내 PC의 로컬 LLM**(llama.cpp `llama-server` 등 OpenAI 호환 API) 사용,
  서버가 꺼져 있으면 LLM 없이 **추출 요약**으로 자동 대체
- 선택: ntfy 푸시 알림 / **음성 아침 팟캐스트(WAV + 팟캐스트 RSS)** / **이슈 타임라인 HTML 뷰어**

## 결과물 예시

`out/2026-11-01.md`

```markdown
# 커뮤니티 브리핑 — 2026-11-01

## 오늘의 요약

1. llama.cpp에 새 양자화 포맷이 추가돼 VRAM 사용량이 크게 줄었습니다.
2. 24GB VRAM으로 70B 모델을 돌릴 GPU 추천 논의가 활발합니다.
3. RTX 그래픽카드 가격이 하락세입니다.

## 로컬 LLM (2)

- **[Show HN: llama.cpp new quantization format released](https://github.com/...)**
  llama.cpp에 새 양자화 포맷이 추가되었습니다.
  출처: [Hacker News](...) · [r/LocalLLaMA](...) · 댓글 150
```

같은 폴더에 `out/2026-11-01.json`도 생성됩니다 (나중에 TTS·타임라인에서 사용할 구조화 데이터).
같은 날 다시 실행하면 새 글이 있을 때만 `2026-11-01-2.md`처럼 새 파일을 만들고, 기존 파일은 덮어쓰지 않습니다.

## 설치 / 실행

```bash
cd community-briefing
cp config.example.json config.json     # Windows: copy config.example.json config.json
python -m briefing sources --test      # 소스 목록 + 실제로 가져와지는지 시험
python -m briefing run --dry-run       # 파일/DB/알림 없이 결과만 화면에 출력
python -m briefing run                 # out/YYYY-MM-DD.md 생성
python -m briefing run --since 2d --config D:\briefing\config.json
```

- `--since 24h|90m|2d|1w` : 이 기간 안에 올라온 글만 (날짜 없는 글은 포함). 기본값은 `output.since`
- `--dry-run` : 결과만 출력. 본 글을 '읽음'으로 기록하지 않으므로 몇 번이든 다시 시험할 수 있습니다.
  (HTTP 캐시 폴더 `cache/`는 생길 수 있습니다)

테스트 (네트워크 없이 로컬 픽스처로 동작):

```bash
python -m unittest discover -s tests -v
```

## config.json 설명

JSON에는 주석을 쓸 수 없어서 여기에 설명합니다. 상대 경로는 **config.json이 있는 폴더 기준**입니다.

### sources — 글을 가져올 곳

```json
{"name": "GeekNews", "type": "rss", "url": "https://news.hada.io/rss"}
```

| 키 | 설명 |
|---|---|
| `name` | 브리핑에 표시될 출처 이름 |
| `type` | `"rss"` (RSS 2.0/Atom 자동 판별) 또는 `"html"` |
| `url` | 피드 주소 또는 게시판 목록 페이지 주소 |
| `enabled` | `false`면 건너뜀 (기본 `true`) |
| `delay` | 이 소스에 요청하기 전 최소 대기 초 (기본은 `fetch.delay_seconds`) |
| `max_items` | 한 번에 읽을 최대 글 수 (기본 100) |
| `rules` | `type: "html"`일 때만. 아래 참고 |

예시로 들어 있는 GeekNews / Hacker News(hnrss) / r/LocalLLaMA 는 모두 공개 RSS 피드입니다.
Reddit `.rss`는 요청이 잦으면 429(Too Many Requests)를 주므로 `delay`를 넉넉히 두세요.

**HTML 규칙** (CSS 엔진이 아니라 간단한 추출기입니다) — 둘 중 하나를 씁니다.

1. 태그 + 클래스: 해당 요소 안의 `<a href>`를 글 링크/제목으로 사용
   ```json
   "rules": {"tag": "td", "class": "subject", "href_regex": "/board/view/\\d+"}
   ```
   `tag`가 `"a"`이면 그 링크 자체를 사용합니다. `href_regex`는 선택(링크 주소 필터).
2. 정규식: `url`, `title` 이름 그룹이 있어야 합니다.
   ```json
   "rules": {"regex": "<a href=\"(?P<url>/news/\\d+)\"[^>]*>(?P<title>.*?)</a>"}
   ```

CP949(EUC-KR) 페이지도 자동으로 디코딩합니다.

### interests — 관심사

```json
"interests": {
  "topics": {
    "로컬 LLM": {"llama.cpp": 4, "llm": 3, "양자화": 2},
    "GPU/하드웨어": {"gpu": 2, "그래픽카드": 2}
  },
  "mute": ["코인", "crypto", "광고"],
  "comment_boost": 0.5, "multi_source_boost": 0.5, "min_score": 1.0, "top_n": 15
}
```

- `topics`: 토픽별 `키워드: 가중치`. 브리핑은 토픽별로 묶여서 나옵니다(가장 점수를 많이 준 토픽).
  - 점수 = 제목에 있으면 가중치×1.0 + 본문 요약에 있으면 가중치×0.5
  - 영어 키워드는 단어 경계로 매칭 (`ai`가 `said`에 걸리지 않음), 한국어는 부분 일치 (`그래픽카드를`도 매칭)
  - 대소문자 무시
- `mute`: 제목/본문에 하나라도 있으면 제외
- `comment_boost`: 관심 키워드가 하나 이상 맞은 글에만 `boost × log10(1+댓글수)` 가산 (댓글 수를 주는 피드만)
- `multi_source_boost`: 여러 소스에 동시에 올라온 이야기에 주는 가산점
- `min_score`: 이 점수 미만은 제외 / `top_n`: 한 번에 브리핑에 넣을 최대 글 수

### dedupe — 중복 제거

- `title_similarity` (0~1, 기본 0.82): 정규화한 제목 유사도가 이 값 이상이면 같은 이야기로 보고 합침
- `remember_days` (기본 14): 지난 실행에서 본 제목과 비교하는 기간. URL은 기간과 무관하게 기억
- URL은 `utm_*` 등 추적 파라미터, `www.`/`old.`/`m.`, 끝의 `/`, `#...`를 제거해서 비교합니다.
- 기록은 `output.db_path`(기본 `data/seen.sqlite3`). 처음부터 다시 받고 싶으면 이 파일을 지우면 됩니다.

### fetch — 요청 예절

- `user_agent`: 연락처를 넣은 정직한 User-Agent를 권장합니다.
- `delay_seconds`: 같은 호스트에 연속 요청할 때 최소 간격(초)
- `cache_dir`: ETag/Last-Modified 캐시 (변경 없으면 304로 트래픽 절약)
- `respect_robots`: `true`면 `type: "html"` 소스는 robots.txt를 확인하고 금지된 경로는 가져오지 않습니다.
  (RSS 피드는 기계가 읽으라고 공개된 것이므로 확인하지 않습니다)

### llm — 요약용 로컬 LLM

```json
"llm": {"enabled": true, "base_url": "http://127.0.0.1:8080/v1", "model": "local",
        "api_key": "", "timeout": 60, "temperature": 0.3, "use_proxy": false}
```

- `base_url`은 `/v1`까지. 실제 요청은 `{base_url}/chat/completions`로 갑니다.
- 서버에 연결이 안 되면 그 실행 동안은 LLM을 더 시도하지 않고 **추출 요약**(본문 앞 1~2문장)으로 진행합니다.
  → 이 경우 요약이 원문 언어(영어 등) 그대로일 수 있습니다. 브리핑 하단에 `요약 방식: extractive`로 표시됩니다.
- `use_proxy: false`: 시스템 프록시를 거치지 않고 직접 접속 (로컬 서버용)
- LM Studio, Ollama(`http://127.0.0.1:11434/v1`), vLLM 등 OpenAI 호환 서버도 같은 방식으로 사용 가능
- `enabled: false`면 항상 추출 요약

### output / delivery / tts / timeline

- `output.dir` (기본 `out`), `output.db_path`, `output.since` (기본 `24h`)
- `delivery.ntfy`: `enabled: true`, `topic`(추측하기 어려운 이름 권장), 필요하면 `server`(자체 호스팅), `token`.
  휴대폰 ntfy 앱에서 같은 토픽을 구독하면 아침마다 3줄 요약 + 상위 5개 링크가 푸시됩니다.
- `timeline.*`, `tts.*`: 아래 "음성 팟캐스트", "이슈 타임라인" 절 참고

## 음성 팟캐스트 (tts)

`tts.enabled: true`면 매 실행마다 그날 브리핑 JSON으로 **듣기 좋은 한국어 구어체 대본**을 만들고,
선택한 TTS 백엔드로 `out/YYYY-MM-DD.wav` + `out/YYYY-MM-DD.script.txt`를 생성합니다.
실패해도 브리핑 자체는 영향을 받지 않습니다(경고만 출력).

| 키 | 설명 |
|---|---|
| `backend` | `none`(대본만) / `sapi` / `piper` / `openai_compat` |
| `style` | `monologue`(진행자 1명, 기본) / `dialogue`(진행자 A 민준·B 서연 대화체) |
| `use_llm` | `true`면 `llm` 설정의 로컬 LLM으로 대본을 다듬음. 서버가 꺼져 있거나 결과가 부실하면 **템플릿 대본**으로 자동 대체 |
| `voice` / `voices` | 음성 이름. `voices: {"A": "...", "B": "..."}`로 대화체 화자별 음성 지정 (sapi=음성 이름, piper=모델 경로, openai_compat=voice) |
| `max_chars` / `pause_ms` | 한 번에 합성할 최대 글자 수(기본 300, 문장 경계로 분할) / 조각 사이 무음(기본 250ms) |

백엔드별 설정:

- **sapi** (Windows 내장, 설치 불필요): PowerShell `System.Speech`로 합성. 한국어 음성(예: Microsoft Heami)이
  설치돼 있어야 합니다 (설정 → 시간 및 언어 → 음성). `voice`에 음성 이름, `rate`(-10~10), `powershell`
  (기본 `powershell`, PowerShell 7이면 `pwsh`). 텍스트는 임시 파일로만 전달하고, 고정된 `.ps1`을 `-File`
  인자로 실행하므로 본문이 명령줄이나 스크립트에 끼어들지 않습니다.
- **piper**: `model`(`.onnx` 경로), 선택 `piper`(실행 파일), `piper_args`(리스트). 텍스트는 stdin으로 전달.
  한국어 음성 모델이 있어야 자연스럽습니다.
- **openai_compat**: `POST {base_url}/audio/speech` (`base_url`은 `/v1`까지, 예 `http://127.0.0.1:8880/v1`),
  `model`, `voice`, `api_key`. 서버가 `wav` 응답을 지원해야 합니다 (Kokoro-FastAPI, openedai-speech 등).
  시스템 프록시는 거치지 않습니다.

긴 대본은 문장 단위로 잘라 합성한 뒤 표준 `wave` 모듈로 한 개의 WAV로 이어붙입니다
(조각들의 샘플레이트/채널이 같아야 합니다).

### 폰 팟캐스트 앱으로 구독

```json
"tts": {"enabled": true, "backend": "sapi", "feed": {"enabled": true, "base_url": "http://내PC.tailnet-name.ts.net:8000/"}}
```

`feed.enabled`면 실행 후 `out/podcast.xml`(RSS 2.0 + iTunes 태그, 최근 30개 에피소드)을 갱신합니다.
`out/` 폴더를 Tailscale로 접근 가능하게 서빙하고 폰 팟캐스트 앱에 `.../podcast.xml`을 구독하세요.

```bat
cd /d D:\community-briefing\out
python -m http.server 8000 --bind 100.x.y.z
```

(`100.x.y.z`는 PC의 Tailscale IP. 모든 인터페이스에 여는 `0.0.0.0`은 피하세요.)
일부 앱은 WAV를 싫어할 수 있고 용량이 큽니다(수십 MB). 필요하면 ffmpeg 등으로 mp3 변환을 별도로 거세요.

## 이슈 타임라인 (timeline)

`timeline.enabled: true`면 실행할 때마다 브리핑 글을 **매칭된 키워드별**(`data/timeline/<키워드>.json`)과
**토픽별**(`data/timeline/topic__<토픽>.json`)로 누적(URL 중복 제외)하고, 자체 완결형 뷰어
`out/timeline.html`(외부 파일/CDN 없음)을 다시 만듭니다.
토픽 선택, 날짜별 세로 타임라인, 링크, 출처 배지, 라이트/다크(시스템 설정) 지원, 폰 화면 대응.
`timeline.days`(기본 30)는 뷰어에 보여 줄 기간, `timeline.html`은 `output.dir` 안의 파일명입니다.

```bash
python -m briefing timeline                       # 기록된 토픽/키워드 목록 + timeline.html 재생성
python -m briefing timeline --topic "로컬 LLM"     # 터미널에 해당 타임라인 출력 (일부만 써도 됨)
python -m briefing timeline --topic gguf --days 7
```

## llama-server 연결하기 (Windows + GPU)

```bat
llama-server.exe -m D:\models\Qwen2.5-14B-Instruct-Q4_K_M.gguf -ngl 99 -c 8192 --host 127.0.0.1 --port 8080
```

- 한국어 요약은 한국어를 잘하는 instruct 모델(예: Qwen 계열, EXAONE, Gemma 등)을 추천합니다.
- `-ngl 99`: 모든 레이어를 GPU에 올림. `--api-key 비밀값`을 주었다면 config의 `api_key`에 같은 값을 넣으세요.
- 브리핑 봇을 다른 PC에서 돌린다면 `--host 0.0.0.0`으로 띄우고 `base_url`을 `http://<PC IP>:8080/v1`로.
  (이 경우 방화벽/`--api-key` 설정을 꼭 하세요)
- 확인: `curl http://127.0.0.1:8080/v1/models`
- `<think>...</think>`를 출력하는 추론 모델도 자동으로 그 부분을 제거합니다.

## Windows 작업 스케줄러로 매일 아침 실행

`run_briefing.bat` (예: `D:\community-briefing`에 저장):

```bat
@echo off
cd /d D:\community-briefing
set PYTHONUTF8=1
py -3 -m briefing run --config config.json >> data\run.log 2>&1
```

명령 프롬프트에서 등록 (매일 07:30):

```bat
schtasks /Create /TN "CommunityBriefing" /TR "D:\community-briefing\run_briefing.bat" /SC DAILY /ST 07:30
```

또는 작업 스케줄러 GUI → 기본 작업 만들기 → 매일 07:30 → 프로그램 시작 → `run_briefing.bat`.
llama-server도 아침에 켜져 있어야 LLM 요약이 됩니다 (꺼져 있으면 추출 요약으로 자동 대체).
llama-server를 먼저 띄우는 작업을 07:25에 하나 더 만들어 두는 방법도 있습니다.

## 매너와 주의사항

- 각 사이트의 **이용약관과 robots.txt를 지키세요.** 스크래핑을 금지하는 사이트는 넣지 마세요.
  가능하면 HTML 대신 공식 RSS/API를 사용하세요.
- 요청은 하루 몇 번 정도로 충분합니다. `delay_seconds`/소스별 `delay`를 낮추지 말고, 캐시를 켜 두세요.
- 로그인이 필요한 페이지, 비공개 게시판은 지원하지 않습니다 (의도적으로).
- 수집한 글은 개인 열람용입니다. 재배포하지 마세요.

## 구조

```
briefing/
  cli.py        python -m briefing run|sources|timeline
  pipeline.py   수집 → 기간 필터 → 점수 → 병합 → 중복 제거 → 요약 → 출력 → (타임라인/TTS/알림)
  fetch.py      User-Agent, 호스트별 지연, ETag 캐시, robots.txt
  parse.py      RSS/Atom, HTML 목록(HTMLParser 기반 추출기)
  score.py      키워드 가중치, 뮤트, 댓글 가산점
  store.py      URL/제목 정규화, 병합, SQLite seen 기록
  summarize.py  OpenAI 호환 클라이언트 + 추출 요약 대체
  render.py     Markdown / JSON
  deliver.py    ntfy
  timeline.py   키워드/토픽별 타임라인 JSON + timeline.html 뷰어
  tts.py        대본 생성, TTS 백엔드(sapi/piper/openai_compat), WAV 이어붙이기, 팟캐스트 RSS
```

## 로드맵

- 타임라인: LLM으로 이슈 흐름 요약, 팟캐스트 mp3 변환/ntfy 첨부
- 요약 품질: 본문 페이지까지 가져와 요약 (사이트 약관 허용 시), 클러스터 단위 묶음 요약
- 관심사 학습: 내가 클릭/저장한 글로 키워드 가중치 자동 조정
