# C5 PC 닥터 (pc-doctor)

직접 조립한 게임/로컬 AI PC(예: RTX 5080, 대용량 RAM, BIOS·RAM 자주 만지는 사람, llama.cpp 사용자)를 위한
**Windows 11 전용 명령줄 도구**입니다. Python 3.12+ 표준 라이브러리만 씁니다.

Windows 가 아닌 PC에서 실시간 점검 명령(`snapshot`, `check`, `updates`, `tweaks list` 등)을 실행하면 "윈도우 전용 점검입니다" 와 함께 종료 코드 3 으로 끝납니다. '문제 없음'으로 판단하지 않습니다. 저장된 스냅샷을 `--snapshot <파일>` 로 지정하면 Windows 가 아니어도 점검할 수 있습니다.

| 기능 | 명령 | 하는 일 |
|---|---|---|
| PC 설정 스냅샷 | `snapshot`, `diff` | CPU·RAM(속도/슬롯/부품번호)·GPU(드라이버/VRAM/PCIe)·디스크·OS 빌드·전원 옵션·게임 모드/HAGS/VBS·시작 프로그램·상위 프로세스를 JSON으로 저장하고 두 시점 비교 |
| A6 병목 분석 | `check` | 규칙 기반 진단 (🔴 심각 / 🟡 주의 / 🔵 참고) + 해결 방법 |
| A6 업그레이드 추천 | `upgrade` | VRAM vs 모델 크기, RAM 대역폭(RAM-bound LLM), 저장장치 등 병목 기준 조언 (가격 정보 없음) |
| B7 윈11 짜증 해결 | `tweaks list/apply/restore` | 확장자 표시, 클래식 우클릭 메뉴, 위젯/검색 숨기기, 시작 메뉴 추천·광고 끄기 등 — **항상 백업 후 적용, 정확히 복원** |
| B15 업데이트 신호등 | `updates` | 최근 설치 업데이트·실패 기록·대기 업데이트를 내가 관리하는 "알려진 문제 목록"과 대조해 🟢/🟡/🔴 |
| B6 게임 모드 | `gamemode on/off/status/watch` | 전원 옵션 고성능 + 내가 지정한 백그라운드 프로그램/서비스 종료, off 때 원상 복구. `watch`는 게임 실행을 감지해 자동 전환 |

## 설치 / 실행

```powershell
# 저장소 폴더에서
py -m pc_doctor --help
# 또는 설치해서 명령으로
py -m pip install .
pc-doctor --help
```

이모지가 깨지면 Windows Terminal을 쓰세요 (출력은 UTF-8).

처음 한 번:

```powershell
pc-doctor init     # %LOCALAPPDATA%\pc-doctor\ 에 config.json, known_issues.json 예제 복사
notepad $env:LOCALAPPDATA\pc-doctor\config.json
```

`config.json` 주요 항목 (`examples/config.example.json` 참고)

- `expected_ram_mts`: RAM 정격(XMP/EXPO) 속도. 비우면 부품번호/SPD로 추정합니다.
- `model_dirs`: GGUF 모델 폴더 목록 → 업그레이드 조언과 페이지 파일 점검에 실제 모델 크기를 사용.
- `gamemode`: `power_plan`, `stop_processes`, `stop_services`, `game_exes`, `poll_seconds`, `relaunch_processes`.

모든 데이터는 `%LOCALAPPDATA%\pc-doctor\` 아래에 저장됩니다
(`snapshots\`, `backups\`, `gamemode_state.json`). 환경 변수 `PC_DOCTOR_HOME`으로 위치를 바꿀 수 있습니다.

## 사용법

### 스냅샷과 비교

```powershell
pc-doctor snapshot            # 수집 + 저장 (snapshot-YYYYMMDD-HHMMSS.json)
pc-doctor snapshot --print    # JSON도 출력
pc-doctor diff                # 최근 2개 비교
pc-doctor diff A.json B.json
```

`diff`는 단순 값 비교 외에 의미 있는 변화를 따로 짚어 줍니다. 예:

- `RAM 동작 속도 6000 → 4800 MT/s 하락! BIOS 초기화·업데이트로 XMP/EXPO가 꺼졌을 가능성이 큽니다.`
- `GPU 드라이버 변경: 576.88 → 581.15`, `BIOS 버전 1.A20 → 1.A30`, `GPU PCIe 링크 폭 x16 → x8 감소`
- 메모리 무결성/게임 모드/HAGS/전원 옵션 변경, 드라이브 여유 공간 20GB 이상 감소, 새 시작 프로그램

### 진단 (`check`)

```powershell
pc-doctor check               # 지금 상태로 진단 (저장 안 함, --save 로 저장)
pc-doctor check --snapshot 파일.json --json
```

규칙 목록 (`pc_doctor/checks.py`, 규칙 하나 = 함수 하나):

| id | 내용 |
|---|---|
| `ram_below_rated` | RAM이 정격보다 낮게 동작 → XMP/EXPO 확인 |
| `ram_single_channel` | RAM 1개, 또는 2개가 같은 채널 슬롯 |
| `ram_mixed_kits` | 서로 다른 키트 혼용 |
| `low_free_ram` | 여유 RAM 10% 미만 (상위 프로세스 표시) |
| `disk_almost_full` | 드라이브 90%(주의)/95%(심각) 이상 사용 |
| `system_disk_hdd` | C: 가 HDD |
| `vbs_on` | VBS/메모리 무결성 켜짐 (게임 성능 저하 가능 — 정보만, 변경하지 않음) |
| `power_plan_saver` | 전원 옵션 절전 |
| `gpu_driver_old` | GPU 드라이버가 오래됨 (기본 180일) |
| `gpu_pcie_width` | GPU가 x16보다 좁게 연결 |
| `startup_too_many` | 시작 프로그램 과다 (기본 15개 초과) |
| `pagefile_off` | 페이지 파일 꺼짐 (큰 모델이 RAM 절반 이상이면 주의) |
| `hags_off` | HAGS 꺼짐 (RTX 40/50 프레임 생성에 필요) |
| `game_mode_off` | 게임 모드 꺼짐 |

심각(🔴) 항목이 있으면 종료 코드 1을 돌려줍니다.

### 업그레이드 조언 (`upgrade`)

- 무료로 먼저 할 일 (XMP/EXPO, 듀얼 채널)
- 가장 큰 GGUF 모델이 VRAM에 다 올라가는지 → 안 되면 RAM 오프로드 크기와 RAM 대역폭 기준 토큰 속도 상한 추정
  (`대역폭 GB/s ÷ 오프로드 GB`), 양자화·MoE 전문가 CPU 배치·VRAM 큰 GPU 등 선택지
- 모델 + 여유분이 RAM을 넘으면 RAM 증설 목표 용량 (DDR5 4개 장착 시 속도 하락 주의)
- 시스템 드라이브 HDD/공간 부족, 코어 수, VRAM 부족

실시간 가격·특정 제품 추천은 하지 않습니다.

### 윈11 짜증 해결 프로필 (`tweaks`)

```powershell
pc-doctor tweaks list                      # 목록 + 현재 적용 상태
pc-doctor tweaks apply --dry-run           # 기본 프로필: 무엇이 바뀔지 미리 보기
pc-doctor tweaks apply                     # 기본 프로필 적용 (백업 먼저)
pc-doctor tweaks apply show_hidden_files mouse_no_acceleration
pc-doctor tweaks apply --restart-explorer  # 적용 후 탐색기 재시작
pc-doctor tweaks restore                   # 원래대로 (미복원 백업 전부, 최신부터)
pc-doctor tweaks restore --backup 파일.json
pc-doctor tweaks backups
```

| id | 기본 | 내용 |
|---|---|---|
| `show_extensions` | ✔ | 파일 확장자 표시 |
| `classic_context_menu` | ✔ | 클래식(전체) 우클릭 메뉴 |
| `taskbar_hide_widgets` | ✔ | 작업표시줄 위젯 버튼 숨기기 (일부 최신 빌드는 쓰기 차단 → 실패로 표시) |
| `taskbar_hide_search` | ✔ | 작업표시줄 검색 상자 숨기기 |
| `start_no_recommendations` | ✔ | 시작 메뉴 추천/최근 항목 줄이기 |
| `no_web_search_in_start` | ✔ | 시작 메뉴 검색의 웹(Bing) 결과 끄기 |
| `no_ads_suggestions` | ✔ | 광고성 제안·팁·자동 설치 앱·잠금화면 광고·탐색기 OneDrive 광고 끄기 |
| `show_hidden_files` |  | 숨김 파일 표시 |
| `taskbar_hide_taskview` |  | 작업 보기 버튼 숨기기 |
| `no_background_recording` |  | Xbox Game Bar 백그라운드 녹화 끄기 |
| `mouse_no_acceleration` |  | 마우스 가속 끄기 |
| `widgets_policy` | 관리자 | 위젯 기능 자체 끄기 (정책) |
| `disable_fast_startup` | 관리자 | 빠른 시작 끄기 (BIOS/RAM 설정 자주 바꾸면 추천) |

트윅은 `pc_doctor/tweaks_data.py`에 **데이터**(레지스트리 경로/값 이름/형식/데이터, 재시작 필요 여부)로만 정의되어 있습니다.

### 윈도우 업데이트 신호등 (`updates`)

```powershell
pc-doctor updates               # 설치 기록 + 실패 기록 + 대기 업데이트 검색 (검색은 수십 초 걸릴 수 있음)
pc-doctor updates --no-pending  # 대기 업데이트 검색 생략
pc-doctor updates --issues D:\my-known-issues.json --days 30
```

- 🔴: 대기/최근 설치 업데이트가 알려진 문제 목록의 `red` 항목과 일치
- 🟡: `yellow` 항목 일치, 최근 설치 실패, 마지막 업데이트가 60일 넘음
- 🟢: 위에 해당 없음 (= **내 목록 기준**으로 문제 없음)

알려진 문제 목록(`known_issues.json`)은 **직접 관리**합니다. pc-doctor는 KB 번호를 지어내지 않습니다.
`examples/known_issues.example.json`의 `KB0000000`, `KB0000001`은 형식을 보여 주는 **가짜 번호**입니다.
실제 항목은 Microsoft "Windows 릴리스 상태(Release health)" 등에서 확인한 뒤 `kb` 또는 `title_contains`로 적으세요.
대기 업데이트는 관리자 권한 없는 Windows Update COM API로 검색하며, 실패하면 "감지 불가"로 표시합니다.

### 게임 모드 (`gamemode`)

```powershell
pc-doctor gamemode on --dry-run   # 계획만 보기
pc-doctor gamemode on             # 확인 후 적용 (--yes 로 생략)
pc-doctor gamemode off            # 원래 전원 옵션·서비스 복원, 종료했던 프로그램 다시 실행
pc-doctor gamemode status
pc-doctor gamemode watch          # game_exes가 실행되면 자동 on, 모두 꺼지면 자동 off
```

- 서비스 중지/시작은 관리자 터미널이 필요합니다.
- `watch`가 켠 게임 모드만 자동으로 끕니다 (직접 `on` 한 건 그대로 둠). Ctrl+C로 종료하면 자동으로 켠 것은 복원합니다.

## 안전 모델

1. **미리 보기**: `tweaks apply --dry-run`, `tweaks restore --dry-run`, `gamemode on --dry-run`은 읽기만 하고 아무것도 쓰지 않습니다(백업 파일도 만들지 않음).
2. **백업 먼저**: `tweaks apply`는 바꾸기 전에 대상 값마다 이전 형식·데이터 또는 **"원래 없었음"**을
   `%LOCALAPPDATA%\pc-doctor\backups\tweaks-*.json`에 저장(fsync)합니다. 백업 저장에 실패하면 아무것도 바꾸지 않습니다.
3. **정확한 복원**: `restore`는 있던 값은 원래 형식으로 되쓰고, 없던 값은 삭제합니다. 클래식 메뉴처럼 *키가 있는 것 자체*가
   의미 있는 경우 원래 없던 키를 통째로 지웁니다. 여러 번 apply 했어도 미복원 백업을 최신부터 거꾸로 되돌리므로 맨 처음 상태로 돌아갑니다.
   (그 밖에 값을 쓰면서 새로 생긴 빈 상위 키는 남을 수 있지만 동작에는 영향이 없습니다.)
4. **HKCU 기본**: 관리자 권한(HKLM)이 필요한 트윅은 표시되고, `--admin` 없이는 건너뜁니다.
5. **절대 건드리지 않는 것**: Microsoft Defender(백신), UAC(사용자 계정 컨트롤), Windows 방화벽, SmartScreen,
   VBS/메모리 무결성(코어 격리), LSA 보호, Windows Update 정책. 이런 경로를 가리키는 트윅은 정의 단계에서 거부됩니다
   (`tweaks_data.FORBIDDEN_FRAGMENTS`). `check`가 VBS가 켜져 있다고 알려 줄 수는 있지만, 끌지 말지는 사용자가 Windows 보안 화면에서 직접 판단합니다.
6. **게임 모드**: config에 **직접 적은** 프로세스/서비스만 대상으로 하며, 시스템 필수·보안 항목(lsass, csrss, explorer, svchost,
   MsMpEng, WinDefend, mpssvc, wuauserv 등)은 적어 두어도 거부합니다. 확인을 받고(`--yes` 제외), 바꾸기 전에 이전 상태를 저장하고,
   프로그램은 강제 종료가 아니라 정상 종료 요청(`taskkill` without `/F`)을 보냅니다.
7. **셸을 거치지 않음**: 모든 Windows 호출은 `pc_doctor/system.py` 한 곳에서 인자 리스트로 실행합니다(`shell=True` 없음, 테스트로 검사).
   PowerShell 스크립트는 전부 조회 전용 상수이고, 사용자 입력을 스크립트 문자열에 끼워 넣지 않습니다. 레지스트리는 표준 라이브러리 `winreg`로 다룹니다.

## 주간 스냅샷 자동화 (작업 스케줄러)

매주 스냅샷을 남겨 두면 "언제부터 RAM이 4800으로 돌았지?" 같은 걸 바로 찾을 수 있습니다.

```powershell
$py = (Get-Command py).Source
$action = New-ScheduledTaskAction -Execute $py -Argument "-m pc_doctor snapshot" -WorkingDirectory "C:\path\to\pc-doctor"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 9pm
Register-ScheduledTask -TaskName "pc-doctor 주간 스냅샷" -Action $action -Trigger $trigger
```

(`pip install .` 했다면 `-Execute` 에 `pc-doctor.exe` 경로, `-Argument "snapshot"`.) BIOS 업데이트 전후로
`pc-doctor snapshot` → `pc-doctor diff` 를 직접 돌리는 습관도 추천합니다.

## 개발 / 테스트

```bash
python3 -m unittest discover -s tests -v
```

테스트는 `tests/fakes.py`의 `FakeSystem`(가짜 레지스트리, PowerShell JSON 견본, 가짜 tasklist/sc/powercfg/nvidia-smi 출력)으로
OS와 무관하게 돌아갑니다. 실제 Windows 호출(`WindowsSystem`)은 테스트에서 실행되지 않습니다.
