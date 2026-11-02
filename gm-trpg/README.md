# Claude GM — 지속 세계 TRPG 봇

로컬 LLM(llama-server)이나 Claude API로 돌아가는 **한국어 텍스트 TRPG 게임 마스터**입니다.
세계 설정, NPC, 아이템, 퀘스트, 사실 기록이 sqlite에 저장되어 **세션이 바뀌어도 세계가 기억됩니다.**
Python 3.9+ 표준 라이브러리만 사용합니다 (pip 설치 불필요).

## 설정

### llama-server (로컬, 기본값)
```bash
llama-server -m your-model.gguf --port 8080
python -m gmtrpg new                      # 세계 만들기 (프리셋: 판타지 던전 / 사이버펑크 서울 2087 / 조선 괴담)
python -m gmtrpg play --world mydungeon   # 터미널 플레이
```
기본 주소는 `http://127.0.0.1:8080/v1`(프록시 환경변수 무시). 바꾸려면 `--base-url` 또는 `GMTRPG_BASE_URL`.

### Claude API
```bash
export ANTHROPIC_API_KEY=sk-ant-...
python -m gmtrpg play --world mydungeon --backend anthropic            # 모델 기본값 claude-sonnet-5-5
python -m gmtrpg play --world mydungeon --backend anthropic --model <모델명>
```

### 웹 UI
```bash
python -m gmtrpg serve --world mydungeon          # http://127.0.0.1:8765
```
채팅 + 상태 패널(위치, NPC, 소지품, 퀘스트, 지도). 기본은 127.0.0.1에만 바인딩됩니다(인증 없음, 외부 공개 비권장).

공통 옵션: `--dir worlds`(세계 DB 폴더), `--seed N`(주사위 시드), `--recent-turns 6`.

## 기억 방식

GM은 전체 채팅 로그를 받지 않습니다. 매 턴 다음만 조립해 전달합니다.
1. 세계관(배경/분위기/규칙)
2. 현재 위치, 연결 장소, HP, 소지품
3. 지금 장면에 있는 NPC(특징, 관계, 마지막 목격 턴)와 진행 중 퀘스트
4. **관련 기록**: 플레이어 입력·위치·NPC 이름과 문자 bigram이 겹치는 사실 상위 5개 (한국어 조사 변화에 강한 단순 키워드 검색)
5. **롤링 요약**: 최근 N턴(기본 6)을 넘어선 오래된 턴은 LLM이 기존 요약과 합쳐 압축
6. 직전 N턴의 원문 대화

GM 응답은 서술 + 펜스 JSON(`state_updates`)입니다. 코드가 검증한 뒤 DB에 반영하며(`move_to`, `add_npc`, `update_npc`, `add_item`, `remove_item`, `quest_add`, `quest_done`, `add_fact`, `hp_change`, `add_location`), JSON이 깨지면 서술만 살리고 갱신은 건너뜁니다(로그 `gmtrpg` 경고).

**주사위는 코드가 굴립니다.** GM이 `{"roll": {"skill": "설득", "dc": 12}}`를 요청하면 엔진이 d20+보정(스탯 또는 `modifier`, 유리/불리 지원)으로 판정하고, 결과를 같은 턴에 GM에게 돌려주어 이어서 서술하게 합니다(2단계 호출).

매 턴 시작 직전 상태가 스냅샷으로 저장되어 `/되감기`로 되돌릴 수 있습니다(최근 50턴).

## 명령어

| 명령 | 설명 |
|---|---|
| `/상태` | HP, 위치, 주변 NPC |
| `/인벤` | 소지품 |
| `/퀘스트` | 진행 중/완료 퀘스트 |
| `/지도` | 알려진 장소와 연결 (ASCII, `[*]`=현재) |
| `/요약` | 지금까지를 요약해 표시 |
| `/저장` | 세션 요약 저장 (상태는 매 턴 자동 저장) |
| `/되감기` | 마지막 턴 취소 |
| `/세계관` | 세계 설정 보기 |
| `/도움말`, `/종료` | |

## 개발

```bash
python3 -m unittest discover -s tests -v
```
테스트는 FakeLLM으로 네트워크 없이 실행됩니다.

## 로드맵
- Discord 봇 (v0.1 비목표)
- 멀티플레이어 (여러 플레이어/파티, 턴 순서)
- 이미지 생성 (장면/NPC 삽화)
- 임베딩 기반 검색, 스트리밍 출력, 월드 내보내기/가져오기

기본 톤은 전연령입니다. 수위는 세계관의 `tone`/`rules`로 조절하세요.
