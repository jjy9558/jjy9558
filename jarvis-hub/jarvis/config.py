"""설정 로딩: 기본값 병합 + 검증. 허브(hub.json), 워커(worker.json), 클라이언트(client.json)."""
import copy
import json
import os
import re

DEVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")
TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z0-9_-]+)*$")
RESERVED_DEVICES = ("hub",)
MIN_TOKEN_LEN = 16


class ConfigError(ValueError):
    pass


HUB_DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": 8800, "max_body_kb": 256},
    "db": "jarvis.db",
    # "s21": {"token": "...", "label": "S21", "worker": false, "approve": true, "submit": ["*"]}
    "devices": {},
    "queue": {
        "default_lease_seconds": 300,
        "max_lease_seconds": 7200,
        "default_max_attempts": 3,
        "max_attempts_limit": 10,
        "retry_delay_seconds": 5,
        "max_payload_kb": 64,
        "max_result_kb": 256,
        "max_active_jobs": 500,
        "keep_events": 1000,
        "max_lease_wait_seconds": 30,
    },
    "presence": {"offline_after_seconds": 45},
    # 메인 PC Wake-on-LAN. gpu 작업이 들어왔는데 처리할 워커가 없으면 허브가 wake 작업을 넣는다.
    "wake": {"enabled": False, "label": "5080", "mac": "", "broadcast": "255.255.255.255", "port": 9,
             "for_caps": ["gpu"], "cooldown_seconds": 300},
    # 이 타입은 '신뢰(trusted)' 기기가 아닌 곳에서 넣으면 승인 대기로 들어간다.
    "approval": {"required_types": ["claude.task"]},
    "notify": {
        "say_events": True,   # 허브 이벤트 스트림에 kind=say 이벤트 (대시보드/폰이 읽어줌)
        "say_job": True,      # notify.say 작업을 넣어 s21-sidekick으로 전달
        "say_caps": [],
        "say_ttl_seconds": 600,  # 이 시간 안에 처리 안 된 notify.say 작업은 만료
        "ntfy": {"enabled": False, "server": "https://ntfy.sh", "topic": "", "token": "", "priority": 3,
                 "timeout": 5},
    },
    "router": {
        "wake_words": ["자비스", "쟈비스", "jarvis"],
        "require_wake_word": True,
        "llm_fallback": False,
        "llm_caps": ["gpu"],
        "claude_caps": [],
        "claude_repo": "",
        # "백업": {"name": "backup", "args": {}}  ->  "자비스, 백업 실행해"
        "shell_aliases": {},
    },
    "schedule": {"path": ""},
    "tick_seconds": 5,
}

WORKER_DEFAULTS = {
    "hub": "http://127.0.0.1:8800",
    "device": "",
    "token": "",
    "caps": [],
    "concurrency": 1,
    "lease_wait_seconds": 20,
    "heartbeat_seconds": 10,
    "retry_backoff_seconds": 5,
    "handlers": {
        "llm.chat": {"base_url": "", "model": "local", "api_key": "", "timeout": 300,
                     "max_tokens": 800, "files_dir": "", "max_file_chars": 40000,
                     "pdftotext": "pdftotext"},
        # "backup": {"argv": ["robocopy", "C:/work", "D:/backup", "/MIR"], "cwd": "", "timeout": 3600,
        #            "params": {"target": {"choices": ["D:/backup", "E:/backup"]}}}
        "shell.allowed": {"commands": {}, "max_output_chars": 8000},
        "claude.task": {"enabled": False, "command": ["claude", "-p", "{prompt}"], "repos": {},
                        "timeout": 1800, "max_prompt_chars": 8000, "max_output_chars": 20000},
        "notify.say": {"enabled": False, "sidekick_url": "", "token": "", "path": "/api/say",
                       "timeout": 5},
        "wake": {"enabled": False, "repeat": 3},
    },
}


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _read(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ConfigError("%s: JSON 객체가 필요합니다" % path)
    return raw


def check_tags(tags, what="caps"):
    if not isinstance(tags, (list, tuple)):
        raise ConfigError("%s는 목록이어야 합니다" % what)
    out = []
    for t in tags:
        if not isinstance(t, str) or not TAG_RE.match(t):
            raise ConfigError("%s 항목이 잘못됐습니다: %r (소문자/숫자/._- 32자 이내)" % (what, t))
        if t not in out:
            out.append(t)
    return out


def build_hub_config(raw):
    cfg = deep_merge(HUB_DEFAULTS, raw)
    errors = []
    devices = cfg.get("devices")
    if not isinstance(devices, dict) or not devices:
        errors.append("devices: 기기를 하나 이상 등록하세요 (각 기기마다 token)")
        devices = {}
    tokens = set()
    for did, d in devices.items():
        if not DEVICE_ID_RE.match(did) or did in RESERVED_DEVICES:
            errors.append("devices.%s: 기기 id는 소문자/숫자/_- 32자 이내 ('hub'은 예약어)" % did)
            continue
        if not isinstance(d, dict):
            errors.append("devices.%s: 객체여야 합니다" % did)
            continue
        tok = d.get("token")
        if not isinstance(tok, str) or len(tok) < MIN_TOKEN_LEN:
            errors.append("devices.%s.token: %d자 이상 필요 (python -m jarvis token)" % (did, MIN_TOKEN_LEN))
        elif tok in tokens:
            errors.append("devices.%s.token: 기기마다 다른 토큰을 쓰세요" % did)
        else:
            tokens.add(tok)
        d.setdefault("label", did)
        d.setdefault("worker", False)
        d.setdefault("approve", False)
        d.setdefault("trusted", False)
        d.setdefault("submit", ["*"])
        try:
            if "caps" in d:
                d["caps"] = check_tags(d["caps"], "devices.%s.caps" % did)
        except ConfigError as e:
            errors.append(str(e))
    try:
        cfg["wake"]["for_caps"] = check_tags(cfg["wake"]["for_caps"], "wake.for_caps")
        cfg["router"]["llm_caps"] = check_tags(cfg["router"]["llm_caps"], "router.llm_caps")
        cfg["router"]["claude_caps"] = check_tags(cfg["router"]["claude_caps"], "router.claude_caps")
        cfg["notify"]["say_caps"] = check_tags(cfg["notify"]["say_caps"], "notify.say_caps")
    except ConfigError as e:
        errors.append(str(e))
    if cfg["wake"]["enabled"]:
        from .wol import parse_mac
        try:
            parse_mac(cfg["wake"]["mac"])
        except ValueError as e:
            errors.append("wake.mac: %s" % e)
    if errors:
        raise ConfigError("\n".join(errors))
    return cfg


def load_hub_config(path):
    return build_hub_config(_read(path))


def build_worker_config(raw, overrides=None):
    cfg = deep_merge(WORKER_DEFAULTS, raw)
    for k, v in (overrides or {}).items():
        if v is not None:
            cfg[k] = v
    if isinstance(cfg["caps"], str):
        cfg["caps"] = [c.strip() for c in cfg["caps"].split(",") if c.strip()]
    errors = []
    if not cfg.get("device") or not DEVICE_ID_RE.match(cfg["device"]):
        errors.append("device: 허브 hub.json에 등록한 기기 id가 필요합니다")
    if not cfg.get("token"):
        errors.append("token: 기기 토큰이 필요합니다 (또는 환경변수 JARVIS_TOKEN)")
    try:
        cfg["caps"] = check_tags(cfg["caps"])
    except ConfigError as e:
        errors.append(str(e))
    cmd = cfg["handlers"]["claude.task"]["command"]
    if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) for a in cmd):
        errors.append("handlers.claude.task.command: 문자열 목록이어야 합니다")
    elif cfg["handlers"]["claude.task"]["enabled"] and "{prompt}" not in cmd:
        errors.append("handlers.claude.task.command: '{prompt}' 를 독립된 항목으로 넣으세요")
    if errors:
        raise ConfigError("\n".join(errors))
    return cfg


def load_worker_config(path=None, overrides=None, environ=None):
    env = os.environ if environ is None else environ
    raw = _read(path) if path else {}
    ov = dict(overrides or {})
    if not ov.get("token") and env.get("JARVIS_TOKEN"):
        ov["token"] = env["JARVIS_TOKEN"]
    if not ov.get("hub") and env.get("JARVIS_HUB") and "hub" not in raw:
        ov["hub"] = env["JARVIS_HUB"]
    return build_worker_config(raw, ov)


def load_client_settings(path=None, hub=None, device=None, token=None, environ=None):
    """say/submit/status CLI용: --옵션 > 환경변수 > client.json."""
    env = os.environ if environ is None else environ
    raw = _read(path) if path and os.path.exists(path) else {}
    return {
        "hub": (hub or env.get("JARVIS_HUB") or raw.get("hub") or "http://127.0.0.1:8800").rstrip("/"),
        "device": device or env.get("JARVIS_DEVICE") or raw.get("device") or "",
        "token": token or env.get("JARVIS_TOKEN") or raw.get("token") or "",
    }
