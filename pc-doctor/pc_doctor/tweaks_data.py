"""윈11 짜증 해결 프로필 — 데이터로만 정의합니다.

필드
- id, title, description
- values: [{path, name, type, data}]  (name "" = 키의 (기본값))
- restart: None | "explorer" | "logoff" | "reboot"
- default: 기본 프로필(`tweaks apply`에 id를 안 주면 적용되는 목록)에 포함 여부
- cleanup_keys: 적용 전에 없던 키였다면 복원 시 키째로 삭제할 경로
- HKLM 경로가 하나라도 있으면 관리자 전용 → `--admin` 없이는 건너뜀

절대 건드리지 않는 것: Defender, UAC, 방화벽, SmartScreen, VBS/메모리 무결성, Windows Update 정책.
`FORBIDDEN_FRAGMENTS`에 걸리는 경로는 로드 시점에 거부됩니다 (tweaks.validate_tweak).
"""

ADV = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
CDM = r"HKCU\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
CLASSIC_MENU_CLSID = r"HKCU\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}"


def _dw(path, name, data):
    return {"path": path, "name": name, "type": "REG_DWORD", "data": data}


TWEAKS = [
    {
        "id": "show_extensions",
        "title": "파일 확장자 표시",
        "description": "탐색기에서 .exe/.pdf 등 확장자를 항상 표시 (가짜 확장자 피싱 방지에도 도움).",
        "values": [_dw(ADV, "HideFileExt", 0)],
        "restart": "explorer",
        "default": True,
    },
    {
        "id": "show_hidden_files",
        "title": "숨김 파일 표시",
        "description": "탐색기에서 숨김 파일/폴더 표시 (시스템 보호 파일은 계속 숨김).",
        "values": [_dw(ADV, "Hidden", 1)],
        "restart": "explorer",
        "default": False,
    },
    {
        "id": "classic_context_menu",
        "title": "클래식 우클릭 메뉴",
        "description": "윈11 축약 메뉴 대신 '추가 옵션 표시' 없이 바로 전체 메뉴.",
        "values": [{"path": CLASSIC_MENU_CLSID + r"\InprocServer32", "name": "", "type": "REG_SZ", "data": ""}],
        "cleanup_keys": [CLASSIC_MENU_CLSID],
        "restart": "explorer",
        "default": True,
    },
    {
        "id": "taskbar_hide_widgets",
        "title": "작업표시줄 위젯 버튼 숨기기",
        "description": "날씨/뉴스 위젯 버튼 숨김. 일부 최신 빌드는 이 값 쓰기를 막아 둬서 실패할 수 있습니다 "
                       "(그럴 땐 widgets_policy를 --admin으로).",
        "values": [_dw(ADV, "TaskbarDa", 0)],
        "restart": "explorer",
        "default": True,
    },
    {
        "id": "widgets_policy",
        "title": "위젯 기능 끄기 (정책, 관리자)",
        "description": "그룹 정책과 같은 값으로 위젯(뉴스 및 관심사) 자체를 끕니다.",
        "values": [_dw(r"HKLM\SOFTWARE\Policies\Microsoft\Dsh", "AllowNewsAndInterests", 0)],
        "restart": "reboot",
        "default": False,
    },
    {
        "id": "taskbar_hide_search",
        "title": "작업표시줄 검색 상자 숨기기",
        "description": "검색은 시작 메뉴에서 바로 타이핑하면 그대로 됩니다.",
        "values": [_dw(r"HKCU\Software\Microsoft\Windows\CurrentVersion\Search", "SearchboxTaskbarMode", 0)],
        "restart": "explorer",
        "default": True,
    },
    {
        "id": "taskbar_hide_taskview",
        "title": "작업 보기 버튼 숨기기",
        "description": "작업표시줄의 작업 보기 버튼 숨김 (Win+Tab은 그대로).",
        "values": [_dw(ADV, "ShowTaskViewButton", 0)],
        "restart": "explorer",
        "default": False,
    },
    {
        "id": "start_no_recommendations",
        "title": "시작 메뉴 추천 줄이기",
        "description": "시작 메뉴의 팁·바로가기·새 앱 추천과 최근 연 항목 표시 끄기.",
        "values": [_dw(ADV, "Start_IrisRecommendations", 0), _dw(ADV, "Start_TrackDocs", 0)],
        "restart": "explorer",
        "default": True,
    },
    {
        "id": "no_web_search_in_start",
        "title": "시작 메뉴 검색에서 웹(Bing) 결과 끄기",
        "description": "로컬 앱/파일 검색만 남깁니다 (사용자 정책 키, 관리자 불필요).",
        "values": [_dw(r"HKCU\Software\Policies\Microsoft\Windows\Explorer", "DisableSearchBoxSuggestions", 1)],
        "restart": "explorer",
        "default": True,
    },
    {
        "id": "no_ads_suggestions",
        "title": "광고성 제안·팁 끄기",
        "description": "시작/설정/잠금화면의 '제안', 자동 설치 앱, 탐색기 OneDrive 광고 알림 끄기.",
        "values": [
            _dw(CDM, "SubscribedContent-338388Enabled", 0),
            _dw(CDM, "SubscribedContent-338389Enabled", 0),
            _dw(CDM, "SubscribedContent-338387Enabled", 0),
            _dw(CDM, "SubscribedContent-353694Enabled", 0),
            _dw(CDM, "SubscribedContent-353696Enabled", 0),
            _dw(CDM, "SystemPaneSuggestionsEnabled", 0),
            _dw(CDM, "SilentInstalledAppsEnabled", 0),
            _dw(CDM, "RotatingLockScreenOverlayEnabled", 0),
            _dw(ADV, "ShowSyncProviderNotifications", 0),
        ],
        "restart": "explorer",
        "default": True,
    },
    {
        "id": "no_background_recording",
        "title": "Xbox Game Bar 백그라운드 녹화 끄기",
        "description": "게임 중 상시 녹화(Game DVR)를 꺼서 GPU 인코더·디스크 사용을 줄입니다.",
        "values": [
            _dw(r"HKCU\System\GameConfigStore", "GameDVR_Enabled", 0),
            _dw(r"HKCU\Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", 0),
        ],
        "restart": "logoff",
        "default": False,
    },
    {
        "id": "mouse_no_acceleration",
        "title": "마우스 가속(포인터 정확도 향상) 끄기",
        "description": "FPS 게임용. 다시 로그인하면 적용됩니다.",
        "values": [
            {"path": r"HKCU\Control Panel\Mouse", "name": "MouseSpeed", "type": "REG_SZ", "data": "0"},
            {"path": r"HKCU\Control Panel\Mouse", "name": "MouseThreshold1", "type": "REG_SZ", "data": "0"},
            {"path": r"HKCU\Control Panel\Mouse", "name": "MouseThreshold2", "type": "REG_SZ", "data": "0"},
        ],
        "restart": "logoff",
        "default": False,
    },
    {
        "id": "disable_fast_startup",
        "title": "빠른 시작 끄기 (관리자, 선택)",
        "description": "빠른 시작은 완전 종료 대신 최대 절전을 써서, BIOS/RAM 설정 변경이나 듀얼부팅 시 문제를 일으킬 수 있습니다.",
        "values": [_dw(r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power", "HiberbootEnabled", 0)],
        "restart": "reboot",
        "default": False,
    },
]

# 이 문자열이 경로에 들어간 트윅은 정의 자체가 거부됩니다 (대소문자 무시).
FORBIDDEN_FRAGMENTS = [
    "windows defender",
    "microsoft antimalware",
    r"currentversion\policies\system",  # UAC (EnableLUA, ConsentPromptBehavior...)
    "firewallpolicy",
    r"\windowsfirewall",
    "smartscreen",
    r"\system\currentcontrolset\control\deviceguard",  # VBS / 메모리 무결성
    r"\control\lsa",
    "securityhealth",
    r"policies\microsoft\windows\windowsupdate",
    r"\services\windefend",
    r"\services\mpssvc",
    r"\services\wscsvc",
]
