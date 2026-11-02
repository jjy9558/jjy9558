# house-detective

냉장고 유통기한 탐정과 재활용 분리 심판

<!-- demo:start -->
<!-- demo:end -->

## 설치

Python 3.12 이상, 외부 패키지 없음. 폴더에서 바로 실행합니다.

```
cd house-detective
python -m housedet fridge check
```

선택: `pip install .` 하면 `house-detective fridge check` 처럼 쓸 수 있습니다.

## 사용 예

```
python -m housedet fridge add 우유 두부 --date 2026-10-30      # 개봉일/구매일 기준, 보관법 --storage 냉장|냉동|실온
python -m housedet fridge add 소스 --expires 2027-01-31        # 포장 기한을 직접 입력
python -m housedet fridge add --from-caption caption.txt       # object-memory 캡션 텍스트를 붙여 넣기(사진 직접 접근 없음)
python -m housedet fridge list                                 # 임박 순 목록
python -m housedet fridge check                                # 탐정 보고서 + 임박 품목 요리 제안
python -m housedet fridge remove 3                             # 번호 또는 이름
python -m housedet recycle 치킨 뼈                              # 분리배출 판정
python -m housedet rules add 종이컵 --bin "종이류" --why "우리 동네 안내"   # 내 규칙(내장 규칙을 덮어씀)
```

- 표에 없는 품목은 기한을 임의로 정하지 않고 "기한 모름", 분리배출은 "모름: 지자체 확인"으로 답합니다.
- 유통기한 표(`housedet/data/shelf.json`)는 보수적인 참고값이며, 기한이 지났거나 의심되면 버리라고만 안내합니다.
- 분리배출 표(`housedet/data/recycle.json`)는 일반적인 안내이며 지역(지자체)마다 다릅니다. 공식 기준이 아닙니다.
- 약 복용, 가스불 같은 안전 판정은 하지 않습니다. LLM이나 카메라는 쓰지 않습니다.

## 데이터와 개인정보

- 데이터 위치: `~/.house-detective/` (`fridge.json`, `rules.json`). `--home` 또는 `HOUSEDET_HOME`으로 변경
- 외부로 나가는 것: 없음

## 공통 기반 (jjkit)

`housedet/_jjkit/`에 jjkit 복사본(textwidth)이 들어 있습니다. **직접 고치지 말고** 원본 `jjkit/jjkit/`을 고친 뒤 저장소 루트에서 다시 복사하세요.

```
python tools/jjkit_sync.py house-detective
python tools/jjkit_sync.py --check house-detective
```

코드에서는 `from ._jjkit import textwidth` 처럼 씁니다.

## 테스트

```
cd house-detective
python -m unittest discover -s tests -v
```

네트워크 없이 돕니다(가짜 opener와 픽스처 사용).
