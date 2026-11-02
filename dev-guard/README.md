# dev-guard - 개발자 안전장치 팩

Claude Code 를 무인(unattended)으로, 서브에이전트까지 굴려 쓰는 개발자를 위한 **작고 독립적인 안전장치 5종**.
Python 3.9+, **표준 라이브러리만** 사용, 네트워크 불필요(ntfy 알림은 선택), 폰/음성 승인 없음.
각 구성요소는 **따로 써도 됩니다.** (음성 승인으로 `rm -rf` 같은 위험 명령을 막는 별도 프로젝트와는 독립적이며 서로 보완 관계입니다.)

<!-- demo:start -->
![데모](docs/demo.svg)
<!-- demo:end -->

| # | 이름 | 무엇을 | 어디에 거는지 |
|---|------|--------|-------------|
| 1 | .env 커밋 도둑 경보 | 스테이징된 비밀(키/토큰/.env/개인키) 커밋 차단 | git `pre-commit` |
| 2 | 금요일 배포 차단기 | 금요일 15시 이후 main/master/release/* 푸시 차단 | git `pre-push` |
| 3 | 에이전트 파업 | 같은 명령 3연속 실패 / 같은 파일 되돌림 반복 시 다음 시도 거부 | Claude Code hooks |
| 4 | 지갑 고발장 | 로컬 트랜스크립트로 일별/세션별 토큰·비용 집계, 한도 알림 | CLI + Claude Code `Stop` 훅 |
| 5 | settings-snippet (+ --merge) | `.claude/settings.json` 에 붙일 hooks 블록 출력 | CLI |

## 빠른 시작

```bash
cd dev-guard                      # (이 폴더를 아무 곳에 두고 절대경로로 호출해도 됩니다)
python -m unittest discover -s tests -v      # 테스트

# 1+2) 어떤 저장소에든 git 훅 설치 (기존 훅은 보존/체이닝)
python -m devguard install-git-hooks /path/to/your/repo

# 3+4) Claude Code 훅 블록 출력 -> settings.json 에 직접 붙여넣기 (자동 수정 안 함)
python -m devguard settings-snippet > hooks-snippet.json
```

선택 사항: `pip install -e .` 하면 어디서나 `devguard ...` / `python -m devguard ...` 로 호출할 수 있습니다.
설치 없이 쓰려면 `python /절대경로/dev-guard/devguard_run.py <명령>` 형태를 쓰세요 (훅이 이 방식을 씁니다).

**Windows**: git 훅은 Git for Windows 의 `sh` 로 실행되고, Claude Code 훅도 Git Bash 경로 규칙을 따르므로 경로는 슬래시(`C:/...`)로 출력됩니다.
`python` 이 PATH 에 없다면 `install-git-hooks --python "C:/Path/to/python.exe"` / `settings-snippet --python ...` 로 지정하세요.
콘솔 인코딩(cp949 등)에서 한글이 깨져도 죽지 않도록 출력은 `errors=replace` 로 처리하며, 훅이 내보내는 JSON 은 ASCII 로 이스케이프합니다.

---

## 설정 파일

우선순위: 기본값 < `~/.devguard/config.json` (전역, `DEVGUARD_HOME` 으로 위치 변경) < 저장소의 `.devguard.json`.
전체 예시는 [`.devguard.example.json`](.devguard.example.json). 잘못된 JSON 은 조용히 무시됩니다.

---

## 1) .env 커밋 도둑 경보 (pre-commit)

```bash
python -m devguard install-git-hooks [repo]         # 설치 (재실행해도 안전: 갱신만 함)
python -m devguard uninstall-git-hooks [repo]       # 제거 (보존해둔 기존 훅 복원)
python -m devguard scan 파일1 파일2                 # 훅 없이 파일만 검사해 보기
```

- 설치 시 `.git/hooks/pre-commit` (실제 위치는 `git rev-parse --git-path hooks`, `core.hooksPath` 존중)에 작은 `sh` 스크립트를 씁니다.
- **기존 훅이 있으면** `pre-commit.devguard-orig` 로 옮겨 보존하고, devguard 검사 통과 후 **원래 훅도 실행**합니다 (실패하면 커밋 중단). 두 번 설치해도 중복 체이닝되지 않습니다.
- 검사 대상은 `git diff --cached -U0` 의 **추가된 줄만**입니다 (이미 있던 줄/삭제된 줄은 안 막음). git 은 인자 리스트로 호출(쉘 미사용).
- 파일 이름 규칙: `.env`, `.env.*`, `*.env`, `id_rsa`/`id_ed25519` 등, `*.pfx`/`*.p12`/`*.jks`, `.netrc`, `.git-credentials`.
  `.env.example/.sample/.template/.dist/.defaults` 는 이름으로는 허용(내용은 검사).
- 탐지 규칙:

| 규칙 이름 | 대상 |
|-----------|------|
| `private-key` | `-----BEGIN ... PRIVATE KEY-----` |
| `anthropic-key` | `sk-ant-...` |
| `openai-key` | `sk-...`, `sk-proj-...` |
| `stripe-live-key` | `sk_live_`, `rk_live_` |
| `github-token` / `github-fine-grained` | `ghp_ gho_ ghu_ ghs_ ghr_` / `github_pat_` |
| `slack-token` / `slack-webhook` / `discord-webhook` | `xox*-`, `xapp-` / 웹훅 URL |
| `aws-access-key-id` | `AKIA...`, `ASIA...` 등 |
| `google-api-key` | `AIza...` |
| `jwt` | `eyJ....eyJ....sig` |
| `url-credentials` | `scheme://user:비밀번호@host` |
| `generic-secret` | `*_KEY / *_TOKEN / *_SECRET / PASSWORD` 류 변수에 대한 높은 엔트로피 값 할당 |

- **오탐 방어**: 플레이스홀더(`your_..`, `example`, `xxxx`, `changeme`, `${VAR}`, `<...>`, `AKIAIOSFODNN7EXAMPLE`, jwt.io 예제 등), UUID, 해시처럼 보이는 hex(변수 이름이 cache/commit/sha/hash 류일 때), 환경변수 이름 값(`GITHUB_TOKEN_2`), 함수 호출/참조(`os.environ[...]`, `getpass()`), 락파일(`package-lock.json`, `yarn.lock`, `poetry.lock`, `go.sum` ...)과 `*.min.js`, 바이너리 확장자는 건너뜁니다. 엔트로피 임계값은 `secrets.min_entropy`.
- 차단 시 한글 메시지로 **파일:줄 번호와 마스킹된 값**(예: `sk-ant****Xk9Q`)만 보여줍니다. 전체 값은 절대 출력하지 않습니다.
- 오탐 예외 처리:
  - 줄 끝에 `# devguard:allow` (또는 `// devguard:allow`, 어떤 주석 형식이든 문자열만 있으면 됨)
  - 저장소 루트 `.devguardignore` ([예시](.devguardignore.example)): 경로/글롭, `rule:이름`, `line:문자열`
  - 급할 때: `DEVGUARD_SKIP=1 git commit ...` 또는 `git commit --no-verify`
- 검사 도중 devguard 자체에서 예외가 나면 경고만 내고 커밋은 통과시킵니다 (fail-open).
- 한계: 정규식/엔트로피 기반이라 100% 가 아닙니다. 짧은 평문 비밀번호(예: `pw = "abc"`)나 비밀번호에 `$ ( ) { }` 가 섞인 경우는 놓칠 수 있습니다. 이미 푸시된 키는 **반드시 폐기/재발급**하세요.

## 2) 금요일 배포 차단기 (pre-push)

기본: **금요일 15:00 이후(로컬 시각)** `main`, `master`, `release/*` 로 가는 푸시 차단. 웃긴 한글 메시지(날짜별로 다른 문구).

```json
// .devguard.json (또는 ~/.devguard/config.json)
{"friday": {"days": ["thu", "fri"], "after": "14:00", "before": "24:00",
            "all_day_days": ["sat", "sun"], "branches": ["main", "release/*", "prod"], "tags": ["v*"]}}
```
- `days`: `mon..sun` / `월..일` / 0~6(월=0). `all_day_days`: 하루 종일 막을 요일. 태그는 `tags` 를 지정했을 때만 보호.
- 우회(둘 다 기록은 안 남음. 책임은 본인에게):
  - `DEVGUARD_FRIDAY_OK=1 git push ...` (PowerShell: `$env:DEVGUARD_FRIDAY_OK=1; git push ...`)
  - `python -m devguard push --i-know origin main` - `git push` 에는 임의 옵션을 붙일 수 없어서(`git push --i-know` 는 git 이 거부) **래퍼 명령**으로 제공합니다. 쓰기 편하게 `git config alias.fpush '!python /경로/devguard_run.py push --i-know'` 같은 별칭을 직접 만들어도 됩니다 (devguard 가 자동으로 건드리지 않음).
- 푸시되는 **원격 ref 이름**(stdin) 기준으로 판단하므로 `git push origin feature:main` 도 막습니다. 삭제 푸시도 대상 브랜치가 보호 대상이면 막습니다.
- 시험용: `DEVGUARD_NOW=2025-01-03T16:00` 으로 가짜 시계.

## 3) 에이전트 파업 (Claude Code hooks)

한 명령 `python devguard_run.py strike` 가 `PreToolUse` / `PostToolUse` / `PostToolUseFailure` 를 모두 처리합니다 (`hook_event_name` 으로 구분).

동작:
1. **같은 명령 연속 실패** - 명령을 정규화(공백 접기, `2>&1`, 끝의 `| tail/head/tee ...` 제거)해 세션별로 연속 실패 횟수를 셉니다. 성공하면 0 으로 리셋. 기본 3번째 실패에서 파업 선언.
2. **같은 파일 되돌림** - `Edit`/`MultiEdit` 의 `old_string<->new_string` 이 정확히 거꾸로 적용되거나, `Write` 내용이 이전 내용으로 돌아가면 "되돌림" 1회. 기본 3회에서 파업.
3. 파업 선언 시 `PostToolUse` 출력: `{"decision":"block","reason":"같은 실패 3번째. 파업합니다. 접근 방식을 바꾸세요."}`
   (`strike.action=stop` 이면 `{"continue":false,"stopReason":...}` 로 Claude 를 아예 멈춤. 무인 실행에서 사람 개입을 강제하고 싶을 때)
4. 그 **다음 동일 시도**는 `PreToolUse` 에서 거부:
   ```json
   {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
     "permissionDecisionReason": "같은 실패 3번째. 파업합니다. 접근 방식을 바꾸세요. (...)"}}
   ```
5. 해제: 파일을 수정(`Edit/Write` 성공)하면 명령 파업은 풀립니다("접근을 바꿨다"). 그 외에 거부 `max_denies`(3)회 후 자동 해제, `ttl_seconds`(1800) 후 자동 해제(무인 실행 교착 방지), 수동은 `python -m devguard strike-reset`.
6. 상태: 세션(서브에이전트는 `agent_id` 가 있으면 별도)마다 `tempdir/devguard-strike/<session_id>.json` (`DEVGUARD_STATE_DIR` 로 변경). 조회: `python -m devguard strike-status`.
7. **절대 Claude 를 망가뜨리지 않음**: 잘못된 JSON, 모르는 모양, 상태 파일 손상/쓰기 실패, 내부 예외 모두 "허용"(종료코드 0, 출력 없음). 상태는 임시파일 + `os.replace` 로 원자적 저장. 병렬 서브에이전트가 동시에 쓰면 드물게 카운트가 한 번 유실될 수 있습니다(파업이 한 박자 늦어질 뿐).
8. 폴백: `--exit2` / `DEVGUARD_STRIKE_OUTPUT=exit2` / `strike.output="exit2"` 이면 JSON 대신 **종료코드 2 + stderr 메시지**로 신호합니다 (문서상 PreToolUse 는 호출 차단, PostToolUse 는 Claude 에 stderr 전달).
9. 끄기: `DEVGUARD_STRIKE_OFF=1` 또는 `strike.enabled=false`. 집계 제외 명령은 `strike.ignore_regex`.

설정 (`strike` 섹션): `threshold`(3), `edit_flips`(3), `max_denies`(3), `ttl_seconds`(1800), `action`(`deny`|`stop`), `output`(`json`|`exit2`), `ignore_regex`.

### 에이전트 파업 검증 방법 (중요)

Hooks 의 JSON 입출력 형식은 Claude Code 버전에 따라 바뀔 수 있고, 이 구현은 **문서화된 형식으로 알고 있는 내용**에 맞췄습니다. 실제 환경에서 꼭 확인하세요.

1. **payload 모양 확인**: 훅 명령 앞에 환경변수를 붙이거나(`DEVGUARD_STRIKE_DUMP=/tmp/dg-payloads.jsonl`), settings 의 `env` 에 넣고 Claude Code 에서 아무 Bash 를 몇 번 실행(`false` 포함). 파일에 쌓인 JSON 에서 확인:
   - `hook_event_name`, `session_id`, `tool_name`, `tool_input.command` 가 있는지
   - **실패한 Bash 가 어느 이벤트로 오는지**: `PostToolUse` 의 `tool_response`(종료코드 필드가 있는지) vs `PostToolUseFailure`(`error` 필드). 이 구현은 둘 다 처리합니다. `PostToolUse` 에 종료코드 필드가 없고 `PostToolUseFailure` 도 안 온다면 실패 카운트가 안 올라가므로, `strike.bash_failed()` 를 그 모양에 맞게 고치세요.
2. **훅 등록 확인**: Claude Code 안에서 `/hooks`, 또는 `claude --debug` 로 훅 실행/출력 로그 확인.
3. **엔드투엔드**: 새 세션에서 Claude 에게 "`bash -c 'exit 1'` 을 그대로 3번 실행해 봐" 라고 시킨 뒤 4번째 시도에서 거부 메시지("같은 실패 3번째. 파업합니다...")가 보이는지 확인. `python -m devguard strike-status` 로 카운터 확인.
4. **JSON 이 무시되는 것 같다면**: `DEVGUARD_STRIKE_OUTPUT=exit2` 로 폴백(종료코드 2 + stderr)을 시험.
5. **수동 시뮬레이션** (Claude 없이 우리 쪽 입출력만 확인):
   ```bash
   for i in 1 2 3; do echo '{"session_id":"t","hook_event_name":"PostToolUse","tool_name":"Bash","tool_input":{"command":"make"},"tool_response":{"exit_code":1}}' | python devguard_run.py strike; done
   echo '{"session_id":"t","hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"make"}}' | python devguard_run.py strike
   python devguard_run.py strike-reset
   ```

## 4) 지갑 고발장 (사용량 감시)

`~/.claude/projects/**/*.jsonl` (`CLAUDE_CONFIG_DIR` 존중; 서브에이전트 하위 폴더 포함)의 각 줄에서 `message.usage` (`input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, 있으면 `cache_creation.ephemeral_5m/1h_input_tokens`)와 `message.model` 을 읽어 **로컬 날짜별 / 세션별 / 모델별**로 합산합니다.

```bash
python -m devguard wallet report              # 오늘
python -m devguard wallet report --days 7     # 최근 7일
python -m devguard wallet report --json
python -m devguard wallet alert               # 오늘 지출 vs 한도 (콘솔)
python -m devguard wallet alert --ntfy https://ntfy.sh/내-비밀-토픽   # 선택: 폰 알림
python -m devguard wallet alert --strict      # 한도 초과면 종료코드 1 (cron/스크립트용)
python -m devguard wallet init                # ~/.devguard/config.json 템플릿 생성 (이미 있으면 안 건드림)
```

- 같은 응답이 여러 줄/여러 파일(세션 재개 시 복사)에 있어도 `(message.id, requestId)` 로 **중복 제거**합니다. 깨진 줄/읽기 실패 파일은 건너뜁니다. 오늘 보고서는 오늘 수정된 파일만 읽어 빠릅니다.
- **단가표는 예시(PLACEHOLDER)입니다.** `devguard/wallet.py` 의 `PLACEHOLDER_PRICES` 는 실제 가격이 아닐 수 있습니다. **공식 가격 페이지를 보고** `~/.devguard/config.json` 에 덮어쓰세요 (USD per 1M tokens):
  ```json
  {"wallet": {"prices_confirmed": true, "daily_limit_usd": 20, "warn_ratio": 0.8,
              "prices": {"opus": {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0, "cache_write_1h": 0.0}}}}
  ```
  (위 0.0 은 자리표시일 뿐이니 실제 값으로 바꾸세요.) 키는 모델명에 **포함된 부분 문자열**로 매칭하고 가장 긴 키가 우선합니다 (`"opus-4-5"` 를 따로 두면 `opus` 보다 우선). 매칭 없는 모델은 `default` 단가가 적용되고 보고서에 표시됩니다. `prices_confirmed` 를 `true` 로 바꾸기 전까지 보고서에 경고가 붙습니다.
- 한도: `wallet.daily_limit_usd`(0 이면 미설정), `warn_ratio`(기본 0.8 -> 경고). 상태는 `ok / warn / over`.
- ntfy: 같은 날 같은(혹은 더 낮은) 수준은 한 번만 보냅니다 (`~/.devguard/wallet-state.json`). HTTP 헤더 제목은 ASCII, 본문만 한글(UTF-8). 전송 실패는 조용히 무시. 네트워크는 이때만 사용.
- **Stop 훅**: `python devguard_run.py wallet stop-hook` -> `{"systemMessage": "[devguard 지갑] 오늘 $12.34 / 한도 $20.00 (62%) | 이 세션 $3.21"}` 한 줄을 출력(Claude 를 막지 않음, `decision` 안 냄). 예외가 나면 아무 것도 출력하지 않고 종료코드 0.
- 한계: 이 값은 **로컬 로그 기반 추정치**입니다. 구독제(정액) 사용자라면 실제 청구액이 아니라 "API 였다면" 환산액이며, 로그 형식이 바뀌면 파서를 고쳐야 합니다.

### 야근 수당 명세서 (`wallet payslip`)

```bash
python -m devguard wallet payslip --month 2026-05              # devguard-payslip-2026-05.html 생성
python -m devguard wallet payslip --month 2026-05 --out -      # stdout 으로
```
Claude 가 혼자 일한 시간을 월급 명세서 모양의 자체 완결 HTML(외부 리소스 없음) 한 장으로 만듭니다. 세션별로 응답 타임스탬프 사이 15분 이내의 간격만 이어 붙여 시간을 재고, 평일 09~18시는 정규, 그 외 야근, 22~06시 심야, 주말 특근으로 나눕니다. 토큰과 환산 청구액은 위 지갑 고발장과 같은 파서·단가표를 쓰며, **단가가 예시값이면 명세서에도 경고가 붙습니다.** 시간·금액 모두 추정치이고 농담용입니다.

## 5) settings-snippet

```bash
python -m devguard settings-snippet                 # strike + wallet
python -m devguard settings-snippet --only strike   # 파업만
python -m devguard settings-snippet --no-stop       # Stop 훅 제외
python -m devguard settings-snippet --python "C:/Python312/python.exe"
python -m devguard settings-snippet --with-sidekick C:/dev/s21-sidekick/hooks/claude_pretooluse.py
python -m devguard settings-snippet --with-nagger [PATH]
```
기본은 출력만 합니다. stdout 에는 JSON 만, 안내는 stderr 로 나가므로 `> snippet.json` 이 안전합니다. `PostToolUseFailure` 이벤트를 모르는 버전이면 그 항목만 지우세요.

- `--with-sidekick PATH`: s21-sidekick 의 위험 명령 결재 훅(`Bash|PowerShell`, timeout 60)을 같은 hooks 블록에 넣습니다. **PreToolUse 순서는 위험 명령 결재 -> strike -> 나머지** 입니다.
- `--with-nagger [PATH]`: life-nagger 의 Claude Code 훅을 Stop 이벤트(지갑 한 줄 뒤)에 넣습니다. 현재 life-nagger 에는 이런 훅이 없어서, PATH 없이 쓰면 형제 폴더 `life-nagger/hooks/` 를 찾아보고 없으면 안내만 하고 건너뜁니다.

### 기존 settings.json 에 병합 (`--merge`)

```bash
python -m devguard settings-snippet --with-sidekick PATH --merge ~/.claude/settings.json --dry-run   # diff 만
python -m devguard settings-snippet --with-sidekick PATH --merge ~/.claude/settings.json             # 실제 병합
```
- **지정한 파일 하나만** 읽고 씁니다 (다른 settings 는 건드리지 않음). 파일이 없으면 새로 만듭니다.
- 변경이 있을 때만 쓰며, 쓰기 전에 `settings.json.bak` 을 만듭니다 (이미 있으면 `.bak.1`, `.bak.2` ... 로 덮어쓰지 않음). 임시 파일에 쓴 뒤 교체합니다.
- 알 수 없는 키와 기존 훅은 그대로 두고, 같은 이벤트에 같은 `command` 가 이미 있으면 추가하지 않으므로 **몇 번 실행해도 결과가 같습니다**(두 번째는 "변경 없음", 백업도 안 만듦). 새 PreToolUse 항목은 기존 항목 앞에 들어갑니다.
- JSON 이 깨졌거나 `hooks` 모양이 이상하면 파일을 그대로 두고 종료코드 1.
- `--dry-run`(`--merge` 와 함께만 허용)은 unified diff 만 보여 주고 아무 것도 쓰지 않습니다.

## 6) 권한 소음 측정기 (`permissions-report`)

```bash
python -m devguard permissions-report                    # ~/.claude/projects/**/*.jsonl 전체
python -m devguard permissions-report --days 30 --min-count 5 --top 15 --json
```
tool_use 를 도구별 / Bash 명령 접두어별(`git status`, `npm run` 처럼 하위 명령 포함, `&&` `|` `;` 로 이은 명령은 나눠서)로 세어 `permissions.allow` 후보(`Bash(git status:*)`, `Read` ...)를 **제안만** 출력합니다. `rm`, `sudo`, `curl`, `git push` 같은 위험해 보이는 것은 "검토 필요" 로 따로 빼며, **settings 는 절대 수정하지 않습니다.** 적용은 직접 하세요.

---

## 파일 구성

```
devguard/            scanner.py precommit.py friday.py strike.py wallet.py snippet.py permissions.py payslip.py githooks.py config.py cli.py common.py
devguard_run.py      설치 없이 절대경로로 호출하는 런처 (훅이 사용)
tests/               unittest (178개)
.devguard.example.json  .devguardignore.example
```

## 테스트

```bash
cd dev-guard && python3 -m unittest discover -s tests -v
```
임시 git 저장소(`git init`)를 만들어 실제 `git commit` / `git push` 로 훅을 돌리는 테스트를 포함합니다. 가짜 토큰은 실행 시 생성하므로 소스에 비밀 같은 리터럴이 없습니다. 네트워크는 쓰지 않습니다.

## 알아둘 점

- git 훅 파일은 저장소별 로컬 설정이라 커밋되지 않습니다. 팀 공유는 각자 `install-git-hooks` 를 실행하세요.
- `git commit --no-verify` / `git push --no-verify` 는 git 자체 기능이라 막을 수 없습니다. devguard 는 사고 방지용이지 보안 경계가 아닙니다.
- 실제 Windows 에서의 동작(Git Bash 경로, `python` 해석)은 이 개발 환경(Linux)에서 직접 검증하지 못했습니다. 문제가 있으면 `--python` 옵션과 `DEVGUARD_STRIKE_DUMP` 로 확인하세요.
