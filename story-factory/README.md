# story-factory

사연 공장 야간조: 생활 로그 소재를 익명 사연 쇼츠 대본으로

<!-- demo:start -->
<!-- demo:end -->

## 하는 일

내 생활 기록(한숨·설거지 방치 일수·못 지킨 할 일·후회한 소비·단톡방 익명 통계)에서 **익명 사연 쇼츠 소재**를 뽑아 밤새 대본까지 만들고,
shorts-linter로 검사한 뒤 아침에 "오늘 올릴 만한 후보 2개"를 md로 보고합니다.

```
seeds(소재 카드 10개, 읽기 전용) -> scrub(마스킹 + 가명화 + 민감 주제 필터) -> draft(LLM 있으면 사연체, 없으면 템플릿)
  -> lint(shorts-linter: titles, promise, disclose, similar) -> report(후보 2개 md) -> [--submit-jarvis]
```

## 설치

Python 3.12 이상, 외부 패키지 없음. 폴더에서 바로 실행합니다.

```
cd story-factory
python -m storyfactory --help
```

## 사용 예

```
# 소재 카드만 보기 (가명화/제외 결과 포함)
python -m storyfactory seeds --life-nagger ~/.life-nagger --decision-pet ~/.decision-pet \
    --cool-off ~/.cool-off --kakao wrapped.json --name 김민수 --place 행복아파트

# 전체 파이프라인 (야간 실행용). 같은 입력 + 같은 --seed 면 같은 후보가 나옵니다.
python -m storyfactory run --out out --life-nagger ~/.life-nagger --decision-pet ~/.decision-pet \
    --cool-off ~/.cool-off --kakao wrapped.json --name 김민수 --names-file names.txt --seed 17

# 일기는 내가 고른 것만: id 를 직접 지정한 항목만 읽습니다
python -m storyfactory run --voice-journal ~/.vjournal/journal.db --journal-entry 12 --journal-entry 40 ...

# LLM(로컬 OpenAI 호환)이 켜져 있으면 사연체, 아니면 템플릿
STORY_FACTORY_LLM_URL=http://127.0.0.1:8080/v1 python -m storyfactory run ...
python -m storyfactory run --no-llm ...

# 5080 PC 작업 큐에 결과 알리기 (서버가 없으면 건너뜀)
python -m storyfactory run --submit-jarvis --jarvis-device pc --jarvis-token ... ...

python -m storyfactory scrub "김철수 씨한테 010-1234-5678 로 연락했다" --name 김철수   # 가명화 확인
python -m storyfactory lint out/2026-11-02                                               # 산출물만 다시 린트
```

산출물은 `out/<기준 날짜>/` 에 shorts-linter 사이드카 규칙대로 `<카드id>.txt`(나레이션), `<카드id>.meta.json`, 그리고 `report.md`(아침 보고),
`run.json`(제외 사유 요약, 내용 없음)으로 생깁니다. 이 폴더에서 `shortslint similar .` / `titles <id>.meta.json` 을 그대로 쓸 수 있습니다.
`.story-factory` 표식이 있는 폴더만 재실행 때 비웁니다.

### meta.json

`title`(린트 점수 최고 제목으로 갱신), `title_candidates`, `claims`, `thumbnail_text`, `hashtags`, `transcript`,
`ai_used: {voice, image, script}`(LLM이 대본을 썼으면 `script: true`; 음성·이미지는 기본 `true`, 직접 녹음/촬영이면 `--no-ai-voice`/`--no-ai-image`),
`ai_disclosure`, `story_factory: {card, source, kind, seed, anonymized}`. `ai_used` 는 shorts-linter `disclose` 가 그대로 읽습니다.

## 소재 소스 (전부 읽기 전용)

| 소스 | 읽는 것 | 만드는 카드 |
|---|---|---|
| life-nagger `counters.jsonl` | 설거지/한숨/푸시업 기록 | 설거지 방치 N일, 한 주 한숨 N번(몰린 시간), 한 달 푸시업 |
| decision-pet `data.json` | 묘지(`status=buried`). voice-journal에서 가져온 "선언했다 포기" 항목 포함 | 묘지 N기, 가장 많이 미룬 묘비명, 선언 후 N일 |
| cool-off `data.json` | 후회 목록(만족도 2 이하 또는 "거의 안 씀"), 참은 위시 | 후회 소비 단건/합계, 참아서 아낀 돈 |
| kakao-wrapped 통계 JSON | **`meta.anonymized: true` 인 파일의 숫자 필드만** (연속 일수, 최다 시간대, 최다 메시지 날, 총량). 멤버/인용/단어는 읽지 않음 | 단톡 연속, 시끄러운 시간 등 |
| voice-journal `journal.db` | `--journal-entry` 로 지정한 일기만. 지정 없으면 DB를 열지 않음 | 일기 한 줄 |

원본은 `open(..., "r")` 과 sqlite `mode=ro` 로만 열고 쓰기·삭제를 하지 않습니다(테스트가 바이트와 mtime 불변을 확인).
단톡방 **원문 대화는 어떤 경로로도 읽지 않습니다**. 익명화되지 않은 통계 JSON은 거부합니다.

## 가명화와 민감 주제 필터

1. jjkit `redact`: 전화·이메일·경로·토큰·카드·계좌 등 마스킹
2. 이름·지명 가명화: `--name`/`--names-file`/`--place` 로 알려 준 실명(성을 뗀 이름 포함), `OOO 씨/님`, `OO초등학교·OO대학교·OO아파트`,
   주요 도시·동네 이름을 일관된 가명(가온, 나래 ...)이나 `○○`로 바꿉니다. 가명은 시드로 결정됩니다.
3. 가명화 뒤에도 원본 이름이 남아 있으면(`leak`) 그 카드는 후보에서 빠집니다.
4. 민감 주제 카드는 **후보에서 제외**하고 보고서에는 카드 id와 사유만 적습니다(내용 미기록): 실존 인물 비방(비방 표현 + 사람 지칭),
   의료·정신건강, 미성년자 단서(학생·학교·19세 미만 나이 등). LLM 출력에도 같은 검사를 다시 하고, 걸리면 템플릿으로 되돌립니다.

정규식 기반 best-effort 입니다. 실명을 모르는 상태에서 문장 속 이름을 완벽히 찾을 수는 없으니, 올리기 전에 대본을 직접 읽어 보세요.
LLM 에는 가명화가 끝난 카드 내용만 보내며, 주소는 로컬/사설망만 허용됩니다(jjkit `llm`).

## shorts-linter 연동

shorts-linter 폴더(`--linter-dir` > 환경변수 `SHORTSLINT_DIR` > 형제 폴더 `../shorts-linter`)를 찾아 모두 subprocess 로 호출합니다.
`titles`(제목 후보 점수, 최고 점수 제목을 최종 제목으로) -> `promise`(제목·썸네일·claims 가 대본에 있는지; shorts-linter 에는 단독 명령이 없어
`shortslint.promise.check_promise` 를 `-c` 로 실행) -> `disclose`(AI 공개 문구 초안) -> `similar`(같은 날 대본끼리, `--history` 과거 대본과 재활용 유사도).
후보는 약속 일치 > 유사도 위험 없음 > 제목 80점 이상 > 소재 우선순위 순으로 2개를 고릅니다. 린터가 없거나 실패하면 해당 칸을 "생략"으로
표시하고 정상 종료합니다. 영상 렌더(TTS, 이미지)는 이 도구의 범위 밖입니다.

## jarvis 연동 (선택)

`--submit-jarvis` 는 `POST {--jarvis-url}/api/jobs` 로 `story.batch` 작업(후보 id/제목/파일명만, 대본 본문 없음)을 올립니다.
`JARVIS_URL`/`JARVIS_DEVICE`/`JARVIS_TOKEN` 환경변수도 읽습니다. 설정이 비었거나 서버가 꺼져 있으면 메시지만 남기고 건너뜁니다.
그 기기의 `submit` 목록에 `story.batch` 가 있어야 합니다.

## 데이터와 개인정보

- 읽는 곳: 위 소스 파일들(읽기 전용). 쓰는 곳: `--out` 폴더뿐.
- 외부로 나가는 것: 기본값 없음. LLM(로컬 주소)과 jarvis(선택)에만 가명화된 내용/후보 제목이 갑니다. 자동 업로드는 하지 않습니다.
- 일기는 지정한 항목만, 단톡은 익명 통계 숫자만 씁니다.

## 공통 기반 (jjkit)

`storyfactory/_jjkit/`에 jjkit 복사본(httpkit, llm, notify, redact, textwidth)이 들어 있습니다. **직접 고치지 말고** 원본 `jjkit/jjkit/`을 고친 뒤 저장소 루트에서 다시 복사하세요.

```
python tools/jjkit_sync.py story-factory
python tools/jjkit_sync.py --check story-factory
```

코드에서는 `from ._jjkit import httpkit` 처럼 씁니다.

## 테스트

```
cd story-factory
python -m unittest discover -s tests -v
```

네트워크 없이 돕니다(가짜 opener와 픽스처 사용).
