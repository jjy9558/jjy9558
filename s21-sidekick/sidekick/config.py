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
    if problems:
        raise ConfigError("\n".join(problems))
    return cfg


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return validate(build_config(json.load(f)))
