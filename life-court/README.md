# life-court

야식·지각·내기를 판결문으로 만드는 웃긴 재판소

<!-- demo:start -->
<!-- demo:end -->

## 설치

Python 3.12 이상, 외부 패키지 없음. 폴더에서 바로 실행합니다.

```
cd life-court
python -m lifecourt --help                                  # 명령 목록
python -m lifecourt toast 민수:개발자 --occasion 송년회      # 건배사 (저장 안 함)
```

선택: `pip install .` 하면 `life-court toast 민수:개발자 --occasion 송년회` 처럼 쓸 수 있습니다.

## 사용 예

```
life-court night-snack --food 치킨 --time 23:40 --kcal 1200 --count 2   # 야식 재판소
life-court late --consent --minutes 25 --meeting "금요 저녁" --excuse "버스가 안 와서"
life-court bet --a 민수 --b 지연 --condition "축구 승부 예측" --winner 민수 --stake 커피 --complaint "심판이 편파였다"
life-court toast 민수:개발자 지연 --occasion 송년회
life-court wakeup --woke 03:40          # 누가 날 깨웠나 재판 (sound-watch DB 자동 탐색)
life-court forget --who 철수          # 저장된 지각 기록 지우기 (--night, --late도 가능)
```

(설치 없이는 `python -m lifecourt ...`) 공통 옵션: `--seed`(같은 시드면 같은 판결), `--date`, `--md 파일`,
`--html 파일`(인쇄용 단일 HTML, 외부 자원 없음), `--speak`, `--llm`.

- **night-snack**: 시각, 열량, 오늘 횟수, 최근 7일 기록(재범)을 점수로 환산해 형량(샐러드 N일)을 정합니다. 같은 날 같은 입력은 한 번만 기록됩니다.
- **late**: 본인이 자진 피고로 신청하는 재판입니다. `--consent` 없이는 거부됩니다. 피고인은 기본적으로 본인(`--me`, 또는 `LIFECOURT_ME`)뿐이고, 다른 사람은 그 사람이 직접 동의했을 때만 `--other-agreed`로 가능합니다.
- **bet**: 두 사람, 조건, 결과로 판결문과 항소 기각 문구를 만듭니다. 아무것도 저장하지 않습니다.
- **toast**: 이름(`이름:직업`)으로 건배사 5개를 만듭니다. 아무것도 저장하지 않습니다.

- **wakeup**: "누가 날 깨웠나 재판". sound-watch의 sqlite를 **읽기 전용**(`mode=ro`)으로 열어 밤 시간대(기본 오늘 00:00~07:00, `--from/--to`, 시작이 끝보다 늦으면 자정을 넘는 창)의 소음 이벤트와 인식 기록(세탁기·초인종·노크 등)을 모으고, 가장 크고 긴 소리(또는 `--woke HH:MM`에 가장 가까운 소리)를 주범으로 판결문을 씁니다. `--db` 기본값은 `$SOUNDWATCH_HOME/soundwatch.db` → (옛 위치) `./data/soundwatch.db` → `~/.soundwatch/soundwatch.db` 순서로 찾습니다. 아무것도 저장하지 않고 sound-watch 기록은 바꾸지 않습니다.
  - 피고는 **소리뿐**입니다. 사람·이웃·집을 가리키는 말(옆집, 아저씨 등)이 소리 이름에 섞여 있으면 "정체불명의 소음"으로 바꾸고, 이 판결문으로 누구에게도 따지지 말라는 유의 문구를 붙입니다.
  - 증거의 dBFS는 보정하지 않은 **상대값**입니다(실제 소음계 dB과 다름). 소리끼리 비교용으로만 보세요.
  - 기록이 없는 밤은 "무혐의"(마이크가 켜져 있었는지 확인하라는 처방)로 끝납니다. `--llm`은 wakeup에서 무시됩니다(템플릿 문장만 사용).

판결문은 복사해서 직접 보내는 용도입니다. 단톡 자동 전송 기능은 일부러 없습니다.

### 낭독과 LLM (선택)

- `--speak`: 주문 한두 문장을 s21-sidekick 폰이 읽게 합니다. `LIFECOURT_SIDEKICK_TOKEN` 필요, 주소는 `LIFECOURT_SIDEKICK_URL`(기본 `http://127.0.0.1:8765`). 실패해도 판결문은 정상 출력됩니다.
- `--llm`: `LIFECOURT_LLM_URL`(+`LIFECOURT_LLM_MODEL`)의 로컬 OpenAI 호환 서버로 문장만 다시 씁니다. 로컬/사설망 주소만 허용합니다. 이름이나 숫자가 하나라도 바뀌면 템플릿 원문을 씁니다. 기본은 LLM 없이 템플릿만 씁니다.

## 데이터와 개인정보

- 데이터 위치: `~/.life-court/night.json`, `late.json` (`LIFECOURT_DIR` 또는 `--data-dir`로 바꿀 수 있으나 **홈 폴더 안만 허용**). 파일 권한 600 시도.
- 지각 기록에는 피고인 이름이 들어가므로 로컬에만 있고, 전송하거나 내보내는 기능이 없습니다. `forget`으로 지울 수 있습니다.
- 외부로 나가는 것: 없음. 단 `--speak`는 내 sidekick 주소로, `--llm`은 내가 지정한 로컬 LLM으로 판결 문구가 갑니다.
- 모든 판결문은 오락용이며 법적 효력이 없습니다.

## 공통 기반 (jjkit)

`lifecourt/_jjkit/`에 jjkit 복사본(llm, notify, textwidth)이 들어 있습니다. **직접 고치지 말고** 원본 `jjkit/jjkit/`을 고친 뒤 저장소 루트에서 다시 복사하세요.

```
python tools/jjkit_sync.py life-court
python tools/jjkit_sync.py --check life-court
```

코드에서는 `from ._jjkit import llm` 처럼 씁니다.

## 테스트

```
cd life-court
python -m unittest discover -s tests -v
```

네트워크 없이 돕니다(가짜 opener와 픽스처 사용). wakeup 테스트는 sound-watch 스키마 텍스트로 만든 임시 sqlite를 씁니다.
