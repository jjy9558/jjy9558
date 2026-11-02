# taste-map (취향 지도)

스팀 구매 기록, 게임 스크린샷 캡션, 유튜브 시청 기록을 **태그 벡터**로 바꿔서 한 장짜리 지도(HTML)와
"나는 이런 걸 좋아한다" 5줄, 다음에 살 후보 순위, 연말결산 카드, 게임 경력 증명서를 만듭니다.

<!-- demo:start -->
<!-- demo:end -->

- 네트워크를 쓰지 않습니다. 모든 계산은 이 컴퓨터 안에서 순수 파이썬으로 합니다(PCA 2D 투영, 시드 고정 k-means).
- LLM은 선택입니다. 켜면 프로필 5줄의 말투만 다듬고, 숫자가 바뀌거나 형식이 틀리면 원문을 씁니다.
- 결과 HTML은 외부 파일, 글꼴, 스크립트가 없는 한 파일이라 메신저로 보내거나 폰에서 바로 열 수 있습니다.

## 설치

Python 3.12 이상, 외부 패키지 없음. 폴더에서 바로 실행합니다.

```
cd taste-map
python -m tastemap --help
```

선택: `pip install .` 하면 `taste-map ...` 으로 쓸 수 있습니다. 데이터는 `~/.taste-map/data.json`
(`TASTEMAP_HOME` 또는 `--home` 으로 바꿈)에 저장됩니다.

## 사용 예

```
python -m tastemap import steam ~/.steam-buddy/data.json     # 옆에 compat.json 이 있으면 덱 등급도 읽음
python -m tastemap import diary ~/game-diary/gamediary.sqlite3   # 읽기 전용으로 열어 태그만 집계
python -m tastemap import youtube "Takeout/YouTube 및 YouTube Music/기록/시청 기록.json"
python -m tastemap status                       # 개수, 태그 없는 항목 안내
python -m tastemap tags list --untagged         # 태그를 못 찾은 항목
python -m tastemap tags edit "Mystery" --add 퍼즐,로그라이크   # 수동 태그 (이름 일부, appid, 키)
python -m tastemap map --out map.html           # 취향 지도 (--k 군집 수, --seed, --llm)
python -m tastemap profile                      # 5줄 프로필
python -m tastemap rank --candidates appids.txt # 후보 순위
python -m tastemap wrapped --year 2026 --out wrapped.html
python -m tastemap career --name 닉네임 --out career.html
```

### 후보 파일 (`rank --candidates`)

한 줄에 하나. `#` 뒤는 주석. 태그를 아는 게임(사전, 수동 태그)이면 appid만 적어도 됩니다.

```
1794680
7001,어떤 축구 게임,스포츠|멀티플레이     # appid,이름,태그들(| 로 구분)
```

steam-buddy `gems` 로 찾은 appid 를 그대로 붙여 넣으면 됩니다. 점수는 내 취향 벡터와 후보 태그 벡터의 코사인
유사도입니다. 만족도(rate)가 높은 게임의 태그는 끌어올리고, 낮은 게임의 태그는 깎습니다. 태그를 알 수 없는
후보는 점수 없이 맨 아래에 "태그 없음"으로 나옵니다. `--handheld` 를 주면 compat.json 에서 스팀덱 미지원인
후보를 0.2 감점합니다.

## 태그는 어디서 오나

1. `tastemap/data/tags.json` 오프라인 사전: 태그별 별칭(한국어/영어)으로 게임 이름, 영상 제목, 장르, 캡션 글자를 찾습니다.
   `games` 항목은 이름이나 appid 를 아는 게임에 태그를 바로 붙입니다. **예시 사전**이라 appid 가 틀렸을 수 있으니
   이름 별칭을 우선 쓰세요.
2. steam-buddy 기록에 `genres`/`tags` 필드가 있으면 사용(없어도 됩니다).
3. game-diary 캡션의 `tags`, `mood`.
4. 수동 태그(`tags edit`): 가중치가 가장 큽니다.
5. `<데이터 폴더>/tags.json` 에 같은 형식으로 쓰면 사전을 덧붙이거나 덮어씁니다.

태그를 끝내 못 찾은 항목은 지도에서 빠지고 몇 개인지 알려 줍니다.

## 만족도 신호

게임은 steam-buddy 만족도(1~5, 3이 중립)와 플레이 시간, 스크린샷 수로, 영상은 시청 횟수로 선호 신호를 만듭니다.
영상 취향은 게임 취향의 35%만 섞습니다. 영상 전용 태그(리뷰, 공략 같은 것)는 후보 점수에서 뺍니다.

## 연말결산과 경력 증명서

- `wrapped --year Y`: Y년에 산 게임 기준으로 플레이 시간 합계, 쓴 돈, 시간당 비용 최저/최고, 가장 오래 묵힌(아직 안 켠) 게임,
  가장 오래 한 게임, 평균 만족도, 그해 유튜브 시청 횟수. 플레이 시간은 steam-buddy 의 **누적값**이라 "그해에 한 시간"이
  아니라 "그해에 산 게임의 지금까지 시간"입니다. 환불한 게임은 뺍니다.
- `career`: 재미용 경력 증명서 한 장(인쇄 가능). 닉네임, 경력 기간, 직급(누적 시간 기준), 전문 분야, 주요 프로젝트 5개만 들어가며
  가격, 계정, 스팀 ID 는 넣지 않습니다.

## 데이터와 개인정보

- 저장하는 것: 스팀 게임 이름/구매일/가격/플레이 분/만족도, 게임별 스크린샷 수와 태그 집계, 영상 **제목과 시청 시각**, 수동 태그.
- 저장하지 않는 것: 시청 기록의 URL, 채널, 계정 정보, game-diary 캡션 원문과 파일 경로(채팅창 이름이 찍혔을 수 있음). 영상 제목 안의
  이메일/전화번호 같은 것은 jjkit `redact` 로 가립니다.
- game-diary sqlite 는 읽기 전용으로만 엽니다. 다른 프로젝트의 파일은 수정하지 않습니다.
- `--llm` 은 `TASTEMAP_LLM_URL`(로컬/사설망 주소만 허용, `TASTEMAP_ALLOW_REMOTE_LLM=1` 이면 외부도)이 있을 때만 쓰고, 보내는 것은
  이미 만든 5줄 문장뿐입니다. 없으면 LLM 없이 같은 결과가 나옵니다.

## 한계

- 태그 사전이 작아서 낯선 게임은 "태그 없음"이 많이 나옵니다. `tags edit` 로 붙이면 바로 반영됩니다.
- 2D 지도는 정보를 줄여서 보여 주는 그림입니다. 가까우면 비슷하지만 멀다고 꼭 다른 것은 아닙니다(화면에 설명량 %를 적어 둡니다).
- 시청 기록 제목 형식(`Watched `, `시청: ` 접두사)은 몇 가지만 처리합니다. 다른 형식이면 접두사가 제목에 남을 수 있습니다.

## 공통 기반 (jjkit)

`tastemap/_jjkit/`에 jjkit 복사본(llm, redact, textwidth)이 들어 있습니다. **직접 고치지 말고** 원본 `jjkit/jjkit/`을 고친 뒤 저장소 루트에서 다시 복사하세요.

```
python tools/jjkit_sync.py taste-map
python tools/jjkit_sync.py --check taste-map
```

## 테스트

```
cd taste-map
python -m unittest discover -s tests -v
```

네트워크 없이 돕니다(가짜 입력 파일, 임시 폴더, 스텁 LLM).
