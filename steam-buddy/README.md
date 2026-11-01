# 스팀 구매 결정 비서 (steam-buddy)

스팀 게임을 사기 전부터 산 뒤까지 챙겨 주는 작은 명령줄 도구입니다.

- **사기 전** `check`: 가격/할인, 한국어 지원(자막/음성), 리뷰, 얼리액세스 방치 위험 점수
- **살 때** `buy`: 구매를 기록하고 환불 기한 추적 시작
- **산 뒤** `status` / `alert` / `keep`: 환불 기한 현황, 임박 시 폰 알림, 계속 갖기로 결정
- **돌아보기** `rate` / `report`: 만족도 기록, 시간당 비용, 안 켜본 게임, 후회 목록

## 환불 규칙 (중요)

Steam 환불은 **구매 후 14일 이내 AND 플레이 2시간 미만**일 때 가능합니다. 이 도구는 이 기준으로 남은 여유를 계산할 뿐이며, **최종 판단은 항상 Steam 공식 환불 정책과 Steam의 결정이 우선**입니다.

상태: 여유 / 급함(마감 2일 이내 또는 남은 플레이 30분 이내) / 기한지남 / 보유확정(`keep`).

## 설치

Python 3.9 이상, 외부 패키지 없음. steam-buddy 폴더에서 바로 실행합니다.

```
cd steam-buddy
python -m steam_buddy check "Hades"
```

선택: `pip install .` 하면 `steam-buddy check ...` 처럼 쓸 수 있습니다.
데이터는 `~/.steam-buddy/data.json`에 저장됩니다 (환경변수 `STEAM_BUDDY_HOME`으로 위치 변경).

## 사용 예

```
python -m steam_buddy check "Hades"                 # 사기 전 체크 (이름 또는 appid)
python -m steam_buddy buy 1145360                   # 구매 기록 (가격은 스토어 가격)
python -m steam_buddy buy Hades --date 어제 --price 20000
python -m steam_buddy play Hades 1h30m              # 총 플레이 시간 입력 (1h30m, 90m, 1.5, 1:30)
python -m steam_buddy play Hades 20m --add          # 더하기
python -m steam_buddy status [--all]                # 환불 기한 현황
python -m steam_buddy keep Hades                    # 계속 갖기 (알림 끔)
python -m steam_buddy refunded Hades                # 환불했음 (통계에서 제외)
python -m steam_buddy rate Hades 5 --note "명작"    # 만족도 1~5
python -m steam_buddy report                        # 시간당 비용 돌아보기
python -m steam_buddy remove Hades                  # 기록 삭제
```

이름이 여러 게임에 걸리면 오류가 나니 appid로 지정하세요.

## Steam Web API 키와 SteamID64 (선택, `sync`용)

없어도 모든 기능이 동작합니다(플레이 시간은 `play`로 직접 입력). 있으면 `sync`가 플레이 시간을 자동으로 가져오고, 새로 생긴 게임을 구매로 자동 감지합니다.

1. API 키: https://steamcommunity.com/dev/apikey 에서 발급 (도메인 칸에는 아무 값이나 입력)
2. SteamID64(17자리 숫자): 스팀 프로필 URL이 `/profiles/7656...`이면 그 숫자, 커스텀 URL이면 https://steamid.io 에 프로필 주소를 넣어 확인
3. 프로필의 게임 세부 정보가 **공개**여야 합니다.

```
python -m steam_buddy config --key <API키> --steamid <SteamID64>
python -m steam_buddy sync
```

첫 `sync`는 현재 보유 게임을 기준선으로만 저장합니다. 이후 새로 생긴 게임은 "오늘 구매"로 자동 기록되니, 날짜가 다르면 `buy --date`로 고치세요. API 키는 `data.json`에 평문 저장되니 공유하지 마세요.

## 폰 알림 (ntfy)

1. 폰에 ntfy 앱(https://ntfy.sh) 설치
2. 앱에서 주제(topic)를 구독. **ntfy 주제는 공개라서 이름만 알면 누구나 읽을 수 있습니다.** `steam-buddy` 같은 이름 말고 추측 어려운 긴 이름(예: `sb-k3x9q2m7vz41hd`)을 쓰세요.
3. 설정 후 테스트:

```
python -m steam_buddy config --ntfy sb-k3x9q2m7vz41hd
python -m steam_buddy alert
```

`alert`는 키가 설정돼 있으면 먼저 `sync`를 하고, "급함" 상태 게임이 있을 때만 알림을 보냅니다.

## 윈도우 작업 스케줄러로 하루 몇 번 실행

경로는 본인 환경에 맞게 바꾸세요. 아래는 4시간마다 실행하는 예입니다.

```
schtasks /create /tn "SteamBuddyAlert" /sc hourly /mo 4 ^
  /tr "cmd /c cd /d C:\Users\me\steam-buddy && python -m steam_buddy alert"
```

하루 3번 정해진 시각이라면 `/sc daily /st 09:00`으로 작업을 시각별로 따로 만들면 됩니다. 삭제는 `schtasks /delete /tn SteamBuddyAlert`.

## 테스트

```
python -m unittest discover -s tests -v
```
