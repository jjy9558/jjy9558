"""설정 로드: 기본값 위에 config.json을 깊게 병합한다."""
import copy
import json
import shlex

DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": 8765, "token": "", "max_body_mb": 8},
    # 텍스트 요약용 LLM (llama-server 등 OpenAI 호환). base_url 비우면 사용 안 함.
    "llm": {"base_url": "", "api_key": "", "model": "local", "timeout": 60},
    # 알람 사진 판정용 VLM. base_url 비우면 수동 확인(카메라 N초 + 산수) 모드.
    "vision": {"base_url": "", "api_key": "", "model": "local", "timeout": 60},
    "debugger": {
        "enabled": True,
        "triggers": ["왜 안 돼", "왜 안되지", "왜 이래", "에러 뭐야", "오류 뭐야",
                     "뭐가 문제야", "또 터졌네", "이게 왜"],
        "cooldown_seconds": 60,
        "mode": "log_tail",  # "log_tail" | "command"
        "log_file": "",
        "tail_lines": 200,
        "error_pattern": r"(?i)(error|exception|traceback|failed|fatal|panic|에러|오류|실패)",
        "command": ["claude", "-p", "{prompt}"],
        "cwd": "",
        "timeout_seconds": 180,
        "prompt_template": (
            "사용자가 방금 혼잣말로 '{utterance}'라고 했다. 이 프로젝트의 최근 에러/실패 원인을 "
            "조사하고, 폰 스피커로 읽어줄 한국어 2문장 이내로만 답해라. 코드는 수정하지 마라."
        ),
        "summarize_with_llm": True,
        "max_speech_chars": 220,
    },
    "alarm": {
        "enabled": True,
        "time": "07:00",
        "snooze_minutes": 5,
        "max_snoozes": 2,
        "max_ring_minutes": 60,
        "emergency_code": "",
        "fallback_camera_seconds": 45,
        "requirement": "칫솔을 들고 있는 얼굴, 또는 욕실 세면대 앞에 서 있는 모습",
    },
    "butler": {
        "enabled": True,
        "cooldown_seconds": 600,
        "providers": [
            {"name": "greeting"},
            {"name": "calendar_ics", "path": ""},
            {"name": "claude_status", "path": ""},
            {"name": "weather"},
            {"name": "delivery"},
        ],
        "motion": {"threshold": 18, "min_changed_ratio": 0.08},
    },
    # 현관 출발 체크: provider 결과를 이어 붙여 읽는다 (butler provider 재사용).
    "departure": {
        "enabled": True,
        "cooldown_seconds": 300,
        "intro": "출발 체크할게요.",
        "outro": "다녀오세요!",
        "providers": [
            # 네트워크가 필요하므로 기본 꺼짐. 켜려면 enabled: true + 위도/경도.
            {"name": "open_meteo", "enabled": False, "latitude": 37.5665, "longitude": 126.9780,
             "umbrella_threshold": 50, "timeout": 4},
            {"name": "calendar_next", "path": ""},
            {"name": "reminders", "items": ["지갑", "열쇠", "휴대폰 충전기"]},
            # nvidia-smi로 GPU 사용률 확인 (선택, 기본 꺼짐)
            {"name": "gpu_busy", "enabled": False, "threshold": 30},
        ],
    },
    # 커밋 관중석: python -m sidekick notify <event>
    "stadium": {
        "enabled": True,
        "max_per_minute": 4,
        "min_interval_seconds": 3,
        "big_pr_lines": 300,  # pr_merged에 --lines를 주면 이보다 작을 때는 불꽃 대신 박수
    },
    # rm -rf 결재 보고: Claude Code PreToolUse 훅 (python -m sidekick hook)
    "approval": {
        "enabled": True,
        "timeout_seconds": 45,  # Claude Code 훅 기본 제한(60초)보다 짧게. 늘리려면 settings.json의 timeout도 늘릴 것
        "on_timeout": "deny",   # "deny" | "ask"(Claude Code의 원래 확인 창으로 넘김)
        "fail_open": False,     # 서버에 못 붙으면: False=막음, True="ask"로 넘김
        "tools": ["Bash", "PowerShell"],
        "risky_patterns": None,  # None이면 기본 목록(sidekick/hook.py DEFAULT_PATTERNS). 리스트로 주면 교체
        "extra_patterns": [],    # 기본 목록에 추가: "정규식" 또는 {"pattern": ..., "label": ...}
        "allow_style": "json",   # "json"(Claude Code 확인 창 생략) | "passthrough"(원래 흐름대로)
        "deny_style": "exit2",   # "exit2"(stderr + 종료 코드 2, 가장 확실) | "json"
        "max_pending": 10,
        "url": "",               # 훅이 붙을 서버 주소. 비우면 http://127.0.0.1:<server.port>
    },
    # 외부 프로그램이 폰에 말 시키기: POST /api/say (토큰 필요)
    "say": {"enabled": True, "max_per_minute": 12, "min_interval_seconds": 1, "max_chars": 300},
    # 선택(기본 꺼짐): 폰 버튼 이벤트를 다른 로컬 프로그램으로 전달.
    # 예: [{"url": "http://127.0.0.1:8799/api/event", "token_env": "NAGGER_EVENTS_TOKEN", "types": ["sigh"]}]
    "forward_events": [],
    # 매크로 패드: 폰 버튼/NFC 태그 -> POST /api/macro {"id"} -> PC가 *여기 적힌* 동작만 실행. 자세한 형식은 README.
    "macro": {
        "enabled": True,
        "max_per_minute": 20,
        "min_interval_seconds": 0.5,
        "max_chain": 8,
        "audit_keep": 200,   # 메모리에 남기는 감사 로그 수
        "audit_file": "",    # 비우면 파일 저장 안 함. 경로를 주면 JSON Lines로 덧붙임
        "items": [],
    },
    "stt": {"model": "small", "device": "auto", "compute_type": "default", "language": "ko"},
}


class ConfigError(ValueError):
    pass


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def build_config(raw=None):
    cfg = _merge(DEFAULTS, raw or {})
    cmd = cfg["debugger"]["command"]
    if isinstance(cmd, str):  # config는 신뢰하는 입력이므로 여기서만 분해한다
        cfg["debugger"]["command"] = shlex.split(cmd)
    return cfg


def validate(cfg):
    problems = []
    token = cfg["server"]["token"]
    if not token or token in ("change-me", "CHANGE_ME") or token.startswith("여기에"):
        problems.append("server.token을 길고 무작위한 값으로 설정하세요 (예: python -c \"import secrets;print(secrets.token_urlsafe(24))\").")
    if cfg["alarm"]["enabled"] and not str(cfg["alarm"]["emergency_code"]).strip():
        problems.append("alarm.emergency_code(비상 해제 코드)를 설정하세요. 갇히지 않기 위한 안전장치입니다.")
    if cfg["debugger"]["mode"] not in ("log_tail", "command"):
        problems.append("debugger.mode는 'log_tail' 또는 'command'여야 합니다.")
    if cfg["approval"]["on_timeout"] not in ("deny", "ask"):
        problems.append("approval.on_timeout은 'deny' 또는 'ask'여야 합니다.")
    from .macro import validate_macros
    problems += validate_macros(cfg["macro"], token)
    if problems:
        raise ConfigError("\n".join(problems))
    return cfg


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return validate(build_config(json.load(f)))
