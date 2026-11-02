# 안녕하세요, jjy9558입니다 👋

Claude Code로 만든 개인 도구 프로젝트들이 모여 있는 곳입니다.

## 프로젝트

| 프로젝트 | 설명 | 상태 |
|---|---|---|
| [steam-buddy](steam-buddy/) | 스팀 구매 결정 비서: 사기 전 체크(가격·한국어·리뷰·얼리액세스 방치 위험), 환불 기한(14일·2시간) 알림, 산 뒤 시간당 만족도 돌아보기, 번들 실가치·숨은 명작 찾기 | v0.1 |
| [til-drafter](til-drafter/) | Claude Code 세션 기록으로 매일 밤 TIL 블로그 초안 자동 생성 (민감 정보 자동 제거, 게시는 직접) | v0.1 |
| [community-briefing](community-briefing/) | 내 관심사에 맞춘 커뮤니티 브리핑 봇 (RSS/게시판 수집 → 로컬 LLM 요약 → 아침 브리핑, 음성 팟캐스트·이슈 타임라인) | v0.1 |
| [s21-sidekick](s21-sidekick/) | 남는 S21을 Claude의 눈·귀·입으로: 혼잣말 디버거, 침대 탈출 알람, 귀가 집사, 커밋 관중석, rm -rf 결재 보고, 현관 출발 체크 | v0.1 |
| [kakao-wrapped](kakao-wrapped/) | 단톡방 연말결산: 카톡 내보내기 파일 → 폰에서 넘겨보는 결산 카드 HTML (익명화 지원) | v0.1 |
| [game-calendar](game-calendar/) | 내 게임 캘린더: 점검·이벤트·서비스 종료를 .ics로, 해외 패치노트 한국어 요약, 종료 D-day 알림 | v0.1 |
| [pc-doctor](pc-doctor/) | PC 닥터(윈도우): 사양 스냅샷·비교, 병목 진단, 윈11 설정 적용·복원, 업데이트 신호등, 게임 모드 | v0.1 |
| [hangul-patcher](hangul-patcher/) | 인디게임 AI 한글패치 (RPG Maker MV/MZ, Ren'Py) — 로컬 LLM 번역, 원본 백업·복원, 개인 사용 전용 | v0.1 |
| [sub-pipeline](sub-pipeline/) | 자막 외주용: Whisper 받아쓰기 → 로컬 LLM 번역 초안 → 검수 화면 → SRT, 분당 견적 | v0.1 |
| [sub-vocab](sub-vocab/) | 영어 자막 → 단어장 (Markdown + Anki) | v0.1 |
| [jarvis-hub](jarvis-hub/) | S21·미니PC·5080을 묶는 자비스 허브: 작업 큐, 음성 명령 라우터, Wake-on-LAN, 대시보드 | v0.1 |
| [life-nagger](life-nagger/) | 생활 잔소리 엔진: 새벽 게임 엄마봇, 엉덩이 경보, 재택 출근 도장, 지각 변명 생성기, 한숨 카운터 | v0.1 |
| [voice-journal](voice-journal/) | 음성 일기 → 지식베이스: 3줄 일기, 퇴근 3문답, 선언 성적표, 야간 작업 아침 보고, "왜 접었지?" 검색 | v0.1 |
| [paper-butler](paper-butler/) | 개인 서류 비서: 고지서·결제문자 → 납부일 캘린더, 가계부, "버려도 됨" 판정 | v0.1 |
| [cool-off](cool-off/) | 구매 쿨링오프 + 후회 일기 + 구독료 총합 | v0.1 |
| [used-pc-judge](used-pc-judge/) | 중고 PC 매물 적정가 판정 + 업그레이드 추천 (붙여넣기 방식, 크롤링 없음) | v0.1 |
| [decision-pet](decision-pet/) | 결정해주는 데스크톱 펫: 오늘은 이거 해, 15분 실험, 안 한 일 묘지 | v0.1 |
| [dev-guard](dev-guard/) | 개발자 안전장치: 비밀값 커밋 경보, 금요일 배포 차단, 에이전트 파업, 사용량 지갑 | v0.1 |
| [gm-trpg](gm-trpg/) | Claude GM 지속 세계 TRPG (로컬 LLM / Claude API) | v0.1 |
| [game-diary](game-diary/) | 게임 스크린샷 AI 앨범·일기 (로컬 비전 모델) | v0.1 |
| [shorts-linter](shorts-linter/) | 쇼츠 업로드 전 검사기: 길이·비율·음량·무음·자막·메타데이터·제목 약속 일치 | v0.1 |
| [demo-maker](demo-maker/) | CLI 터미널 데모 애니메이션 SVG + README 사용법 자동 생성 | v0.1 |

## 테스트

- 전체 테스트 실행: `python run_all_tests.py` (`tests/` 폴더가 있는 모든 프로젝트를 실행하고 요약 표를 출력합니다)
- 일부만 실행하거나 제외: `--only a,b`, `--skip a,b`, 첫 실패 시 중단: `--fail-fast`
- CI(GitHub Actions)는 push와 PR마다 Ubuntu / Windows × Python 3.12 / 3.14 조합에서 테스트를 실행합니다.

## 공통

- 모든 프로젝트는 Python 3.12+ 표준 라이브러리만 사용합니다.
- 자세한 내용은 각 폴더의 README를 참고하세요.
