# 1차 프로토타입 점검표

점검일: 2026-11-04. Linux 컨테이너, Python 3.13. 실제 HOME 대신 임시 HOME(`HOME`/`USERPROFILE`/`APPDATA`/`LOCALAPPDATA` 덮어쓰기)에서 실행했고, 외부 네트워크는 일부러 막았습니다.

## 한눈에 보기

| 항목 | 결과 |
|---|---|
| `--help` (36개 패키지, 하위 명령까지 약 700번) | 트레이스백 **1건**: `paperbutler budget set --help` |
| 오프라인 스모크 (프로젝트마다 1~4개 명령, 약 110번) | 트레이스백 0건. 오류 2건: steam-buddy `buy` 가 오프라인이면 실패, `life-court hello` 가 없는 명령 |
| 전체 테스트 `python run_all_tests.py` | 37개 프로젝트 모두 통과 (3,223개 테스트, 166초) |
| homelab `init → doctor → up → status → run → down` (깨끗한 임시 HOME) | 서비스 7개 모두 켜지고 건강 확인 정상, doctor ❌ 0개 |
| README 상대 링크 | 깨진 링크 없음 |
| README 의 Python 버전 | 20개 README 줄과 6개 pyproject 가 아직 3.9+ (루트 README 와 CI 는 3.12+) |

| 성숙도 | 개수 | 프로젝트 |
|---|---|---|
| 바로 사용 가능 | 14 | cool-off, decision-pet, demo-maker, house-detective, kakao-wrapped, life-court, shorts-feedback, story-factory, sub-vocab, taste-map, til-drafter, token-diet, voice-journal, homelab-e2e |
| 실제 기기·서비스 확인 필요 | 17 | homelab, s21-sidekick, phone-home, jarvis-hub, object-memory, sound-watch, life-nagger, pc-doctor, morning-brief, community-briefing, game-calendar, game-diary, gm-trpg, hangul-patcher, paper-butler, shorts-linter, steam-buddy |
| 임시값 교체 필요 | 5 | dev-guard, used-pc-judge, sell-studio, sub-pipeline, gacha-calc |
| 미완성 | 0 | - |

"바로 사용 가능"이어도 아래 "1차 프로토타입 막는 문제"에 걸린 프로젝트가 있습니다(예: story-factory F-1, life-court README 첫 명령).
각 프로젝트는 자기 폴더에서 실행합니다(`cd <폴더>` 후 `python -m <패키지>`). `jjkit` 은 공통 기반이라 표에서 뺐고, 테스트 126개는 통과했습니다.

## 프로젝트별 요약

| 프로젝트 | 상태 | 먼저 해볼 명령 | 직접 넣어야 할 값 | 실기기 확인 필요 |
|---|---|---|---|---|
| homelab | 실기기 확인 | `python -m homelab init --yes` → `doctor` → `up` | `--llm-url`, `--appids`, `--wol-mac`, `--hub-host`, 미니PC 구성 시 secrets 복사 | Windows `schtasks /tr` 따옴표, Tailscale serve/HTTPS, 두 대 분리 구성(README 에 "확인 못 함"이라고 적혀 있음) |
| homelab-e2e | 바로 사용 | `python -m homelabe2e projects` | 없음 | 없음 (F-1~F-7 은 아래 표에 정리) |
| s21-sidekick | 실기기 확인 | `python -m sidekick serve --config ~/.homelab/config/sidekick.json` (homelab 으로) | 토큰·비상 코드(예시 482915), debugger.log_file, 출발 체크 위도·경도, calendar.ics, 알람 시각 | S21 Chrome 마이크·카메라·화면 켜짐, VLM 알람 판정, NFC 매크로, Claude 훅 결재 |
| phone-home | 실기기 확인 | `python -m phonehome check` | 뒤쪽 서비스 토큰(homelab 이 `token_file` 로 채움), desk.actions | 폰에서 `#token=` 쿠키 전환, HTTPS 프록시 경로 고쳐 쓰기 |
| jarvis-hub | 실기기 확인 | `python -m jarvis token` → hub/worker 설정 | CHANGE-ME 토큰 3개, WOL MAC·broadcast, hub host(Tailscale IP) | Wake-on-LAN, 미니PC↔5080 워커, Claude 작업 핸들러 |
| object-memory | 실기기 확인 | `python -m objmem config --init` → `ask "리모컨 어디 뒀지?"` | VLM 주소, privacy_hours, 보관 일수 | S21 사진 업로드, 로컬 VLM 정확도, **purge 를 아무도 부르지 않음** |
| sound-watch | 실기기 확인 | `python -m soundwatch analyze 녹음.wav` | 소음계로 `calibrate`, threshold_db(기본 50), 소리 템플릿 `enroll` | 폰 마이크 dB 보정, 실제 초인종·세탁기 인식(README: 실제 가전 소리로는 검증 못 함) |
| life-nagger | 실기기 확인 | `python -m nagger excuse --late 20 --to 팀장` | 게임·작업 프로세스 이름, ntfy 토픽, events 토큰 | Windows 유휴 시간·포그라운드 창·토스트·절전 (자동 테스트 안 됨) → 먼저 `nagger run --dry-run` |
| pc-doctor | 실기기 확인 | (Windows) `py -m pc_doctor init` → `check` | expected_ram_mts, model_dirs, 게임 exe 목록 | Windows 11 전용. 리눅스에서는 아무것도 못 모으면서 "문제 없음"을 출력함 |
| morning-brief | 실기기 확인 | `python -m mbrief sources` → `run --dry-run` | 각 어댑터 경로(homelab 이 채움), tz_offset_minutes | sidekick 낭독, KST 가 아닌 PC 의 날짜 어긋남(F-5) |
| community-briefing | 실기기 확인 | `python -m briefing run --dry-run --config config.example.json` | 관심사 키워드·가중치, RSS/HTML 소스 | 실제 RSS 수집(이번 점검은 오프라인이라 "가져오지 못한 소스" 출력), LLM 요약, TTS |
| game-calendar | 실기기 확인 | `python -m gamecal add 570` → `run --dry-run` | appid, default_tz, ntfy 토픽 | Steam 뉴스 API, 해외 패치노트 LLM 요약 |
| game-diary | 실기기 확인 | `python -m gamediary config --init` → `config --add-source ...` → `scan` | 스크린샷 폴더, VLM 주소 | 로컬 비전 모델 캡션, Steam/Game Bar 폴더 구조 |
| gm-trpg | 실기기 확인 | `python -m gmtrpg new` → `play --world <이름>` | LLM 백엔드(로컬/Anthropic), 모델 ID | LLM 응답 품질, `serve` 웹 화면 |
| hangul-patcher | 실기기 확인 | `python -m hpatch scan "D:\Games\MyGame"` | 용어집(`examples/glossary.example.json`), 폰트 | 실제 RPG Maker/Ren'Py 게임 적용·복원, LLM 번역 |
| paper-butler | 실기기 확인 | `echo "...납부기한 2026-11-25 청구금액 45,230원" \| python -m paperbutler add -` → `due` | 예산 3봉투, ntfy 토픽, inbox 폴더 | 실제 고지서 PDF/사진 OCR(tesseract kor), 결제 문자 형식 |
| shorts-linter | 실기기 확인 | `python -m shortslint titles docs/demo.json` | 프로필, 훅 템플릿, 출처 장부 | ffmpeg/ffprobe 로 실제 영상 검사, 정책 문구는 공식 문서와 비교 |
| steam-buddy | 실기기 확인 | `python -m steam_buddy check "Hades"` (네트워크 필요) | compat.json(예시 데이터), API 키(sync) | Steam 스토어 API(오프라인이면 `buy` 도 실패, F-4) |
| dev-guard | 임시값 교체 | `python -m devguard wallet report` | **wallet.prices + prices_confirmed** | Windows Git Bash 훅 경로, Claude Code 훅 JSON 형식 |
| used-pc-judge | 임시값 교체 | `python -m usedpc judge examples/listing_sample.txt` | **부품 시세표 전체**(`prices/parts_used_kr.json` 이 ROUGH PLACEHOLDER) | 없음 |
| sell-studio | 임시값 교체 | `python -m sellstudio config dump markets` | 플랫폼 수수료율·글자 수 제한(근사치), 린트 단어 | VLM 사진 캡션, 사진 속 개인정보는 직접 확인 |
| sub-pipeline | 임시값 교체 | `python -m subpipe price x --minutes 12.5` | **분당 단가**(5천/7천/1만 원), 전기요금 구간(PLACEHOLDER) | faster-whisper(GPU), LLM 번역 |
| gacha-calc | 임시값 교체 | `python -m gachacalc pity --config examples/banners/example_a.json --pity-count 30 --guaranteed --currency 50000 --package "6480원에 300 젬"` | **배너 확률·천장**(예시는 가상의 값) | 없음 |
| cool-off | 바로 사용 | `python -m cool_off want "에어팟 프로" --price 359000` → `list` | ntfy 토픽(명령줄에 노출됨) | 없음 |
| decision-pet | 바로 사용 | `python -m decision_pet add "블로그 초안" --effort 15` → `now` | 의회 기준값(월 예산 50만, 게임 40시간), LLM 주소(localhost:1234) | `gui` 는 tkinter·데스크톱이 있어야 함 |
| demo-maker | 바로 사용 | `python -m demomaker readme ../steam-buddy` | 없음 | 없음 |
| house-detective | 바로 사용 | `python -m housedet fridge add 우유 --date 2026-10-30` → `fridge check` | 우리 동네 분리배출 규칙(`rules add`) | 없음 (유통기한 표는 보수적인 참고값) |
| kakao-wrapped | 바로 사용 | `python -m kakao_wrapped tests/fixtures/pc.txt --anonymize --out w.html` | 없음 | 실제 카톡 내보내기 형식(PC/iOS/Android) |
| life-court | 바로 사용 | `python -m lifecourt night-snack --food 치킨 --time 23:40` (README 의 `hello` 는 없는 명령) | 없음 | 없음 |
| shorts-feedback | 바로 사용 | `python -m shortsfb csv-report tests/fixtures/studio_ko.csv` | 유형 사전(types.json) | YouTube Studio CSV 열 이름(언어·시기에 따라 다름), 투표 서버를 폰에서 열기 |
| story-factory | 바로 사용 | `python -m storyfactory run --no-llm --out out --cool-off docs/sample/cool-off.json --decision-pet docs/sample/decision-pet.json` | LLM 주소, 가명 사전 | 없음 (폴더로 주면 decision-pet 을 못 찾음, F-1) |
| sub-vocab | 바로 사용 | `python -m subvocab tests/fixtures/sample.srt --no-llm --top 10` | `config/llm.json`, known_words.txt | LLM 뜻 생성 |
| taste-map | 바로 사용 | `python -m tastemap import steam ~/.steam-buddy/data.json` → `map` | 태그 사전(필요하면) | 유튜브 Takeout 실제 파일 |
| til-drafter | 바로 사용 | `python -m til_drafter --check` | redact_words | 실제 Claude Code 세션 로그 형식(버전마다 바뀔 수 있음) |
| token-diet | 바로 사용 | `python -m tokendiet scan` | route 단가(prices.example.json 은 가짜 값) | 없음 |
| voice-journal | 바로 사용 | `python -m vjournal add "오늘 리팩토링 끝냄 #개발" --mood 4` → `morning` | 토큰(homelab 이 만듦) | 폰 Web Speech 입력, `serve` 를 폰에서 열기 |

## 직접 넣어야 할 값 (임시값·추정값 목록)

| 프로젝트 | 파일 | 무엇 |
|---|---|---|
| dev-guard | `dev-guard/devguard/wallet.py:26` → `~/.devguard/config.json` | Claude 단가표 `PLACEHOLDER_PRICES`. `prices_confirmed: true` 로 바꾸기 전까지 경고가 뜸 |
| used-pc-judge | `used-pc-judge/prices/parts_used_kr.json` | 부품 중고 시세 전체가 ROUGH PLACEHOLDER (`prices set` → `prices stamp`) |
| sell-studio | `sell-studio/sellstudio/config.py:22,37,53` | 당근 0%, 번개 3.5%, 스마트스토어 5.5% 수수료, 제목·설명 길이 제한 근사치 |
| sub-pipeline | `sub-pipeline/subpipe/price.py:14`, `subpipe/power.py:12` | 분당 견적 단가 5,000/7,000/10,000원, 전기요금 누진 구간(실제 한전 요금 아님) |
| gacha-calc | `gacha-calc/examples/banners/*.json` | 가상의 확률·천장. 실제 게임의 확률 공시로 새로 작성 |
| steam-buddy | `steam-buddy/steam_buddy/data/compat.json` | 덱·안티치트 호환 예시(가상 appid) |
| token-diet | `token-diet/prices.example.json` | route 단가 자리표시자 |
| decision-pet | `decision-pet/decision_pet/storage.py:12,26-27` | LLM 주소 `localhost:1234`(다른 곳은 8080), 의회 기준 월 50만 원·게임 40시간 |
| sound-watch | `sound-watch/soundwatch/config.py:12,19` | +120 dB 대략 보정, threshold 50 dB. 소음계로 `calibrate` |
| house-detective | `house-detective/housedet/data/shelf.json`, `recycle.json` | 유통기한 표(보수적인 참고값), 분리배출 규칙(지자체마다 다름) |
| s21-sidekick | `s21-sidekick/config.example.json:45,89` | 비상 코드 482915, 토큰, 로그 경로, 위도·경도, ics 경로 (homelab init 을 쓰면 토큰·코드는 자동으로 만들어짐) |
| jarvis-hub | `jarvis-hub/hub.example.json:5-9` | CHANGE-ME 토큰, MAC `AA:BB:CC:DD:EE:FF`, broadcast, Tailscale IP |
| life-nagger | `life-nagger/config.example.json:3,15` | 게임·작업 exe 이름, ntfy 토픽 `내-비밀-토픽-이름`, events 토큰 |
| community-briefing | `community-briefing/config.example.json:15` | 관심사 키워드·가중치, 소스(HTML 예시는 example.com) |
| game-calendar | `game-calendar/gamecal.example.json:2,8` | appid 570/730, default_tz UTC |
| gm-trpg | `gm-trpg/gmtrpg/llm.py:7` | Claude 기본 모델 ID `claude-sonnet-5-5` (내 계정에서 되는지 확인) |
| 여러 프로젝트 | `sub-vocab/config/llm.json`, `~/.gamediary/config.json`, sidekick `llm`/`vision`, hangul-patcher, taste-map `--llm`, sell-studio `--llm-url` | LLM 주소 `http://127.0.0.1:8080/v1`, 모델 이름 `local`/`local-model` |
| 여러 프로젝트 | paper-butler `budget alarm --ntfy my-topic`, cool-off `check --ntfy TOPIC`, game-calendar `ntfy.topic` | ntfy 토픽 예시값. 무작위 토픽(`homelab token show ntfy-topic`)을 쓰세요 |

## homelab 이 다루는 범위

서버(`serve`/`hub`/`worker`): sidekick, voice-journal, object-memory, sound-watch, jarvis-hub, jarvis-worker, phone-home, gm-trpg(기본 꺼짐), shorts-vote(기본 꺼짐), jarvis-worker-minipc(기본 꺼짐). 저장소의 모든 `serve` 명령이 들어가 있습니다.

주기 작업: community-briefing, morning-brief, game-calendar, til-drafter, life-nagger(로그온), devguard-wallet, soundwatch-report, cooloff-check.

**빠진 것**

| 무엇 | README 근거 | 영향 |
|---|---|---|
| object-memory `purge` | object-memory/README.md:102 | 아무 데서도 부르지 않아 사진 7일·설명 90일 보관 약속이 지켜지지 않음 (**높음**) |
| steam-buddy `alert` | steam-buddy/README.md:139 | 환불 기한(14일·2시간) 알림이 설치 흐름에서 빠짐 |
| paper-butler `inbox`, `budget alarm` | paper-butler/README.md:117,170 | 폰 사진 서류 자동 처리와 예산 비상벨이 수동으로만 돎 |
| story-factory 야간 `run` | story-factory/README.md:3 ("야간조") | 수동 |
| game-diary `scan` + `caption` | game-diary/README.md:18-19 | 수동 |
| pc-doctor 주간 `snapshot` | pc-doctor/README.md:177 | 수동 (Windows) |
| decision-pet `gui` (로그온) | decision-pet/README.md:27 | 수동 |
| game-calendar 6시간마다 | game-calendar/README.md:91 | homelab 은 하루 1번(07:00) |
| sidekick 연동 설정: 출발 체크의 object-memory·paper-butler, `forward_events` → life-nagger 이벤트 수신기(8799) | homelab/homelab/configgen.py:68 | e2e 에서는 동작하는데 homelab 이 만드는 설정에는 연결이 없음 |

## 1차 프로토타입 막는 문제

심각도: 높음 = 문서대로 하면 바로 죽거나 약속한 동작이 안 됨, 중간 = 결과가 틀리거나 핵심 흐름이 빠짐, 낮음 = 혼란·일관성 문제.
F-n 은 `homelab-e2e/FINDINGS.md` 의 항목이고, 이번 점검에서 다시 확인했을 때 모두 아직 고쳐지지 않았습니다.

| # | 심각도 | 프로젝트 | 파일:줄 | 문제 | 고치는 방법 (한 줄) |
|---|---|---|---|---|---|
| 1 | 높음 | paper-butler | `paper-butler/paperbutler/cli.py:110` | `budget set --help` 가 ValueError 트레이스백으로 죽음: help 문자열의 `%` 가 이스케이프되지 않음 | `"경보 기준선 %% (기본 70,90,100)"` |
| 2 | 높음 | life-court | `life-court/README.md:14` (17행도) | 설치 절의 첫 명령 `python -m lifecourt hello` 가 없는 명령 | 실제 명령(`night-snack --food 치킨 --time 23:40`)이나 `--help` 로 교체 |
| 3 | 높음 | object-memory / homelab | `homelab/homelab/registry.py:290` | `purge` 를 부르는 곳이 없음(서버도 homelab 도 안 부름) → 보관 기간 약속이 무력화됨 | JOBS 에 `objmem-purge`(매일 새벽) 추가, 또는 serve 가 하루 1번 purge |
| 4 | 중간 | steam-buddy / homelab | `homelab/homelab/registry.py:290` | 환불 기한 `alert` 가 스케줄에 없음 | `steam-buddy-alert` 작업(4시간마다) 추가 |
| 5 | 중간 | paper-butler / homelab | `homelab/homelab/registry.py:290` | `inbox`·`budget alarm` 이 스케줄에 없음 | `paperbutler-inbox`(주기), `paperbutler-budget`(매일) 추가 |
| 6 | 중간 | homelab | `homelab/homelab/configgen.py:68` | sidekick 출발 체크(objmem·paperbutler)와 forward_events→nagger 가 연결되지 않음 | sidekick_config 에 providers·forward_events 를 넣고, nagger 설정의 events 도 함께 만들기 |
| 7 | 중간 | story-factory | `story-factory/storyfactory/seeds.py:112` | F-1: 폴더를 주면 decision-pet 의 `data.json` 을 찾는데 실제 파일은 `state.json` | `state.json` 을 먼저 찾고 README 67행·픽스처도 맞추기 |
| 8 | 중간 | cool-off | `cool-off/cool_off/importers.py:102` | F-2: 환불한 스팀 게임을 구매·후회로 가져옴 | `if game.get("refunded"): skipped += 1; continue` |
| 9 | 중간 | steam-buddy | `steam-buddy/steam_buddy/cli.py:169` | F-4: `buy` 가 무조건 스토어 API 를 먼저 부름. 오프라인이면 `--price` 를 줘도 실패(재현함) | `--name`·`--price` 가 다 있으면 API 생략, 또는 `--offline` |
| 10 | 중간 | morning-brief | `morning-brief/mbrief/cli.py:47` | F-5: +09:00 고정 날짜 vs 다른 프로젝트는 로컬 날짜 → "cool_off: 기준일이 달라요" 경고(재현함), 알람 게이트가 stale 판정 | 기본값을 PC 로컬 시각대로, 오프셋은 명시했을 때만 |
| 11 | 중간 | jarvis-hub | `jarvis-hub/README.md:197` | F-7: "sidekick 에 say 엔드포인트가 아직 없다"며 코드 예시를 안내 — 이미 있고 동작함 | 현재 동작(`POST /api/say`)으로 고치고 예시 코드 삭제 |
| 12 | 중간 | life-nagger | `life-nagger/README.md:95` (98·110행) | F-7: "TODO (s21-sidekick)" 안내가 남아 있음 | 구현된 채널·forward_events 설명으로 교체 |
| 13 | 중간 | (루트) | `README.md:71` | 루트 프로젝트 표에 설치 진입점 homelab 과 homelab-e2e 가 없음 | "기반" 표에 두 행 추가 + 맨 위에 "처음이면 homelab/README.md" |
| 14 | 낮음 | phone-home | `phone-home/phonehome/desk.py:58` | F-3: morning-brief 카드 action(문장)을 id 로 해석 → 책상 실행 버튼을 못 만듦 | 문자열 action 은 라벨 전용, 실행은 `{id,label}` 만 |
| 15 | 낮음 | decision-pet | `decision-pet/decision_pet/storage.py:12` | F-6: LLM 주소를 바꿀 명령·환경변수가 없음(기본 1234 포트, homelab `--llm-url` 이 안 닿음) | `DECISION_PET_LLM_URL` 환경변수 + 기본 8080 |
| 16 | 낮음 | 20개 README / 6개 pyproject | 예: `jarvis-hub/README.md:7`, `jarvis-hub/pyproject.toml:5` | Python 3.9+ 표기(paper-butler 142행은 3.10+) ↔ 루트·CI 는 3.12+ | "Python 3.12 이상", `requires-python = ">=3.12"` (전체 목록은 gaps.json) |
| 17 | 낮음 | voice-journal | `voice-journal/README.md:14` | "저장소 루트에서 실행"이라고 하지만 루트에서는 `No module named vjournal` | "voice-journal 폴더에서 실행" |
| 18 | 낮음 | pc-doctor | `pc-doctor/pc_doctor/cli.py:231` | Windows 가 아닌 곳에서 `check` 가 "✅ 발견된 문제가 없습니다" (아무것도 수집 못 함) | win32 가 아니면 "Windows 11 전용"을 알리고 종료코드 2 |
| 19 | 낮음 | game-calendar | `game-calendar/gamecal/cli.py:62` | 추적 게임이 없을 때 `list` 가 아무것도 출력하지 않음 | 빈 목록 안내 문구 |
| 20 | 낮음 | homelab | `homelab/README.md:33` | phone-home README 순서대로 `phonehome init` 을 먼저 하면 homelab doctor 가 ❌ 토큰 불일치 (재현함) | init 요약에 "기존 설정 N개 유지, 토큰이 다르면 --force" 를 강조 |
| 21 | 낮음 | 여러 프로젝트 | `sell-studio/sellstudio/cli.py:29` 외 | 기본 데이터 위치가 현재 폴더(cwd): sell-ledger.json, gamecal.json, game-diary `out/`, sound-watch `data/`, life-court `./data/soundwatch.db` | `~/.<프로젝트>/` 기본값과 환경변수로 통일 |
| 22 | 낮음 | (루트) | `README.md:77` | 포트 표에 life-nagger 이벤트 수신기 8799 가 없음 | 행 추가 (기본 꺼짐 표시) |

README 와 실제 CLI 를 자동으로 대조했을 때(README 의 `python -m` 줄 약 470개, 하위 명령·플래그 존재 여부), 위 2번 외의 불일치는 없었습니다.
(`devguard push` 는 argparse 밖의 래퍼라 정상, `sellstudio config dump markets`·`gachacalc pity ... --currency --package` 도 README 대로 동작)

## 다음 단계 추천

1. **5분짜리 수정 3개**: #1 paper-butler `%%`, #2 life-court README, #17 voice-journal README. 문서대로 했을 때 바로 막히는 곳들입니다.
2. **homelab 스케줄 보강**: #3 object-memory purge(개인정보 약속이라 먼저), #4 steam-buddy alert, #5 paper-butler inbox·budget. `registry.py` 의 JOBS 에 넣고 homelab README 작업 표도 갱신합니다.
3. **e2e 에서 xfail 로 잡힌 계약 버그**: F-1, F-2, F-3(#7, #8, #14). 고치면 `homelab-e2e` 테스트의 `expectedFailure` 데코레이터를 지웁니다. 같은 때 F-4(#9), F-5(#10)도 처리합니다.
4. **문서 일괄 정리**: Python 3.12+ 표기 26곳(#16), F-7 의 오래된 TODO(#11, #12), 루트 README 에 homelab 추가(#13, #22). 한 번의 문서 PR 로 묶을 수 있습니다.
5. **실제 값 넣기(사용자 몫)**: dev-guard 단가, used-pc-judge 시세표, sell-studio 수수료, sub-pipeline 단가, gacha 배너. 넣기 전까지는 출력에 경고가 뜨거나 가상의 값으로 계산됩니다.
6. **실기기 1회 리허설**: Windows PC 에서 `homelab init → doctor → up → schedule --install`, S21 에서 phone-home 열기 → 사이드킥 시작 → 소리 감시 마이크 → 다음 날 아침 브리핑 낭독까지. 특히 `schtasks` 따옴표, Tailscale HTTPS, life-nagger Windows API, pc-doctor 는 이 환경에서 확인할 수 없었습니다.
7. (선택) homelab 의 sidekick 설정에 출발 체크·이벤트 전달 연동 넣기(#6), 데이터 경로를 `~/.<프로젝트>` 로 통일하기(#21).

기계가 읽을 수 있는 같은 목록(68개 항목: 높음 3, 중간 18, 낮음 47)은 점검 세션의 `gaps.json` 으로 따로 넘겼습니다.
