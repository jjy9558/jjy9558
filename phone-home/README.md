# phone-home

S21 폰 홈: 서비스 타일 런처 + 토큰 하나 리버스 프록시 + 사장님 책상

북마크 하나, 토큰 하나, `tailscale serve` 하나로 집 서비스 다섯 개(사이드킥, 물건 기억, 3줄 일기, 소리 감시,
자비스)를 폰에서 엽니다. 각 타일에 살아 있는지 점(초록/빨강/노랑)이 붙고, "사장님 책상"에서 오늘의 서류
카드와 결재 대기를 한 화면에 모아 봅니다.

<!-- demo:start -->
<!-- demo:end -->

## 설치

Python 3.12 이상, 외부 패키지 없음. 폴더에서 바로 실행합니다.

```
cd phone-home
python -m phonehome init            # ~/.phone-home/config.json 생성 (클라이언트 토큰 포함)
# config.json 의 services.<이름>.token 에 각 서비스 토큰을 넣는다 (또는 token_env / token_file)
python -m phonehome check           # 뒤쪽 서비스 상태
python -m phonehome serve           # http://127.0.0.1:8790/
tailscale serve --bg 8790           # 폰에서는 https://<pc>.<tailnet>.ts.net/ 로
```

폰에서 처음 한 번 `https://…/#token=<클라이언트 토큰>` 으로 열면 로그인 쿠키(HttpOnly, SameSite=Strict,
30일)가 남고 주소창에서 토큰은 지워집니다. `python -m phonehome token new` 로 토큰을 바꾸면 기존 쿠키는 모두 무효.

## 명령

| 명령 | 하는 일 |
|---|---|
| `serve [--host --port --verbose]` | 서버 실행 (기본 127.0.0.1:8790) |
| `check` | 서비스마다 건강 확인 경로를 찔러 표로 보여 줌 (하나라도 죽으면 종료 코드 1) |
| `token new` / `token show` | 클라이언트 토큰 새로 만들기 / 보기 |
| `init [--force]` | 설정 파일 만들기 |

공통 옵션 `--config <파일>` (또는 환경변수 `PHONE_HOME_CONFIG`). 클라이언트 토큰은 `PHONE_HOME_TOKEN` 으로도 줄 수 있습니다.

## 서비스 설정

기본값은 각 서비스의 server.py 를 읽어 맞췄습니다(그 폴더들은 고치지 않음).

| 이름 | 주소 | 토큰 헤더 | 건강 확인 | 페이지 |
|---|---|---|---|---|
| sidekick | 127.0.0.1:8765 | `X-Sidekick-Token` | `GET /api/ping` | `/` |
| object-memory | 127.0.0.1:8788 | `X-ObjMem-Token` | `GET /api/ping` | `/` |
| voice-journal | 127.0.0.1:8787 | `X-VJ-Token` | `GET /health` | 없음 → 타일에 "한 줄 일기" 입력칸 (`POST /entry`) |
| sound-watch | 127.0.0.1:8770 | `X-SoundWatch-Token` | `GET /api/ping` | `/` |
| jarvis | 127.0.0.1:8800 | `X-Jarvis-Token` + `X-Jarvis-Device: s21` | `GET /api/ping` | `/` |

```json
"services": {
  "sidekick": {"url": "http://127.0.0.1:8765", "token": "...", "path_mode": "prefix"},
  "jarvis": {"token_env": "JARVIS_S21_TOKEN", "headers": {"X-Jarvis-Device": "s21"}},
  "sound-watch": {"path_mode": "passthrough", "listen_port": 8792}
}
```

`services`를 적으면 적은 서비스만 씁니다(이름이 기본 서비스와 같으면 빠진 칸은 기본값으로 채움).
필드: `url, token_header, token | token_env | token_file, headers(추가로 붙일 헤더), health, page,
page_storage, rewrite(기본 ["/api/"]), path_mode(prefix|passthrough), listen_port, timeout, max_body_mb, title, icon, desc, quick`.

### 프록시 규칙

- `/s/<이름>/<경로>` → 뒤쪽 `/<경로>` (prefix 모드). 쿼리는 그대로.
- 클라이언트가 보낸 `X-Phone-Token`, `Authorization`, `Cookie`, 그리고 **모든 서비스의 토큰 헤더**는 버리고
  서버가 그 서비스의 토큰만 붙입니다. 클라이언트 토큰은 뒤쪽으로 넘어가지 않습니다. 뒤쪽의 `Set-Cookie`도 버립니다.
- prefix 모드에서는 HTML 안의 `"/api/…"`·`'/api/…'`·`` `/api/…` `` 와 `href/src/action="/…"`, `Location` 헤더를
  `/s/<이름>/…` 로 고쳐 씁니다. 각 페이지가 토큰 칸이 비면 "토큰 없음" 화면을 띄우므로 `page_storage` 의 키에
  가짜 값(`via-phone-home`)을 넣어 줍니다 — 진짜 토큰은 서버가 붙입니다.
- 고쳐 쓰기로 안 되는 페이지는 `passthrough` 모드: 서비스마다 `listen_port`를 하나 더 열어 경로를 그대로 넘깁니다
  (쿠키는 포트를 가리지 않아서 로그인이 그대로 통함). tailscale 에서는 `tailscale serve --https=8792 8792` 처럼 따로 엽니다.
- 경로 탈출(`/s/../`, `%2e%2e`, 이중 인코딩, 역슬래시, `//`)과 등록 안 된 서비스는 404.
- 본문 한도: `server.max_body_mb`(기본 8MB) 또는 서비스별 `max_body_mb`. 넘으면 413, 뒤쪽으로 보내지 않음.
- 뒤쪽이 꺼져 있거나 시간 초과면 502, 런처 타일 점이 빨강(토큰 거절이면 노랑).
- sidekick 의 NFC 키(`X-Sidekick-Token` 에 매크로 키를 담는 방식)는 프록시를 통하지 않습니다(헤더를 버리므로).

## 사장님 책상 (`/desk`)

- **오늘의 서류**: morning-brief 계약 파일 `morning-brief/out/cards-latest.json`
  (`[{kind, title, detail, due, actions[]}]` 또는 `{"cards": [...]}`; 경로는 `desk.cards_path`)을 마감 빠른 순으로
  종이 더미처럼 보여 줍니다. 파일이 없거나 깨져도 빈 책상으로 200.
  "확인/반려" 도장은 `~/.phone-home/desk-log.jsonl`(`desk.log_path`)에 한 줄 기록만 하고 카드 파일은 건드리지 않습니다.
- **action**: 카드의 `actions` 항목은 **문자열(표시 전용 문장, 그냥 글자로만 보여 줌)** 또는 **`{id, label[, text, url]}` 객체**입니다
  (morning-brief 계약과 같음). 문자열은 어떤 경우에도 실행 id 로 해석하지 않습니다. `url`은 같은 출처 상대경로(`/s/...`)만 링크로
  걸고, 실행 버튼은 객체의 `id` 가 설정 `desk.actions` 화이트리스트에 맞을 때만 나옵니다(기본 빈 목록). 맞지 않으면 글자로만 보이고, 직접 요청하면 403.
  ```json
  "desk": {"actions": {
    "cool-report": {"label": "구독 리포트", "argv": ["python", "-m", "cooloff", "report"], "timeout": 20},
    "paperbutler.done:": {"label": "완료 처리", "argv": ["python", "-m", "paperbutler", "done", "{arg}"], "arg": "digits"}}}
  ```
  키가 `:` 로 끝나면 **접두사 항목**입니다. action id `paperbutler.done:12` 의 접미사 `12` 가 `arg` 형식(`digits` = 숫자 1~12자리 기본,
  `slug` = 영숫자/`-`/`_`)에 통째로 맞을 때만, argv 의 `"{arg}"` 자리에 **한 인자로** 들어갑니다(셸 없음, 인자 일부로 끼우기 불가).
  셸 없이 argv 로만 실행하고 결과 코드를 로그에 남깁니다. 읽기 전용에 가까운 명령만 넣으세요.
- **결재 대기**: s21-sidekick `GET /api/approval/pending` 과 jarvis-hub `GET /api/jobs?status=waiting_approval`.
  승인/반려는 **해당 서비스 API로만** 보냅니다(sidekick `POST /api/approval/decide`, jarvis `POST /api/jobs/<id>/approve|reject`).
  `desk.link_only_patterns`(기본 `rm -rf`, `rm -fr`, `claude.task`)에 걸리는 위험 결재는 버튼 없이 서비스 화면 링크만
  걸고, 서버도 403으로 막습니다. jarvis 승인은 phone-home 이 쓰는 기기(`X-Jarvis-Device`)에 `approve` 권한이 있어야 합니다.

## 데이터와 개인정보

- 설정: `~/.phone-home/config.json` (권한 600으로 씀). 책상 기록: `~/.phone-home/desk-log.jsonl`.
- 외부로 나가는 것: 없음. 설정한 뒤쪽 서비스 주소로만 요청하고 환경변수 프록시는 쓰지 않습니다.
- 기본 바인드 127.0.0.1. 다른 주소로 열 때도 토큰이 없으면 시작하지 않습니다. 요청 로그는 기본으로 끔.
- 로그인 실패가 1분에 10번을 넘으면 잠시 429.

## 공통 기반 (jjkit)

`phonehome/_jjkit/`에 jjkit 복사본(httpkit)이 들어 있습니다. **직접 고치지 말고** 원본 `jjkit/jjkit/`을 고친 뒤 저장소 루트에서 다시 복사하세요.

```
python tools/jjkit_sync.py phone-home
python tools/jjkit_sync.py --check phone-home
```

## 테스트

```
cd phone-home
python -m unittest discover -s tests -v
```

네트워크 없이 돕니다(127.0.0.1 `port=0` 가짜 서비스, 임시 폴더, 가짜 runner).
