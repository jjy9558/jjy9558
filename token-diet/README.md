# token-diet

토큰 다이어트: 세션 로그 절감 포인트, MCP 체중계, 반복 프롬프트, CLAUDE.md 린터

<!-- demo:start -->
<!-- demo:end -->

## 설치

Python 3.12 이상, 외부 패키지 없음. 폴더에서 바로 실행합니다. 선택: `pip install .` 하면 `token-diet scan` 처럼 쓸 수 있습니다.

## 명령

```
python -m tokendiet scan [경로] --md out.md --html out.html     # 반복 읽기 / 큰 출력 / 비싼 도구 / 재시도 루프
python -m tokendiet mcp-weigh [settings.json .mcp.json tools.json] [--logs 경로]
python -m tokendiet repeats [경로] --min-count 3                # 반복 프롬프트 -> 스킬/훅 후보
python -m tokendiet claude-md-lint CLAUDE.md [--root 폴더]
python -m tokendiet route [--config prices.json] [--runs 100]   # 작업 유형별 모델 배정(단가는 설정 파일에서만)
python -m tokendiet baton [--session ID | --latest] [--project 폴더] [--cap 2000] [--out baton.md]   # 세션 바톤(인수인계 메모)
python -m tokendiet checkup [저장소 폴더] [--run] [--timeout 300] --md out.md --html out.html            # 건강검진표
python -m tokendiet report --md all.md --html all.html          # 위 전부 합친 한국어 종합 리포트
```

경로를 생략하면 `~/.claude/projects`(또는 `CLAUDE_CONFIG_DIR`)를 읽습니다. `--md/--html` 없으면 화면에 마크다운으로 출력합니다. HTML은 CSS 인라인, 외부 리소스 없는 한 파일입니다.

## 무엇을 찾나

| 명령 | 내용 |
|---|---|
| scan | 사이에 수정(Edit/Write) 없이 같은 파일·같은 범위를 다시 읽은 호출, 결과가 큰 호출(기본 2000 토큰 추정 이상), 도구별 비용 순위, 같은 도구가 연속 3회 실패한 루프, `message.usage` 실측 합계(같은 message.id는 한 번만) |
| mcp-weigh | 서버별 도구 정의 토큰 추정과 순위 |
| repeats | 단어 집합 Jaccard 군집, 2개 세션 이상에서 3회 이상 반복된 지시 |
| claude-md-lint | 크기(200줄/4000 토큰 추정), 중복 규칙, 모순 의심(같은 주제에 긍정/부정 키워드), 빈 섹션, 없는 경로, 300자 초과 줄 |
| route | 작업 유형별 small/mid/large 추천과 100회 비용 |

## baton / checkup

- **baton**: 트랜스크립트 한 개에서 목표(첫 요청 + 이후 "대신/바꿔" 류 변경), 지금까지(마지막 어시스턴트 요약 3개, Edit/Write 한 파일), 남은 일("남은/TODO/다음 단계" 문장과 `FAIL:` 테스트 이름), 주요 Bash 명령, "다음 세션 첫 메시지" 블록을 한국어 md로 만듭니다. 기본은 가장 최근 세션이고 `--project`는 폴더 이름 규칙 또는 기록된 cwd로 거릅니다. jjkit redact로 마스킹하고 `--cap`(기본 2,000자)을 넘으면 본문을 줄이되 첫 메시지 블록은 남깁니다. 문장 탐지는 키워드 휴리스틱이라 놓치거나 잘못 잡을 수 있습니다.
- **checkup**: 테스트 명령 감지(run_all_tests.py, pytest 설정, unittest), git 상태(`git status --porcelain`만, 읽기 전용), 큰 파일(기본 1024KB), TODO/FIXME 개수, README 없는 하위 폴더(.py가 있는 폴더만), `tools/jjkit_sync.py`가 있으면 `--check-all`. 테스트는 `--run`을 줄 때만 인자 리스트(shell 없음)로 실행하고 `--timeout`을 적용합니다. 모든 파일은 읽기만 합니다. 리포트는 ✅/⚠️ 한국어 md와 자기완결 HTML입니다.

## 추정 방법 (정직하게)

- **토큰 = 문자 수 / 4** 입니다. 한글은 실제로 더 많이 나올 수 있어 과소 추정일 수 있습니다. 리포트의 모든 추정 값에 "(추정)"을 붙였고, `message.usage`에서 읽은 값만 "실측"입니다.
- 이미지 결과는 1장 = 1500 토큰으로 가정했습니다. 큰 출력의 절감 상한은 "각 출력을 500 토큰까지 줄일 수 있다"는 가정입니다.
- 출력은 이후 모든 턴의 컨텍스트에 남아 캐시 읽기로 반복 계산되므로, 실제 영향은 단발 추정보다 클 수 있습니다(여기서는 모델링하지 않음).
- **MCP 체중계**: 서버를 띄우지 않으므로 디스크에 스키마가 있을 때만 정확합니다. 순서는 (A) `tools` 정의가 있는 JSON이면 compact JSON 문자 수/4, (B) 도구 수만 알거나 로그(`--logs`)에서 `mcp__서버__도구` 호출이 관찰되면 도구 수 x `--tokens-per-tool`(기본 350, 가정), (C) 설정에 서버만 있으면 서버당 `--tools-per-server`(기본 10, 가정) 도구로 계산합니다. 어느 방법인지 표의 마지막 열에 표시됩니다. Claude Code의 `~/.claude.json`에는 스키마가 없고, 커넥터/플러그인 서버는 설정에 없을 수도 있습니다. 또 도구가 많으면 ToolSearch가 정의 로딩을 미루므로 실제 상시 비용은 더 작을 수 있습니다. 실제 정의를 덤프한 `tools.json`(도구 목록 JSON)을 주면 가장 정확합니다.
- **CLAUDE.md 린트**는 휴리스틱입니다. 한국어 조사 처리는 거칠어서 오탐/미탐이 있습니다. 지적은 "의심"이며 사람이 확인하세요.
- `route`의 단가는 코드에 없습니다. `prices.example.json`은 가짜 자리표시자 형식 예시이고, 실제 단가는 직접 채우세요.

## 데이터와 개인정보

- 읽는 곳: Claude Code 로그(`~/.claude/projects/**/*.jsonl`), `~/.claude.json` 등 MCP 설정, 지정한 CLAUDE.md. **전부 읽기 전용**이며 어떤 파일도 수정하지 않습니다(리포트는 `--md/--html`로 지정한 곳에만 씁니다).
- 서브에이전트(sidechain) 줄과 `<system-reminder>` 블록은 제외합니다.
- 리포트 출력 직전에 jjkit `redact`로 토큰/키/경로/이메일 등을 마스킹하고, 사용자 원문 프롬프트는 120자까지만 싣습니다. 그래도 공유 전에 한 번 훑어보세요.
- 외부로 나가는 것: 없음(네트워크 미사용).

## 같은 저장소의 관련 도구 (중복 구현 안 함)

- 사용량 지갑(일별 토큰/비용, 한도 경고): [`dev-guard/`](../dev-guard/README.md)의 `wallet`
- 권한 허용 목록 추천(승인 클릭 줄이기): [`dev-guard/`](../dev-guard/README.md)의 `permissions`

token-diet은 "무엇이 토큰을 낭비하는가"(행동 패턴, MCP 정의, 지시문)를 보고, dev-guard는 "얼마나 썼나/무엇을 허용하나"를 봅니다.

## 공통 기반 (jjkit)

`tokendiet/_jjkit/`에 jjkit 복사본(claude_logs, redact, textwidth)이 들어 있습니다. **직접 고치지 말고** 원본 `jjkit/jjkit/`을 고친 뒤 저장소 루트에서 다시 복사하세요.

```
python tools/jjkit_sync.py token-diet
python tools/jjkit_sync.py --check token-diet
```

코드에서는 `from ._jjkit import claude_logs` 처럼 씁니다.

## 테스트

```
cd token-diet
python -m unittest discover -s tests -v
```

네트워크 없이 돕니다(가짜 opener와 픽스처 사용).
