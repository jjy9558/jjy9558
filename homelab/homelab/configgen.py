"""각 프로젝트의 설정 파일을 만든다 (문서화된 위치·형식). 이미 있는 파일은 --force 없이 건드리지 않는다.

필요한 키만 적고 나머지는 각 프로젝트의 기본값에 맡긴다. 토큰은 secrets.json 의 값을 쓴다.
"""
from __future__ import annotations

import difflib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import secretstore
from .registry import Ctx

PHONE_BACKENDS = {  # phone-home 서비스 이름 -> (홈랩 서비스, 토큰 이름)
    "sidekick": ("sidekick", "sidekick"),
    "object-memory": ("object-memory", "object-memory"),
    "voice-journal": ("voice-journal", "voice-journal"),
    "sound-watch": ("sound-watch", "sound-watch"),
    "jarvis": ("jarvis-hub", "jarvis-s21"),
}


def token_file_name(ph_name: str) -> str:
    """phone-home 뒤쪽 서비스 -> tokens/ 안 파일 이름 (jarvis 는 s21 기기 토큰)."""
    return "jarvis-s21" if ph_name == "jarvis" else PHONE_BACKENDS[ph_name][0]


@dataclass
class ConfigFile:
    key: str
    path: Path
    content: str
    desc: str = ""


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------------------------------
# 필요한 토큰 이름
# ---------------------------------------------------------------------------------------
def required_tokens(ctx: Ctx) -> tuple[list[str], list[str]]:
    toks = ["phone-home"]
    en = ctx.enabled_services()
    for name in ("sidekick", "object-memory", "voice-journal", "sound-watch"):
        toks.append(name)  # 다른 서비스(워커, 일부 작업)가 sidekick 토큰을 쓰므로 항상 만든다
    toks += ["jarvis-main", "jarvis-s21"]
    if ctx.settings.get("minipc") or "jarvis-worker-minipc" in en:
        toks.append("jarvis-minipc")
    extras = ["sidekick-emergency-code"]
    if ctx.settings.get("ntfy"):
        extras.append("ntfy-topic")
    seen = []
    for t in toks:
        if t not in seen:
            seen.append(t)
    return seen, extras


# ---------------------------------------------------------------------------------------
# 서비스 설정
# ---------------------------------------------------------------------------------------
def sidekick_config(ctx: Ctx) -> dict:
    cfg: dict[str, Any] = {
        "server": {"host": "127.0.0.1", "port": ctx.port("sidekick"), "token": ctx.token("sidekick")},
        "alarm": {"emergency_code": ctx.extra("sidekick-emergency-code")},
    }
    if ctx.llm_url:
        cfg["llm"] = {"base_url": ctx.llm_url, "model": "local"}
    if ctx.settings.get("morning_gate"):
        cfg["alarm"]["morning_gate"] = {
            "enabled": True,
            "brief_path": str(ctx.layout.data("morning-brief") / "out" / "brief-latest.json"),
        }
    return cfg


def phone_home_config(ctx: Ctx) -> dict:
    services: dict[str, Any] = {}
    for ph_name, (svc, tok) in PHONE_BACKENDS.items():
        if not ctx.is_enabled(svc):
            continue
        services[ph_name] = {"url": ctx.url(svc), "token_file": str(ctx.layout.token_file(token_file_name(ph_name)))}
    cfg: dict[str, Any] = {
        "server": {"host": "127.0.0.1", "port": ctx.port("phone-home"), "token": ctx.token("phone-home")},
        "services": services,
        "desk": {"cards_path": str(ctx.layout.data("morning-brief") / "out" / "cards-latest.json")},
    }
    return cfg


def object_memory_config(ctx: Ctx) -> dict:
    cfg: dict[str, Any] = {"token": ctx.token("object-memory"), "host": "127.0.0.1",
                           "port": ctx.port("object-memory"),
                           "data_dir": str(ctx.layout.objmem_config.parent / "data")}
    if ctx.llm_url:
        cfg["base_url"] = ctx.llm_url
    return cfg


def sound_watch_config(ctx: Ctx) -> dict:
    notify: dict[str, Any] = {}
    if ctx.is_enabled("sidekick"):
        notify["sidekick"] = {"enabled": True, "url": ctx.url("sidekick"), "token_env": "SIDEKICK_TOKEN",
                              "module": "sound-watch"}
    if ctx.ntfy_on:
        notify["ntfy"] = {"enabled": True, "server": ctx.settings.get("ntfy_server") or "https://ntfy.sh",
                          "topic": ctx.ntfy_topic}
    cfg: dict[str, Any] = {"server": {"host": "127.0.0.1", "port": ctx.port("sound-watch"),
                                      "token": ctx.token("sound-watch")}}
    if notify:
        cfg["notify"] = notify
    return cfg


def jarvis_hub_config(ctx: Ctx) -> dict:
    devices: dict[str, Any] = {
        "main": {"token": ctx.token("jarvis-main"), "label": "메인PC", "worker": True,
                 "caps": ["gpu", "llm", "stt", "windows", "claude"], "submit": ["*"], "trusted": True},
        "s21": {"token": ctx.token("jarvis-s21"), "label": "S21", "worker": False,
                "submit": ["llm.chat", "claude.task", "shell.allowed", "notify.say"], "approve": True},
    }
    if ctx.settings.get("minipc") or ctx.is_enabled("jarvis-worker-minipc"):
        devices["minipc"] = {"token": ctx.token("jarvis-minipc"), "label": "미니PC", "worker": True,
                             "caps": ["lan", "speaker"], "submit": ["*"], "trusted": True}
    notify: dict[str, Any] = {"say_events": True, "say_job": True}
    if ctx.ntfy_on:
        notify["ntfy"] = {"enabled": True, "server": ctx.settings.get("ntfy_server") or "https://ntfy.sh",
                          "topic": ctx.ntfy_topic}
    cfg: dict[str, Any] = {
        "server": {"host": ctx.hub_host, "port": ctx.port("jarvis-hub")},
        "db": str(ctx.layout.data("jarvis") / "jarvis.db"),
        "devices": devices,
        "notify": notify,
    }
    mac = ctx.settings.get("wol_mac")
    if mac:
        cfg["wake"] = {"enabled": True, "label": "메인PC", "mac": mac,
                       "broadcast": ctx.settings.get("wol_broadcast") or "255.255.255.255",
                       "port": 9, "for_caps": ["gpu"], "cooldown_seconds": 300}
    return cfg


def jarvis_worker_config(ctx: Ctx) -> dict:
    """이 PC(5080)의 워커. 토큰은 파일에 쓰지 않고 JARVIS_TOKEN 환경변수로 받는다(up/run 이 넣어 줌)."""
    handlers: dict[str, Any] = {}
    if ctx.llm_url:
        handlers["llm.chat"] = {"base_url": ctx.llm_url, "model": "local"}
    if ctx.is_enabled("sidekick"):
        # jarvis notify.say 핸들러는 토큰을 환경변수로 읽지 못해서 설정에 직접 넣는다 (파일 권한 600).
        handlers["notify.say"] = {"enabled": True, "sidekick_url": ctx.url("sidekick"),
                                  "token": ctx.token("sidekick"), "path": "/api/say"}
    return {"hub": ctx.hub_url, "device": "main", "caps": ["gpu", "llm", "windows"], "handlers": handlers}


def jarvis_worker_minipc_config(ctx: Ctx) -> dict:
    handlers: dict[str, Any] = {
        "wake": {"enabled": bool(ctx.settings.get("wol_mac")), "repeat": 3},
        "notify.say": {"enabled": True,
                       "sidekick_url": ctx.settings.get("sidekick_url_for_minipc") or ctx.url("sidekick"),
                       "token": ctx.token("sidekick"), "path": "/api/say"},
    }
    return {"hub": ctx.hub_url, "device": "minipc", "caps": ["lan", "speaker"], "handlers": handlers}


def gm_trpg_note(ctx: Ctx) -> None:  # gm-trpg 는 설정 파일이 없다 (세계 DB 만 필요)
    return None


# ---------------------------------------------------------------------------------------
# 작업 설정
# ---------------------------------------------------------------------------------------
def briefing_config(ctx: Ctx) -> dict:
    base = ctx.layout.data("community-briefing")
    cfg: dict[str, Any] = {
        "sources": [
            {"name": "GeekNews", "type": "rss", "url": "https://news.hada.io/rss"},
            {"name": "Hacker News", "type": "rss", "url": "https://hnrss.org/frontpage"},
            {"name": "r/LocalLLaMA", "type": "rss", "url": "https://www.reddit.com/r/LocalLLaMA/.rss", "delay": 5},
        ],
        "interests": {
            "topics": {
                "로컬 LLM": {"llama.cpp": 4, "llm": 3, "로컬 llm": 3, "gguf": 3, "양자화": 2, "ollama": 2},
                "GPU/하드웨어": {"gpu": 2, "rtx": 2, "vram": 2, "그래픽카드": 2, "cuda": 1.5},
                "개발 도구": {"python": 1.5, "파이썬": 1.5, "rust": 1, "오픈소스": 1},
            },
            "mute": ["코인", "crypto", "nft", "광고", "sponsored"],
        },
        "fetch": {"user_agent": "community-briefing/0.1 (personal RSS reader; homelab)",
                  "cache_dir": str(base / "cache")},
        "llm": {"enabled": bool(ctx.llm_url), "base_url": ctx.llm_url or "http://127.0.0.1:8080/v1",
                "model": "local"},
        "output": {"dir": str(base / "out"), "db_path": str(base / "seen.sqlite3"), "since": "24h"},
        "timeline": {"enabled": False, "dir": str(base / "timeline")},
    }
    if ctx.ntfy_on:
        cfg["delivery"] = {"ntfy": {"enabled": True, "server": ctx.settings.get("ntfy_server") or "https://ntfy.sh",
                                    "topic": ctx.ntfy_topic}}
    return cfg


def gamecal_config(ctx: Ctx) -> dict:
    base = ctx.layout.data("game-calendar")
    cfg: dict[str, Any] = {
        "appids": list(ctx.settings.get("appids") or []),
        "llm": {"enabled": bool(ctx.llm_url), "base_url": ctx.llm_url or "http://127.0.0.1:8080/v1",
                "model": "local"},
        "cache_dir": str(base / "cache"), "out_dir": str(base / "out"), "state_file": str(base / "state.json"),
    }
    if ctx.ntfy_on:
        cfg["ntfy"] = {"server": ctx.settings.get("ntfy_server") or "https://ntfy.sh", "topic": ctx.ntfy_topic}
    return cfg


def gamecal_state_path(ctx: Ctx) -> Path:
    return ctx.layout.data("game-calendar") / "state.json"


def mbrief_config(ctx: Ctx) -> dict:
    sources: dict[str, Any] = {
        "game_calendar": {"enabled": True, "state": str(gamecal_state_path(ctx))},
        "sound_watch": {"enabled": True, "data_dir": str(ctx.layout.data("sound-watch"))},
        "community_briefing": {"enabled": True, "dir": str(ctx.layout.data("community-briefing") / "out"), "top": 3},
        "jarvis": {"enabled": ctx.is_enabled("jarvis-hub"), "url": ctx.url("jarvis-hub"), "device": "s21",
                   "token_env": "JARVIS_TOKEN", "watch_devices": ["main"]},
    }
    notify: dict[str, Any] = {"sidekick": {"url": ctx.url("sidekick"), "token_env": "SIDEKICK_TOKEN",
                                           "module": "morning-brief"}}
    if ctx.ntfy_on:
        notify["ntfy"] = {"server": ctx.settings.get("ntfy_server") or "https://ntfy.sh", "topic": ctx.ntfy_topic}
    return {"out_dir": str(ctx.layout.data("morning-brief") / "out"), "sources": sources, "notify": notify}


def nagger_config(ctx: Ctx) -> dict:
    outputs: dict[str, Any] = {}
    if ctx.is_enabled("sidekick"):
        outputs["sidekick"] = {"enabled": True, "base_url": ctx.url("sidekick")}  # 토큰: SIDEKICK_TOKEN 환경변수
    if ctx.ntfy_on:
        outputs["ntfy"] = {"enabled": True, "url": ctx.settings.get("ntfy_server") or "https://ntfy.sh",
                           "topic": ctx.ntfy_topic}
    cfg: dict[str, Any] = {"mombot": {"allow_sleep": False}}  # 자동 재우기는 직접 켤 때까지 꺼 둔다
    if outputs:
        cfg["outputs"] = outputs
    if ctx.llm_url:
        cfg["llm"] = {"base_url": ctx.llm_url, "model": "local"}
    return cfg


def til_config(ctx: Ctx) -> dict:
    return {"redact_words": list(ctx.settings.get("redact_words") or [])}


def devguard_config(ctx: Ctx) -> dict:
    wallet: dict[str, Any] = {"daily_limit_usd": ctx.settings.get("wallet_limit_usd") or 0, "warn_ratio": 0.8,
                              "prices_confirmed": False}
    if ctx.ntfy_on:
        wallet["ntfy_url"] = "%s/%s" % ((ctx.settings.get("ntfy_server") or "https://ntfy.sh").rstrip("/"),
                                        ctx.ntfy_topic)
    return {"wallet": wallet}


def render_all(ctx: Ctx) -> list[ConfigFile]:
    L = ctx.layout
    files: list[ConfigFile] = []

    def add(key: str, path: Path, content: str, desc: str) -> None:
        files.append(ConfigFile(key, path, content, desc))

    en = ctx.is_enabled
    if en("sidekick"):
        add("sidekick", ctx.cfg("sidekick"), dumps(sidekick_config(ctx)), "s21-sidekick 서버 설정")
    if en("jarvis-hub"):
        add("jarvis-hub", ctx.cfg("jarvis-hub"), dumps(jarvis_hub_config(ctx)), "jarvis 허브 설정 (기기 토큰)")
    if en("jarvis-worker"):
        add("jarvis-worker", ctx.cfg("jarvis-worker"), dumps(jarvis_worker_config(ctx)), "jarvis 워커(이 PC)")
    if ctx.settings.get("minipc") or en("jarvis-worker-minipc"):
        add("jarvis-worker-minipc", ctx.cfg("jarvis-worker-minipc"), dumps(jarvis_worker_minipc_config(ctx)),
            "jarvis 워커(미니PC로 복사해 쓰는 파일)")
    if en("voice-journal"):
        add("voice-journal", ctx.cfg("voice-journal"), ctx.token("voice-journal") + "\n", "voice-journal 토큰 파일")
    if en("object-memory"):
        add("object-memory", ctx.cfg("object-memory"), dumps(object_memory_config(ctx)), "object-memory 설정")
    if en("sound-watch"):
        add("sound-watch", ctx.cfg("sound-watch"), dumps(sound_watch_config(ctx)), "sound-watch 설정")
    if en("phone-home"):
        add("phone-home", ctx.cfg("phone-home"), dumps(phone_home_config(ctx)), "phone-home 설정")
        for ph_name, (svc, tok) in PHONE_BACKENDS.items():
            if en(svc):
                tname = token_file_name(ph_name)
                add("token:" + tname, L.token_file(tname), ctx.token(tok) + "\n",
                    "phone-home 이 읽는 %s 토큰" % ph_name)
    # 작업용 설정 (서비스를 켜든 말든 만든다 — 작업 스케줄은 서비스와 별개)
    add("briefing", ctx.cfg("briefing"), dumps(briefing_config(ctx)), "community-briefing 설정")
    add("gamecal", ctx.cfg("gamecal"), dumps(gamecal_config(ctx)), "game-calendar 설정")
    add("mbrief", ctx.cfg("mbrief"), dumps(mbrief_config(ctx)), "morning-brief 설정")
    add("nagger", ctx.cfg("nagger"), dumps(nagger_config(ctx)), "life-nagger 설정")
    add("til-drafter", ctx.cfg("til-drafter"), dumps(til_config(ctx)), "til-drafter 설정")
    add("dev-guard", ctx.cfg("dev-guard"), dumps(devguard_config(ctx)), "dev-guard 지갑 설정")
    return files


def data_dirs(ctx: Ctx) -> list[Path]:
    """서비스가 스스로 만들어 주지 않는 폴더들 (예: 자비스 DB 가 들어갈 폴더)."""
    L = ctx.layout
    dirs = [L.data("jarvis"), L.data("morning-brief") / "out", L.data("community-briefing"),
            L.data("game-calendar"), L.data("sound-watch"), L.logs_dir, L.run_dir, L.tokens_dir]
    if ctx.is_enabled("gm-trpg"):
        dirs.append(L.data("gm-trpg") / "worlds")
    if ctx.is_enabled("shorts-vote"):
        dirs.append(L.data("shorts-feedback"))
    return dirs


def ensure_data_dirs(ctx: Ctx) -> None:
    for d in data_dirs(ctx):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------------------
# 쓰기 (덮어쓰기 금지 / dry-run / diff)
# ---------------------------------------------------------------------------------------
@dataclass
class Result:
    key: str
    path: Path
    status: str      # created | exists | same | overwritten | would-create | would-overwrite | would-skip
    diff: str = ""
    backup: Path | None = None


def make_diff(old: str, new: str, label: str, secrets: dict) -> str:
    lines = difflib.unified_diff(old.splitlines(), new.splitlines(), "기존 " + label, "새로 " + label,
                                 lineterm="")
    return secretstore.mask("\n".join(lines), secrets)


def apply(files: list[ConfigFile], secrets: dict, *, force: bool = False, dry_run: bool = False,
          clock: Callable[[], float] = time.time) -> list[Result]:
    results: list[Result] = []
    for f in files:
        exists = f.path.exists()
        if exists:
            try:
                old = f.path.read_text(encoding="utf-8")
            except OSError:
                old = ""
            if old == f.content:
                results.append(Result(f.key, f.path, "same"))
                continue
            diff = make_diff(old, f.content, f.path.name, secrets)
            if not force:
                results.append(Result(f.key, f.path, "would-skip" if dry_run else "exists", diff))
                continue
            if dry_run:
                results.append(Result(f.key, f.path, "would-overwrite", diff))
                continue
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(clock()))
            backup = f.path.with_name("%s.bak-%s" % (f.path.name, stamp))
            n = 0
            while backup.exists():
                n += 1
                backup = f.path.with_name("%s.bak-%s-%d" % (f.path.name, stamp, n))
            shutil.copy2(f.path, backup)
            secretstore.write_private(f.path, f.content)
            results.append(Result(f.key, f.path, "overwritten", diff, backup))
            continue
        if dry_run:
            results.append(Result(f.key, f.path, "would-create", make_diff("", f.content, f.path.name, secrets)))
            continue
        secretstore.write_private(f.path, f.content)
        results.append(Result(f.key, f.path, "created"))
    return results
