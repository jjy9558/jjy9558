"""설정: 기본값, 파일 읽기/쓰기, 검증.

설정 파일 기본 위치는 ``~/.phone-home/config.json`` (환경변수 ``PHONE_HOME_CONFIG``로 바꿈).
클라이언트 토큰은 ``server.token`` 또는 환경변수 ``PHONE_HOME_TOKEN``.
뒤쪽 서비스 토큰은 서비스마다 ``token`` / ``token_env`` / ``token_file`` 중 하나.
"""
import copy
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from ._jjkit.httpkit import generate_token

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
PLACEHOLDER = "via-phone-home"  # 프록시된 페이지가 '토큰 없음' 화면을 띄우지 않게 넣는 가짜 값(서버가 버림)

# 이 폴더가 저장소 안에 있을 때 morning-brief 계약 파일 기본 위치
REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SERVICES = {
    "sidekick": {
        "title": "사이드킥", "icon": "🎙️", "desc": "결재 · 매크로 · 알람",
        "url": "http://127.0.0.1:8765", "token_header": "X-Sidekick-Token", "health": "/api/ping",
        "page": "/", "page_storage": {"sidekickToken": PLACEHOLDER},
    },
    "object-memory": {
        "title": "물건 기억", "icon": "🔑", "desc": "열쇠 어디 뒀지?",
        "url": "http://127.0.0.1:8788", "token_header": "X-ObjMem-Token", "health": "/api/ping",
        "page": "/", "page_storage": {"objmem.token": PLACEHOLDER},
    },
    "voice-journal": {
        "title": "3줄 일기", "icon": "📓", "desc": "한 줄 남기기",
        "url": "http://127.0.0.1:8787", "token_header": "X-VJ-Token", "health": "/health",
        "page": "", "quick": {"label": "한 줄 일기", "path": "/entry", "field": "text"},
    },
    "sound-watch": {
        "title": "소리 감시", "icon": "👂", "desc": "노크 · 초인종 · 층간소음",
        "url": "http://127.0.0.1:8770", "token_header": "X-SoundWatch-Token", "health": "/api/ping",
        "page": "/", "page_storage": {"sw_token": PLACEHOLDER},
    },
    "jarvis": {
        "title": "자비스", "icon": "🤖", "desc": "작업 대시보드",
        "url": "http://127.0.0.1:8800", "token_header": "X-Jarvis-Token", "health": "/api/ping",
        "headers": {"X-Jarvis-Device": "s21"},
        "page": "/", "page_storage": {"jarvis.token": PLACEHOLDER, "jarvis.device": PLACEHOLDER},
    },
}

SERVICE_DEFAULTS = {
    "title": "", "icon": "🔗", "desc": "", "url": "", "token_header": "", "token": "", "token_env": "",
    "token_file": "", "headers": {}, "health": "/", "page": "/", "page_storage": {}, "rewrite": ["/api/"],
    "path_mode": "prefix", "listen_port": 0, "timeout": 40, "max_body_mb": None, "quick": None,
}

DEFAULTS = {
    "server": {"host": "127.0.0.1", "port": 8790, "token": "", "max_body_mb": 8, "max_response_mb": 64,
               "cookie_days": 30, "health_timeout": 3},
    "services": DEFAULT_SERVICES,
    "desk": {
        "cards_path": str(REPO_ROOT / "morning-brief" / "out" / "cards-latest.json"),
        "log_path": "~/.phone-home/desk-log.jsonl",
        # 서비스 이름 -> 결재 어댑터 종류(sidekick | jarvis)
        "approvals": {"sidekick": "sidekick", "jarvis": "jarvis"},
        # 이 문구가 들어간 결재는 버튼 없이 해당 서비스 화면 링크만 건다(대소문자 무시)
        "link_only_patterns": ["rm -rf", "rm -fr", "claude.task"],
        # 실행 허용 action 화이트리스트: {"id": {"label": "...", "argv": ["..."], "timeout": 20}}
        "actions": {},
        "action_timeout": 20,
    },
}


def default_path() -> Path:
    env = os.environ.get("PHONE_HOME_CONFIG")
    return Path(env) if env else Path.home() / ".phone-home" / "config.json"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("services", "actions",
                                                                                "approvals", "headers",
                                                                                "page_storage"):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def normalize(raw: dict | None) -> dict:
    """사용자 설정을 기본값과 합치고 검증한다. 잘못되면 ValueError."""
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError("설정은 JSON 객체여야 합니다")
    cfg = _merge(DEFAULTS, {k: v for k, v in raw.items() if k != "services"})
    services_raw = raw.get("services", DEFAULT_SERVICES)
    if not isinstance(services_raw, dict):
        raise ValueError("services는 객체여야 합니다")
    services = {}
    used_ports = {int(cfg["server"]["port"])}
    for name, svc in services_raw.items():
        if not NAME_RE.match(str(name)):
            raise ValueError("서비스 이름은 영문 소문자/숫자/-/_ 만: %r" % name)
        if not isinstance(svc, dict):
            raise ValueError("services.%s 는 객체여야 합니다" % name)
        base = DEFAULT_SERVICES.get(name, {})
        s = _merge(SERVICE_DEFAULTS, base)
        s.update(copy.deepcopy(svc))
        s["title"] = s["title"] or name
        u = urlparse(s["url"])
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("services.%s.url 은 http(s)://호스트:포트 형식이어야 합니다" % name)
        if s["path_mode"] not in ("prefix", "passthrough"):
            raise ValueError("services.%s.path_mode 는 prefix 또는 passthrough" % name)
        if s["path_mode"] == "passthrough":
            p = int(s.get("listen_port") or 0)
            if not 0 < p < 65536 or p in used_ports:
                raise ValueError("services.%s: passthrough 모드는 겹치지 않는 listen_port 가 필요합니다" % name)
            used_ports.add(p)
        for key in ("health", "page"):
            if s[key] and not str(s[key]).startswith("/"):
                raise ValueError("services.%s.%s 는 /로 시작해야 합니다" % (name, key))
        if not isinstance(s["headers"], dict) or not isinstance(s["page_storage"], dict):
            raise ValueError("services.%s.headers/page_storage 는 객체여야 합니다" % name)
        if not isinstance(s["rewrite"], list):
            raise ValueError("services.%s.rewrite 는 목록이어야 합니다" % name)
        services[name] = s
    cfg["services"] = services
    desk = cfg["desk"]
    if not isinstance(desk["actions"], dict):
        raise ValueError("desk.actions 는 객체여야 합니다")
    for aid, a in desk["actions"].items():
        if not NAME_RE.match(str(aid)) or not isinstance(a, dict):
            raise ValueError("desk.actions.%s 형식 오류" % aid)
        argv = a.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            raise ValueError("desk.actions.%s.argv 는 문자열 목록이어야 합니다 (셸 문자열 불가)" % aid)
    for svc_name, kind in desk["approvals"].items():
        if kind not in ("sidekick", "jarvis"):
            raise ValueError("desk.approvals.%s 는 sidekick 또는 jarvis" % svc_name)
    return cfg


def resolve_secrets(cfg: dict) -> dict:
    """환경변수/파일에서 토큰을 채운 사본. 원본 설정 파일에는 쓰지 않는다."""
    cfg = copy.deepcopy(cfg)
    env_tok = os.environ.get("PHONE_HOME_TOKEN")
    if env_tok:
        cfg["server"]["token"] = env_tok.strip()
    for s in cfg["services"].values():
        if s.get("token_env") and os.environ.get(s["token_env"]):
            s["token"] = os.environ[s["token_env"]].strip()
        elif not s.get("token") and s.get("token_file"):
            try:
                s["token"] = Path(s["token_file"]).expanduser().read_text(encoding="utf-8").strip()
            except OSError:
                s["token"] = ""
    return cfg


def load(path: Path | str | None = None) -> dict:
    p = Path(path) if path else default_path()
    raw = {}
    if p.exists():
        with open(p, encoding="utf-8") as f:
            try:
                raw = json.load(f)
            except ValueError as e:
                raise ValueError("설정 파일 JSON 오류: %s" % e)
    return resolve_secrets(normalize(raw))


def example() -> dict:
    """새 설정 파일에 쓸 내용(토큰은 새로 만든다)."""
    cfg = copy.deepcopy(DEFAULTS)
    cfg["server"]["token"] = generate_token()
    for s in cfg["services"].values():
        s.setdefault("token", "")
    return cfg


def write(path: Path, cfg: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)
    return path
