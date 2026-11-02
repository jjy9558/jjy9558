# gacha-calc

가챠 천장 환율 판정기(`pity`)와 게임 시간 시급 명세서(`rate`)를 만드는 도구입니다.
Python 3.12 이상, 표준 라이브러리만 사용합니다.

> 주의: 가챠 지출은 중독성이 있을 수 있습니다. 이 도구는 **정보에 기반한 판단**을 돕기 위한 것이지,
> 뽑기를 권장하거나 예산을 대신 정해 주지 않습니다. 쓸 금액을 미리 정하고, 힘들어지면 쉬세요.

## 실행

```bash
cd gacha-calc

# 천장/확률/환율 판정 (예시 배너 파일 사용)
python3 -m gachacalc pity --config examples/banners/example_a.json \
  --pity-count 30 --guaranteed --currency 50000 --package "6480원에 300 젬"

# 플레이 시간 CSV -> 시급 명세서
python3 -m gachacalc rate examples/playtime_sample.csv

# 테스트
python3 -m unittest discover -s tests -v
```

- `--pity-count`: 마지막 5성 이후 뽑은 횟수 (기본 0)
- `--guaranteed`: 다음 5성이 픽업 확정된 상태일 때 지정
- `--currency`: 지금 보유한 재화 수 (콤마 가능)
- `--package`: 충전 패키지, 예: `"6480원에 300 젬"`

## 배너 설정 (JSON)

| 항목 | 뜻 |
|---|---|
| `name`, `label` | 배너 이름, 표시용 문구 (예시 파일은 `EXAMPLE` 표시) |
| `base_rate_pct` | 기본 5성 적중 확률 (%) |
| `soft_pity_start` | 이 뽑기 번호부터 확률이 오르기 시작 |
| `soft_pity_increase_pct` | 소프트 천장 이후 뽑기마다 더해지는 확률 (%p) |
| `hard_pity` | 이 뽑기 번호에서는 반드시 5성 (100%) |
| `cost_per_pull` | 뽑기 1회 비용 (게임 재화) |
| `rule` | `"50/50"` 또는 `"guarantee"` |
| `currency_name` | 재화 이름 (표시용) |

확률 모델 (뽑기 번호 n = 천장 카운트 + 1):

- n < `soft_pity_start`: 기본 확률
- `soft_pity_start` <= n < `hard_pity`: `min(1, 기본 + (n - 시작 + 1) × 증가량)`
- n = `hard_pity`: 100%
- `50/50`: 5성 적중 시 50%로 픽업. 빗나가면 다음 5성 적중은 픽업 확정.
- `guarantee`: 5성 적중이 곧 픽업.

## 계산 방식

몬테카를로 시뮬레이션이 아니라 (천장 카운트, 확정 여부) 상태에 대한 DP로
"정확히 t번째 뽑기에 픽업을 얻을 확률"을 구합니다. 그래서 기대 뽑기 횟수,
중앙값, 90% 달성 횟수, 최악 횟수(하드 천장 기준)와 보유 재화로 가능한 확률을 계산합니다.
50/50 은 최악의 경우 하드 천장의 두 배 횟수까지 갈 수 있습니다.

판정 문구는 기대 비용과 보유 재화 기준 확률로 만듭니다.
예: `천장까지 약 23만원, 지금 있는 재화로 확률 41% — 존버 추천`

## 시급 명세서 CSV

```csv
게임,플레이 시간,지출
예시 RPG A,120.5,250000
```

- 헤더 필수 (`게임`/`플레이 시간`/`지출`, 또는 `game`/`hours`/`spent`)
- 같은 게임이 여러 줄이면 합산
- 플레이 시간은 0보다 커야 함, 지출은 0 이상
- UTF-8 또는 엑셀 CP949 파일 모두 읽음
- 시급 = 지출 ÷ 플레이 시간, 시급 높은 순으로 순위 표시

## 예시 데이터 안내

`examples/banners/*.json` 과 `examples/playtime_sample.csv` 의 숫자는 모두 가상의 값입니다.
실제 게임의 확률이나 가격과 관계가 없습니다.

## 구성

- `gachacalc/banner.py`: 배너 설정, 적중 확률
- `gachacalc/pity.py`: DP 분포, 최악값, 판정
- `gachacalc/currency.py`: 충전 패키지 파싱, 환율 환산, 원화 표기
- `gachacalc/rate.py`: CSV 읽기, 시급 명세서 계산
- `gachacalc/table.py`: 동아시아 문자 폭을 반영한 표 정렬
- `gachacalc/render.py`, `gachacalc/cli.py`: 출력과 명령행
- `tests/`: unittest 테스트
