# game-diary — 게임 스크린샷 AI 앨범·일기

Windows + Steam 게이머가 **로컬 비전 모델(llama-server + VLM, OpenAI 호환)** 로 스크린샷에 캡션을 달고,
게임·세션별 한국어 일기(Markdown)와 정적 HTML 앨범을 만드는 도구입니다.
Python 3.12+ 표준 라이브러리만 사용하며, 외부 네트워크·프록시를 쓰지 않습니다.

## 개인정보 안내
- 스크린샷에는 **채팅, 사용자 이름** 등이 찍혀 있을 수 있습니다.
- 이미지는 설정한 `base_url`(기본 `http://127.0.0.1:8080/v1`)로만 전송되고 캡션은 로컬 sqlite DB에만 저장됩니다. 프록시는 무시합니다.
- `--no-llm` 을 쓰면 서버 호출 없이 템플릿 기반 일기와 앨범만 만듭니다.
- 이미지를 디코딩/변환하지 않습니다. PNG/JPEG 헤더에서 크기만 읽고, 원본 바이트를 base64 로 보냅니다.

## 빠른 시작
```
python -m gamediary config --init
python -m gamediary config --add-source "C:\Program Files (x86)\Steam\userdata"::steam
python -m gamediary config --add-source "%USERPROFILE%\Videos\Captures"::gamebar
python -m gamediary scan
python -m gamediary caption --limit 50
python -m gamediary diary            # 오늘 일기
python -m gamediary diary --week     # 이번 주 일기
python -m gamediary album            # ~/.gamediary/out/album.html
```
llama-server 예: `llama-server -m model.gguf --mmproj mmproj.gguf --port 8080`

## 명령
| 명령 | 설명 |
|---|---|
| `config [--init] [--set KEY=VALUE] [--add-source PATH::KIND]` | 설정 보기/수정. KIND = `steam`, `gamebar`, `folder` |
| `scan` | 소스 폴더 스캔, 해시·타임스탬프·크기 기록. 큰 파일은 사유와 함께 건너뜀 |
| `caption [--limit N] [--no-llm]` | 캡션이 없는 파일만 VLM 호출. 파일 해시로 캐시되어 재실행·중복 파일은 호출하지 않음 |
| `diary [--week] [--date YYYY-MM-DD] [--no-llm]` | `out/diary-날짜.md` 또는 `out/diary-week-월요일.md` |
| `album [--embed-thumbs]` | `out/album.html` |

공통 옵션 `--config 경로` (기본 `~/.gamediary/config.json`, 환경변수 `GAMEDIARY_CONFIG`).

아래 표의 `out/` 은 설정 `out_dir` 폴더를 뜻하며 기본값은 `~/.gamediary/out` 입니다. 옛 위치 `./out/` 에 내용이 있고 설정에 `out_dir` 이 없으면 그것을 계속 쓰고 안내를 출력합니다.

## 소스
- **Steam**: `Steam\userdata\<id>\760\remote\<appid>\screenshots\*.jpg`. userdata 또는 그 하위 아무 폴더나 지정하면 `screenshots` 폴더를 찾아 폴더명에서 appid 를 얻습니다. 게임 이름은 로컬 파일만 사용합니다: 설정의 `game_names` > `names_cache`(기본 `~/.gamediary/games.json`, `{"570": "Dota 2"}`) > `~/.steam-buddy/data.json`. 모르면 `Steam 앱 <appid>`.
- **Game Bar / NVIDIA / 임의 폴더**: 파일명 `Game 2026-11-01 21-30-05.png`, `Elden Ring 2026.11.01 - 21.30.05.01.png` 에서 시간과 게임 이름 추출.
- 시간은 Steam `YYYYMMDDHHMMSS_N.jpg` → 일반 날짜시간 패턴 → 파일 수정시각 순으로 결정합니다.

## 큰 파일과 썸네일
PIL 없이는 축소할 수 없으므로 `max_mb`(기본 8MB)보다 큰 파일은 건너뛰고 `scan` 에 표시합니다.
Steam 이 만든 `screenshots\thumbnails\` 는 자동 탐지됩니다(추가 폴더는 `thumbs_dir`).
`caption_source` 를 `thumb` 으로 바꾸면 캡션 때 썸네일을 보내 용량·시간을 줄입니다:
`python -m gamediary config --set caption_source=thumb`

## 일기
게임별로 시간순 정렬 후 촬영 간격이 `session_gap_hours`(기본 2시간)보다 길면 새 세션입니다.
세션마다 LLM 이 캡션을 엮어 짧은 일기체 문단을 쓰고(실패/`--no-llm` 이면 템플릿), notable 장면 3~5장을 하이라이트로 넣습니다. notable 이 3장 미만이면 다른 장면으로 보충합니다.

## 앨범
`out/album.html` 은 이미지를 **상대경로로만 참조**하는 단일 HTML 입니다(원본을 내장하지 않음 — 이미지 폴더를 옮기면 깨집니다). 게임 필터, 검색, 태그 칩, ★ 주요 장면 필터, 날짜 타임라인, 라이트박스(←/→/Esc), 라이트/다크 전환을 지원합니다. `--embed-thumbs` 는 Steam 썸네일(300KB 이하)만 data URI 로 내장해 그리드에 쓰고, 클릭하면 원본을 엽니다. 캡션은 `textContent` 로만 렌더링되어 HTML 주입이 불가능합니다.

## 설정 키
`base_url, model, api_key, timeout, max_mb, caption_source, thumbs_dir, session_gap_hours, sources, game_names, names_cache, use_steam_buddy, db_path, out_dir`

## 테스트
```
python3 -m unittest discover -s tests -v
```
테스트는 최소 PNG/JPEG 바이트를 직접 생성하고 가짜 LLM 을 사용하므로 서버가 필요 없습니다.
