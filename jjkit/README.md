# jjkit

이 저장소 프로젝트들이 같이 쓰는 기반 모듈입니다. 표준 라이브러리만, Python 3.12 이상.

각 프로젝트는 혼자 설 수 있어야 하므로 jjkit을 import 경로로 공유하지 않고, **원본 하나를 각 프로젝트
패키지 안 `_jjkit/`로 복사**해서 씁니다. 복사본은 직접 고치지 않습니다. CI가 `--check-all`로 확인합니다.

## 모듈

| 모듈 | 하는 일 | 기준 구현 |
|---|---|---|
| `llm` | OpenAI 호환 chat, vision, JSON 복구 파서, 로컬 주소만 허용, 로컬이면 프록시 끔, opener 주입 | paper-butler, used-pc-judge |
| `notify` | ntfy(JSON 게시 기본, 헤더 모드는 RFC 2047), sidekick `/api/say`, jarvis `notify.say` 작업, 콘솔, `Notifier` 묶음 | jarvis-hub, steam-buddy |
| `textwidth` | 전각 폭 `width`, `pad`, `truncate`, `table`, `md_table` | used-pc-judge |
| `redact` | 토큰/경로/이메일/IP/전화/카드/주민번호/계좌/식별번호 마스킹, `find`로 검사만 | til-drafter + paper-butler |
| `httpkit` | 토큰 인증 `TokenHandler`, `@route`, 본문 제한, 127.0.0.1 기본, `port=0` 테스트 서버 | s21-sidekick |
| `ics` | VEVENT 쓰기(줄 접기, UTC/종일, 알람)와 읽기(TZID, VALARM 무시) | game-calendar, paper-butler, s21-sidekick |
| `claude_logs` | Claude Code jsonl 순회, sidechain 제외, system-reminder 제거 | til-drafter, voice-journal |
| `netutil` | `is_local_url`, `opener_for`, `request_json` (llm, notify가 씀. 자동으로 같이 복사됨) | |

ntfy 한글 제목: 기본은 JSON 게시(`POST https://ntfy.sh/` 본문 `{"topic","title","message",...}`)라서 헤더
인코딩 문제가 없습니다. JSON 게시를 못 쓰는 환경이면 `mode="headers"`로 바꾸면 제목/태그를 RFC 2047
(`=?UTF-8?B?...?=`)로 감싸 보냅니다.

## 쓰는 법 (저장소 루트에서)

```
python tools/new_project.py morning-brief --pkg mbrief --desc "아침 방송국" --modules llm,notify,textwidth
python tools/jjkit_sync.py morning-brief --modules ics     # 모듈 추가
python tools/jjkit_sync.py morning-brief                   # 원본이 바뀐 뒤 갱신
python tools/jjkit_sync.py --check-all                     # 검사 (CI와 같음)
```

프로젝트 코드에서는 `from ._jjkit import llm` 또는 `from mbrief._jjkit.notify import Notifier`.

## 테스트

```
cd jjkit
python -m unittest discover -s tests -v
```

네트워크를 쓰지 않습니다(가짜 opener, 127.0.0.1의 `port=0` 서버, 임시 폴더). `tools/` 두 스크립트의 테스트도 여기 있습니다.
