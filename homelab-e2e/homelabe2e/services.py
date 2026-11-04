"""형제 프로젝트 서버를 각자의 CLI(`python -m <pkg> serve ...`)로 띄우는 도우미.

설정 파일은 각 프로젝트 README/코드가 문서화한 키만 써서 임시 폴더에 만든다. 토큰은 실행 시점에 생성.
"""
from __future__ import annotations

import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .harness import Service, Stack, http_json, token, wait_until


@dataclass
class Running:
    svc: Service
    token: str
    config: Path | None = None
    extra: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        return self.svc.url


# ---------------------------------------------------------------- s21-sidekick
def sidekick(stack: Stack, extra: dict | None = None, name: str = "sidekick") -> Running:
    tok = token("sk")
    cfg = {
        "server": {"host": "127.0.0.1", "port": 0, "token": tok},
        "llm": {"base_url": ""}, "vision": {"base_url": ""},
        "alarm": {"enabled": False, "emergency_code": str(secrets.randbelow(900000) + 100000)},
        "approval": {"timeout_seconds": 25},
        "departure": {"providers": [{"name": "reminders", "items": ["휴대폰 충전기"]}]},
        "say": {"enabled": True, "max_per_minute": 60, "min_interval_seconds": 0, "max_chars": 300},
    }
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k] = dict(cfg[k], **v)
        else:
            cfg[k] = v
    path = stack.path("%s/config.json" % name)

    def args(port):
        cfg["server"]["port"] = port            # 훅(client)도 이 파일의 server.port 로 붙는다
        stack.write_json(str(path.relative_to(stack.tmp)), cfg)
        return ["serve", "--config", str(path), "--port", str(port)]

    svc = stack.serve("s21-sidekick", args, ready_path="/api/ping", name=name)
    return Running(svc, tok, path, {"cfg": cfg})


def sidekick_events(sk: Running, since: int = 0) -> list[dict]:
    st, data = http_json("GET", sk.url + "/api/events?since=%d&wait=0" % since,
                         headers={"X-Sidekick-Token": sk.token})
    assert st == 200, (st, data)
    return data["events"]


def wait_sidekick_say(sk: Running, pred, timeout: float = 20, what: str = "sidekick say 이벤트") -> dict:
    def find():
        for ev in sidekick_events(sk):
            if ev.get("kind") == "say" and pred(ev):
                return ev
        return None
    return wait_until(find, timeout, 0.2, what)


# ---------------------------------------------------------------- voice-journal
def voice_journal(stack: Stack, db: Path, llm_url: str | None = None) -> Running:
    tok = token("vj")
    env = {"VJ_LLM_BASE_URL": llm_url} if llm_url else {}
    svc = stack.serve("voice-journal", lambda p: ["--db", str(db), "serve", "--port", str(p), "--token", tok],
                      ready_path="/health", env=env)
    return Running(svc, tok)


# ---------------------------------------------------------------- phone-home
def phone_home(stack: Stack, services: dict, cards_path: Path, desk: dict | None = None) -> Running:
    tok = token("ph")
    d = {"cards_path": str(cards_path), "log_path": str(stack.path("phone-home/desk-log.jsonl"))}
    d.update(desk or {})
    raw = {"server": {"host": "127.0.0.1", "port": 0, "token": tok, "health_timeout": 3},
           "services": services, "desk": d}
    path = stack.write_json("phone-home/config.json", raw)
    svc = stack.serve("phone-home", lambda p: ["--config", str(path), "serve", "--port", str(p)],
                      ready_path="/favicon.ico")
    return Running(svc, tok, path)


# ---------------------------------------------------------------- object-memory
def object_memory(stack: Stack, llm_url: str) -> Running:
    tok = token("om")
    cfg = {"token": tok, "data_dir": str(stack.path("objmem/data")), "base_url": llm_url, "model": "fake",
           "api_key": "none", "vlm_timeout": 10, "host": "127.0.0.1", "privacy_hours": [], "llm_phrase": False}
    path = stack.write_json("objmem/config.json", cfg)
    svc = stack.serve("object-memory", lambda p: ["--config", str(path), "serve", "--port", str(p)],
                      ready_path="/api/ping")
    return Running(svc, tok, path)


# ---------------------------------------------------------------- jarvis-hub
def jarvis_hub(stack: Stack, extra_devices: dict | None = None, extra: dict | None = None) -> Running:
    toks = {"minipc": token("jm"), "cli": token("jc"), "s21": token("js")}
    devices = {
        "minipc": {"token": toks["minipc"], "label": "미니PC", "worker": True, "caps": ["lan", "speaker"],
                   "submit": ["*"], "trusted": True},
        "cli": {"token": toks["cli"], "label": "CLI", "worker": False, "submit": ["*"], "trusted": True,
                "approve": True},
        "s21": {"token": toks["s21"], "label": "S21", "worker": False, "approve": True,
                "submit": ["llm.chat", "claude.task", "shell.allowed", "notify.say"]},
    }
    devices.update(extra_devices or {})
    cfg = {"server": {"host": "127.0.0.1", "port": 0}, "db": str(stack.path("jarvis/jarvis.db")),
           "devices": devices, "tick_seconds": 1,
           "notify": {"say_events": True, "say_job": True, "ntfy": {"enabled": False}}}
    cfg.update(extra or {})
    path = stack.write_json("jarvis/hub.json", cfg)
    svc = stack.serve("jarvis-hub", lambda p: ["hub", "--config", str(path), "--port", str(p)],
                      ready_path="/api/ping")
    return Running(svc, toks["cli"], path, {"tokens": toks})


def jarvis_headers(hub: Running, device: str = "cli") -> dict:
    return {"X-Jarvis-Device": device, "X-Jarvis-Token": hub.extra["tokens"][device]}


def jarvis_worker(stack: Stack, hub: Running, handlers: dict, caps=("lan", "speaker")) -> Service:
    cfg = {"hub": hub.url, "device": "minipc", "token": hub.extra["tokens"]["minipc"], "caps": list(caps),
           "lease_wait_seconds": 2, "heartbeat_seconds": 2, "retry_backoff_seconds": 1, "handlers": handlers}
    path = stack.write_json("jarvis/worker.json", cfg)
    # 워커는 HTTP 를 열지 않으므로 직접 띄우고, 허브의 기기 목록에서 온라인이 될 때까지 기다린다
    import subprocess
    from .harness import PROJECTS, project_dir
    log_path = stack.logs / "jarvis-worker.log"
    fh = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", PROJECTS["jarvis-hub"], "worker", "--config", str(path)],
                            cwd=str(project_dir("jarvis-hub")), env=stack.env(), stdin=subprocess.DEVNULL,
                            stdout=fh, stderr=subprocess.STDOUT)
    svc = Service("jarvis-worker", proc, log_path, 0, fh)
    stack.services.append(svc)

    def online():
        if proc.poll() is not None:
            raise AssertionError("워커가 죽음:\n" + svc.log())
        st, data = http_json("GET", hub.url + "/api/devices", headers=jarvis_headers(hub))
        devs = data.get("devices") if isinstance(data, dict) else None
        if isinstance(devs, dict):
            devs = [dict(v, id=k) for k, v in devs.items()]
        return any(d.get("id") == "minipc" and d.get("online") for d in devs or [])
    try:
        wait_until(online, 20, 0.2, "jarvis 워커 온라인")
    except AssertionError as e:
        raise AssertionError("%s\n워커 로그:\n%s" % (e, svc.log()))
    return svc
