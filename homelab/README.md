# homelab

한 번에 켜고 끄는 홈랩 런처

토큰 생성 → 프로젝트별 설정 파일 쓰기 → 서버 켜기/끄기/상태 → 매일 도는 작업 등록 → 폰에서 여는 법 → 점검까지,
이 저장소의 서버 프로젝트들(사이드킥, 폰 홈, 자비스, 일기, 물건 기억, 소리 감시 …)을 **명령 몇 개로** 묶습니다.
Python 3.12 이상, 외부 패키지 없음. 다른 프로젝트 폴더는 **읽기만** 하고(실행할 때 cwd 로만 씀) 아무것도 고치지 않습니다.

<!-- demo:start -->
<!-- demo:end -->

```
S21 ──Tailscale HTTPS──▶ tailscale serve ──▶ phone-home :8790 ──토큰 하나, 프록시──▶ sidekick :8765
                                                                              ├▶ voice-journal :8787
                                                                              ├▶ object-memory :8788
                                                                              ├▶ sound-watch :8770
                                                                              └▶ jarvis-hub :8800 ◀── jarvis-worker (5080)
```

## 처음 30분 설치 가이드 (Windows PC + 남는 S21)

준비물: Windows 10/11 PC(RTX 5080), Python 3.12 이상(설치 때 "Add python.exe to PATH" 체크), 이 저장소 폴더,
Tailscale(PC와 S21 둘 다), S21의 Chrome. 로컬 LLM 서버(llama-server 등)는 있으면 좋고 없어도 시작은 됩니다.
아래 명령은 PowerShell 에서 저장소의 `homelab` 폴더 안에서 실행합니다. `python` 이 안 되면 `py -3` 을 쓰세요.

### 1. 설정 만들기 (약 3분)

```powershell
cd C:\dev\jjy9558\homelab
python -m homelab init
```

질문이 나옵니다. 엔터만 치면 기본값입니다. 아무것도 묻지 않고 하려면 `--yes` 를 쓰고, 질문마다 같은 이름의 플래그가 있습니다.

```powershell
python -m homelab init --yes --llm-url http://127.0.0.1:8080/v1 --appids 570,730 --wallet-limit 20 --ntfy
python -m homelab init --dry-run --yes        # 아무것도 쓰지 않고 무엇이 만들어질지 diff 로만 보기 (토큰은 *** 로 가림)
```

이 한 번으로 다음이 만들어집니다.

- 서비스마다 **서로 다른 긴 무작위 토큰** → `%USERPROFILE%\.homelab\secrets.json` (토큰은 여기 한 곳이 원본)
- 각 프로젝트의 설정 파일 (아래 "파일 위치" 표). **이미 있는 파일은 건드리지 않습니다**. 덮어쓰려면 `--force` (먼저 `.bak-시각` 으로 백업).
- phone-home 이 뒤쪽 서비스에 붙을 때 쓰는 토큰은 서비스 쪽 토큰과 **같은 원본**에서 나옵니다 (`tokens\*.token` 파일을 `token_file` 로 참조).

> **윈도우 주의**: `secrets.json` 은 사용자 프로필 안에 있고, 가능하면 `icacls` 로 현재 사용자만 읽게 줄입니다(POSIX 에서는 권한 600).
> OneDrive/Dropbox 같은 동기화 폴더나 다른 사람과 공유하는 곳에 두지 마세요. 사용자 이름이 한글이어도 괜찮습니다.

### 2. 점검 (약 2분)

```powershell
python -m homelab doctor
```

✅ 는 괜찮음, ⚠️ 는 선택 기능/나중에 해도 됨, ❌ 는 먼저 고칠 것입니다. 항목마다 `→` 로 고치는 방법이 나옵니다.
(파이썬 버전, 각 프로젝트 import, 설정 파일을 각 프로젝트의 자기 로더로 읽어 보기, 토큰 일치, 포트, ffmpeg/nvidia-smi/pdftotext/tesseract, LLM 응답)

### 3. 로컬 LLM 켜기 (선택, 약 3분)

요약·판정이 LLM 을 쓰는 기능들이 있습니다. 예:

```powershell
llama-server -m C:\models\qwen2.5-14b-instruct-q5_k_m.gguf --port 8080 -ngl 99
```

주소가 다르면 `python -m homelab init --llm-url http://127.0.0.1:1234/v1 --force` (또는 `homelab.json` 의 `llm_url`).
LLM 이 꺼져 있어도 브리핑은 "추출 요약", 사이드킥 알람은 "수동 확인" 모드로 돕니다.

### 4. 서버 켜기 (약 2분)

```powershell
python -m homelab up
python -m homelab status
```

```
이름           상태       PID  포트  건강 확인  가동  설명
-------------  -------  -----  ----  ---------  ----  ------------------
sidekick       실행 중  11013  8765  정상       2초   S21 사이드킥
...
```

포트를 다른 프로그램이 쓰고 있으면 켜지 않고 알려 줍니다. 일부만: `up --only sidekick,phone-home`. 끄기: `down` (`--only` 가능).
로그: `python -m homelab logs sidekick -n 80` (크기 기준으로 회전: 기본 2MB × 3개).

### 5. 폰(S21)에서 열기 (약 10분)

1. PC와 S21 에 Tailscale 을 깔고 같은 계정으로 로그인. 관리 화면(https://login.tailscale.com/admin/dns)에서 **MagicDNS 와 HTTPS 인증서**를 켭니다.
2. PC에서 안내 명령을 확인합니다. (**phone-home 하나만** 엽니다)

   ```powershell
   python -m homelab tailscale --print
   tailscale serve --bg 8790
   ```

3. 폰 Chrome 에서 처음 한 번만 `https://<pc이름>.<tailnet>.ts.net/#token=<phone-home 토큰>` 으로 엽니다.
   토큰은 `python -m homelab tailscale --print --show-token` 이나 `python -m homelab token show phone-home` 으로 봅니다.
   쿠키로 바뀌어 주소창에서 지워지고 30일 유지됩니다. Chrome 메뉴 → 홈 화면에 추가.
4. 타일에서 **사이드킥**을 열고 위쪽 **시작**을 누릅니다(화면 켜짐 유지·소리 권한). 마이크/카메라는 HTTPS 에서만 되는데 phone-home 주소는 이미 HTTPS 입니다.
   충전기에 꽂아 세워 두세요. **소리 감시** 타일에서도 마이크를 허용합니다.
5. 알람 비상 해제 코드(6자리)는 `python -m homelab token show sidekick-emergency-code`.

> **Funnel 은 쓰지 마세요.** `tailscale funnel` 은 인터넷 전체에 공개합니다. 홈랩의 어떤 서비스도 Funnel 대상이 아닙니다.
> 다른 포트(8765, 8787 …)를 따로 serve 하지도 마세요. phone-home 이 토큰 하나로 프록시합니다.

### 6. 매일 도는 작업 등록 (약 5분)

```powershell
python -m homelab schedule --print      # 명령만 보기 (기본). 아래 cron 줄도 같이 나옵니다
python -m homelab schedule --install    # 윈도우 작업 스케줄러에 등록 (목록 확인 후 y)
```

| 작업 | 언제 | 하는 일 |
|---|---|---|
| community-briefing | 매일 06:30 | 관심사 커뮤니티 수집·요약 |
| morning-brief | 매일 06:50 | 아침 브리핑 만들기 + 사이드킥 폰이 읽어 줌 (`--speak`) |
| game-calendar | 매일 07:00 | 게임 점검/종료 일정 갱신 |
| til-drafter | 매일 23:30 | 오늘의 TIL 초안 |
| devguard-wallet | 매일 21:00 | Claude 사용량 한도 알림 |
| cooloff-check | 매일 20:30 | 쿨링오프 확인 |
| soundwatch-report | 매주 일요일 09:00 | 층간소음 주간 리포트 |
| life-nagger | 로그온할 때 | 생활 잔소리 데몬 (윈도우 전용) |

시간을 바꾸려면 `homelab.json` 의 `schedule_times` (`{"game-calendar": "06:20"}`)를 고치고 다시 `schedule`. 생각해 볼 점: 아침 브리핑(06:50)이 게임 캘린더(07:00)보다 먼저라
브리핑은 어제 상태를 읽습니다. 순서를 바꾸고 싶으면 위처럼 시간을 조정하세요.
스케줄러가 부르는 것은 `python homelab_run.py --dir ... run <작업>` 한 줄이고, **토큰은 명령줄에 없습니다**(실행할 때 `secrets.json` 에서 환경변수로 넣음).
`/tr` 따옴표는 공백·한글 경로에서도 맞도록 만든 뒤 거꾸로 풀어 검증합니다. 콘솔 창이 번쩍이는 게 싫으면 `--hidden`.
지우기: `schtasks /delete /tn Homelab-morning-brief /f`. 지금 바로 한 번 돌려 보기: `python -m homelab run morning-brief -- --dry-run`.

### 7. Claude Code 훅 (선택, 약 3분)

```powershell
python -m homelab hooks            # dev-guard 훅 + 사이드킥 rm -rf 결재 훅(--config 포함) 블록 출력
```

출력된 `hooks` 블록을 `~/.claude/settings.json` 에 직접 붙여 넣으세요(홈랩은 settings.json 을 고치지 않습니다).
dev-guard 의 단가표는 예시이니 공식 가격 페이지로 `~/.devguard/config.json` 의 `prices` 를 채우고 `prices_confirmed` 를 true 로 바꾸세요.

### 8. 끝 확인

```powershell
python -m homelab doctor
python -m homelab status
```

폰에서 phone-home 타일 점이 초록이면 완료입니다. 문제가 생기면 아래 "자주 막히는 곳".

## 미니PC까지 쓸 때 (선택)

항상 켜 둔 미니PC에 허브와 WOL/폰 알림 워커를, 5080 PC 에는 워커만 둡니다. 두 기기가 **같은 토큰**을 써야 하므로
`secrets.json` 을 안전한 방법(USB, scp 등)으로 미니PC 의 `~/.homelab/` 에 복사한 뒤 init 합니다(있는 토큰은 그대로 씀).

```bash
# 미니PC (Tailscale IP 가 100.64.0.10 이라고 가정)
python3 -m homelab init --yes --hub-host 100.64.0.10 --wol-mac AA:BB:CC:DD:EE:FF \
  --enable jarvis-worker-minipc --disable voice-journal,object-memory,sound-watch,phone-home,jarvis-worker
python3 -m homelab up

# 5080 PC
python -m homelab init --yes --hub-url http://100.64.0.10:8800 --disable jarvis-hub
```

미니PC 에서는 `python3 -m homelab schedule --print` 의 cron 줄을 `crontab -e` 에 붙여 넣으면 됩니다.
`--minipc` 만 주면 이 PC에서 미니PC 용 워커 설정 파일(`config/jarvis-worker-minipc.json`)만 만들어 둡니다.
(분리 구성은 같은 코드 경로를 테스트하지만 실제 두 대 환경에서는 확인하지 못했습니다.)

## 명령

| 명령 | 하는 일 |
|---|---|
| `init [--dir ~/.homelab] [--yes] [--force] [--dry-run] [--rotate-tokens]` | 토큰 생성 + 설정 파일 쓰기. 플래그: `--python --repo-root --llm-url --enable --disable --hub-host --hub-url --wol-mac --wol-broadcast --[no-]minipc --[no-]ntfy --wallet-limit --redact-words --gm-world --appids --[no-]morning-gate --tailnet-host` |
| `up [--only a,b] [--announce]` | 서버를 백그라운드로 켜기 (포트 충돌 검사, 켜진 뒤 상태 표) |
| `down [--only a,b]` | 끄기 (켠 순서의 반대로) |
| `status [--only a,b] [--all]` | 상태 표 (상태·PID·포트·건강 확인·가동 시간) |
| `logs <이름> [-n 40] [-f]` | 서비스/작업 로그 |
| `schedule [--print\|--install] [--only ...] [--yes] [--hidden]` | schtasks 명령 + cron 줄 |
| `tailscale [--print] [--show-token]` | `tailscale serve` 안내 (phone-home 만, Funnel 경고) |
| `doctor [--llm-url URL]` | 점검 (끝 코드 1 이면 ❌ 있음) |
| `run <작업> [-- 추가 인자]` | 주기 작업 한 번 실행 (스케줄러가 부름) |
| `token list\|show <이름>` | 토큰 보기 |
| `hooks [--out 파일]` | Claude Code 훅 블록 출력 |

공통 옵션: `--dir`(홈랩 폴더, 환경변수 `HOMELAB_DIR`), `--home`(`~/.phone-home` 같은 프로젝트 홈 설정의 기준 폴더. 시험할 때 유용).

## 서비스와 포트

| 이름 | 프로젝트 | 포트 | 켜는 명령 (홈랩이 쓰는 형태) | 토큰 전달 |
|---|---|---|---|---|
| sidekick | s21-sidekick | 8765 | `-m sidekick serve --config config\sidekick.json` | 설정 파일 `server.token` |
| voice-journal | voice-journal | 8787 | `-m vjournal --db ~\.vjournal\journal.db serve --port 8787` | `VJ_TOKEN` 환경변수 + `~\.vjournal\token` |
| object-memory | object-memory | 8788 | `-m objmem --config ~\.objmem\config.json serve` | 설정 파일 `token` |
| sound-watch | sound-watch | 8770 | `-m soundwatch serve --data-dir data\sound-watch` | 설정 파일 `server.token`, 사이드킥 알림은 `SIDEKICK_TOKEN` 환경변수 |
| jarvis-hub | jarvis-hub | 8800 | `-m jarvis hub --config config\jarvis-hub.json` | 설정 파일 `devices.*.token` |
| jarvis-worker | jarvis-hub | - | `-m jarvis worker --config config\jarvis-worker-main.json` | `JARVIS_TOKEN` 환경변수 (notify.say 만 설정 파일) |
| phone-home | phone-home | 8790 | `-m phonehome --config ~\.phone-home\config.json serve` | 클라이언트 토큰은 설정 파일, 뒤쪽 서비스는 `token_file` |
| gm-trpg (기본 꺼짐) | gm-trpg | 8766 | `-m gmtrpg serve --world 이름 --dir data\gm-trpg\worlds` | 인증 없음(127.0.0.1 전용). 세계를 먼저 `python -m gmtrpg new --dir …` 로 만들어야 함 |
| shorts-vote (기본 꺼짐) | shorts-feedback | 8791 | `-m shortsfb --data data\shorts-feedback vote serve` | 투표 링크 토큰. `vote create` 로 투표를 먼저 만들어야 함 |
| jarvis-worker-minipc (기본 꺼짐) | jarvis-hub | - | 미니PC 용 워커 | `JARVIS_TOKEN` |

켜기 목록은 `--enable gm-trpg,shorts-vote` / `--disable ...` 로 바꿉니다. 모든 서버는 `127.0.0.1` 에만 바인딩합니다(허브만 `--hub-host`).
실행은 항상 인자 목록(shell=False)이고, 서버마다 작은 감독자(`homelab/logrun.py`)가 출력을 로그 파일로 받아 적습니다.

## 파일 위치

| 무엇 | 어디 | 비고 |
|---|---|---|
| 홈랩 선택 | `~/.homelab/homelab.json` | python 경로, LLM 주소, 켤 서비스, 포트 덮어쓰기, 스케줄 시간 … |
| 토큰 모음 | `~/.homelab/secrets.json` | 원본. POSIX 600 / 윈도우는 사용자 프로필 안 |
| phone-home 뒤쪽 토큰 | `~/.homelab/tokens/*.token` | 설정의 `token_file` 이 읽음 |
| 사이드킥 / 자비스 / 브리핑 / 캘린더 / 아침 브리핑 / 잔소리 | `~/.homelab/config/*.json` | 켤 때 `--config` 로 넘김 |
| 소리 감시 | `~/.homelab/data/sound-watch/config.json` | `--data-dir` |
| phone-home | `~/.phone-home/config.json` | 문서화된 위치 |
| object-memory | `~/.objmem/config.json` | 문서화된 위치 (`data_dir` 도 같은 홈) |
| voice-journal 토큰 | `~/.vjournal/token` | 문서화된 위치 |
| til-drafter | `~/.til-drafter.json` | 문서화된 위치 |
| dev-guard | `~/.devguard/config.json` | 문서화된 위치 |
| PID / 로그 | `~/.homelab/run/`, `~/.homelab/logs/` | 로그: 서비스 `<이름>.log`, 작업 `job-<이름>.log` |

morning-brief 가 읽는 파일들(`brief-latest.json`, `cards-latest.json`, 게임 캘린더 `state.json`, 커뮤니티 브리핑 `out/`)은
모두 `~/.homelab/data/…` 아래에 있고, 설정 파일이 서로를 가리키도록 맞춰 둡니다(phone-home 책상이 읽는 카드 파일 포함).

## 자주 막히는 곳

| 증상 | 확인 |
|---|---|
| `up` 이 "포트 N 을 다른 프로그램이 쓰고 있어요" | 예전에 직접 켠 서버일 수 있어요. 끄거나 `homelab.json` 의 `"ports": {"sidekick": 8775}` 로 바꾸고 `init --force` |
| 상태가 "토큰 거절" | 다른 설정으로 켜진 서버. `doctor` 의 토큰 항목 → `init --force` 로 다시 맞춤 |
| `init` 했는데 doctor 가 토큰 불일치 | 이미 있던 설정 파일은 안 덮어써서 예전 토큰이 남은 것. `init --force` (백업 후 맞춤) |
| 켜자마자 꺼짐 | `homelab logs <이름>` 마지막 줄. 프로젝트 폴더 위치는 `homelab.json` 의 `repo_root` |
| gm-trpg 가 안 켜짐 | 세계 없음 → `python -m gmtrpg new --dir %USERPROFILE%\.homelab\data\gm-trpg\worlds` |
| 폰에서 마이크가 안 됨 | HTTP 로 열었기 때문. `https://…ts.net/` 주소로 여세요 |
| 스케줄이 안 돔 | 작업 스케줄러에서 `Homelab-…` 작업 → 마지막 결과. `homelab logs morning-brief` (작업 로그) |

## 데이터와 개인정보

- 홈랩은 `~/.homelab` 아래와, 각 프로젝트가 문서화한 홈 설정 파일(위 표)만 씁니다. 프로젝트 폴더에는 쓰지 않습니다.
- 외부로 나가는 것: 없음. 건강 확인과 LLM 확인은 127.0.0.1(또는 설정한 주소)로만 가고 환경변수 프록시는 쓰지 않습니다.
  ntfy 를 켜면(`--ntfy`) 각 프로젝트가 만든 알림이 `ntfy.sh` 로 나갑니다. 토픽은 무작위(`homelab-` + 16자리)이니 폰 ntfy 앱에서 `homelab token show ntfy-topic` 값을 구독하세요.
  (cool-off 는 토픽을 인자로만 받아서 그 작업의 명령줄에 토픽이 보입니다.)
- 토큰은 화면에 찍지 않습니다(`--dry-run` diff 도 가림). 보려면 `token show`.
- 기본 켜기에 들어 있는 모든 서버는 127.0.0.1 전용이고, 바깥은 `tailscale serve`(tailnet 안)로 phone-home 하나만 엽니다.

## 공통 기반 (jjkit)

`homelab/_jjkit/`에 jjkit 복사본(httpkit, notify, textwidth)이 들어 있습니다. **직접 고치지 말고** 원본 `jjkit/jjkit/`을 고친 뒤 저장소 루트에서 다시 복사하세요.

```
python tools/jjkit_sync.py homelab
python tools/jjkit_sync.py --check homelab
```

- `httpkit.generate_token` 으로 토큰을 만들고, `netutil`(자동 포함)로 프록시 없이 건강 확인, `notify.sidekick_say` 로 `up --announce`, `textwidth.table` 로 한글 상태 표를 그립니다.

## 테스트

```
cd homelab
python -m unittest discover -s tests -v
```

네트워크 없이 돕니다(임시 홈, 127.0.0.1 의 작은 가짜 서버, 가짜 opener/runner). 서비스마다 만든 설정을 **각 프로젝트의 자기 로더**
(`sidekick.config`, `jarvis.config`, `phonehome.config`, `objmem.config`, `soundwatch.config`, `briefing.config`, `mbrief.config`,
`gamecal.config`, `nagger.config`)로 읽어 확인하고(그 폴더가 없으면 건너뜀), 토큰 일관성, schtasks `/tr` 따옴표(공백·한글 경로, 거꾸로 풀어 검증),
up/down/status, 포트 충돌, 로그 회전, doctor(가짜 의존성)를 검사합니다.
