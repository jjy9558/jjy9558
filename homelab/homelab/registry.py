"""서비스(서버)와 작업(주기 실행)의 목록. 각 프로젝트의 README/config/cli 를 읽고 맞춘 값이다.

모든 실행은 인자 목록(shell=False)이고, 토큰은 명령줄이 아니라 환경변수/설정 파일/토큰 파일로만 넘긴다.
다른 프로젝트 폴더는 읽기만 한다(실행 시 cwd 로만 씀).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .paths import Layout, default_repo_root

LOOPBACK = "127.0.0.1"


@dataclass
class Spec:
    argv: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)  # os.environ 위에 얹을 값


class Ctx:
    """설정 + 토큰 + 배치를 묶은 읽기 전용 문맥."""

    def __init__(self, layout: Layout, settings: dict, secrets: dict):
        self.layout = layout
        self.settings = settings
        self.secrets = secrets

    # --- 기본 값 ---------------------------------------------------------------------
    @property
    def python(self) -> str:
        return self.settings.get("python") or sys.executable

    @property
    def repo_root(self) -> Path:
        r = self.settings.get("repo_root")
        return Path(r).expanduser() if r else default_repo_root()

    def project(self, folder: str) -> Path:
        return self.repo_root / folder

    def port(self, name: str) -> int:
        p = (self.settings.get("ports") or {}).get(name)
        if p:
            return int(p)
        svc = SERVICES.get(name)
        return svc.port if svc else 0

    def token(self, key: str) -> str:
        return self.secrets["tokens"].get(key, "")

    def extra(self, key: str) -> str:
        return self.secrets["extras"].get(key, "")

    @property
    def llm_url(self) -> str:
        return self.settings.get("llm_url") or ""

    def enabled_services(self) -> list[str]:
        names = self.settings.get("enabled") or default_enabled()
        return [n for n in SERVICES if n in names]

    def is_enabled(self, name: str) -> bool:
        return name in self.enabled_services()

    @property
    def ntfy_on(self) -> bool:
        return bool(self.settings.get("ntfy"))

    @property
    def ntfy_topic(self) -> str:
        return self.extra("ntfy-topic") if self.ntfy_on else ""

    def url(self, name: str, host: str = LOOPBACK) -> str:
        return "http://%s:%d" % (host, self.port(name))

    @property
    def hub_host(self) -> str:
        return self.settings.get("hub_host") or LOOPBACK

    @property
    def hub_url(self) -> str:
        u = self.settings.get("hub_url")
        if u:
            return u.rstrip("/")
        host = self.hub_host
        if host in ("0.0.0.0", "::", ""):
            host = LOOPBACK
        return "http://%s:%d" % (host, self.port("jarvis-hub"))

    # --- 설정 파일 위치 (이 규칙이 한 곳에 있어야 doctor 와 생성기가 어긋나지 않는다) ----------
    def cfg(self, key: str) -> Path:
        L = self.layout
        table = {
            "sidekick": L.config("sidekick"),
            "jarvis-hub": L.config("jarvis-hub"),
            "jarvis-worker": L.config("jarvis-worker-main"),
            "jarvis-worker-minipc": L.config("jarvis-worker-minipc"),
            "mbrief": L.config("mbrief"),
            "gamecal": L.config("gamecal"),
            "briefing": L.config("community-briefing"),
            "nagger": L.config("life-nagger"),
            "phone-home": L.phone_home_config,
            "object-memory": L.objmem_config,
            "voice-journal": L.vjournal_token,
            "sound-watch": L.data("sound-watch") / "config.json",
            "til-drafter": L.til_config,
            "dev-guard": L.devguard_config,
        }
        return table[key]

    def env_base(self) -> dict[str, str]:
        return {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


# ---------------------------------------------------------------------------------------
# 서비스
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Service:
    name: str
    title: str
    project: str                       # 저장소 안 폴더 이름
    port: int                          # 0 이면 포트 없음(워커)
    health: str                        # 건강 확인 경로 ("" 이면 HTTP 확인 없음)
    build: Callable[[Ctx], Spec]
    token_key: str = ""                # 건강 확인에 붙일 토큰 이름 (secrets.json)
    token_header: str = ""
    headers: tuple = ()                # 추가 헤더 (이름, 값)
    any_http_ok: bool = False          # 인증 없는 경로가 없어 아무 HTTP 응답이나 '살아 있음'
    default_on: bool = True
    config_keys: tuple = ()            # 이 서비스가 쓰는 설정 키 (ctx.cfg)
    needs: tuple = ()                  # 먼저 켜져 있어야 하는 서비스 (순서 힌트)
    note: str = ""


def _spec(ctx: Ctx, project: str, args: list[str], env: dict[str, str] | None = None) -> Spec:
    e = ctx.env_base()
    e.update(env or {})
    return Spec([ctx.python] + args, ctx.project(project), e)


def _sidekick(ctx: Ctx) -> Spec:
    return _spec(ctx, "s21-sidekick", ["-m", "sidekick", "serve", "--config", str(ctx.cfg("sidekick"))])


def _voice_journal(ctx: Ctx) -> Spec:
    db = ctx.layout.vjournal_token.parent / "journal.db"   # 토큰 파일(같은 폴더의 token)과 짝
    return _spec(ctx, "voice-journal", ["-m", "vjournal", "--db", str(db), "serve", "--host", LOOPBACK,
                                        "--port", str(ctx.port("voice-journal"))],
                 {"VJ_TOKEN": ctx.token("voice-journal")})


def _object_memory(ctx: Ctx) -> Spec:
    return _spec(ctx, "object-memory", ["-m", "objmem", "--config", str(ctx.cfg("object-memory")), "serve"])


def _sound_watch(ctx: Ctx) -> Spec:
    env = {"SIDEKICK_TOKEN": ctx.token("sidekick")}
    return _spec(ctx, "sound-watch", ["-m", "soundwatch", "serve", "--data-dir",
                                      str(ctx.layout.data("sound-watch"))], env)


def _jarvis_hub(ctx: Ctx) -> Spec:
    return _spec(ctx, "jarvis-hub", ["-m", "jarvis", "hub", "--config", str(ctx.cfg("jarvis-hub"))])


def _jarvis_worker(ctx: Ctx) -> Spec:
    return _spec(ctx, "jarvis-hub", ["-m", "jarvis", "worker", "--config", str(ctx.cfg("jarvis-worker"))],
                 {"JARVIS_TOKEN": ctx.token("jarvis-main")})


def _jarvis_worker_minipc(ctx: Ctx) -> Spec:
    return _spec(ctx, "jarvis-hub", ["-m", "jarvis", "worker", "--config", str(ctx.cfg("jarvis-worker-minipc"))],
                 {"JARVIS_TOKEN": ctx.token("jarvis-minipc")})


def _gm_trpg(ctx: Ctx) -> Spec:
    args = ["-m", "gmtrpg", "serve", "--world", ctx.settings.get("gm_world") or "default",
            "--dir", str(ctx.layout.data("gm-trpg") / "worlds"), "--host", LOOPBACK,
            "--port", str(ctx.port("gm-trpg"))]
    if ctx.llm_url:
        args += ["--base-url", ctx.llm_url]
    return _spec(ctx, "gm-trpg", args)


def _shorts_vote(ctx: Ctx) -> Spec:
    return _spec(ctx, "shorts-feedback", ["-m", "shortsfb", "--data", str(ctx.layout.data("shorts-feedback")),
                                          "vote", "serve", "--host", LOOPBACK,
                                          "--port", str(ctx.port("shorts-vote"))])


def _phone_home(ctx: Ctx) -> Spec:
    return _spec(ctx, "phone-home", ["-m", "phonehome", "--config", str(ctx.cfg("phone-home")), "serve"])


# 시작 순서 = 이 순서. 끌 때는 거꾸로.
SERVICES: dict[str, Service] = {s.name: s for s in [
    Service("sidekick", "S21 사이드킥", "s21-sidekick", 8765, "/api/ping", _sidekick,
            token_key="sidekick", token_header="X-Sidekick-Token", config_keys=("sidekick",)),
    Service("voice-journal", "음성 일기", "voice-journal", 8787, "/health", _voice_journal,
            token_key="voice-journal", token_header="X-VJ-Token", config_keys=("voice-journal",)),
    Service("object-memory", "물건 기억", "object-memory", 8788, "/api/ping", _object_memory,
            token_key="object-memory", token_header="X-ObjMem-Token", config_keys=("object-memory",)),
    Service("sound-watch", "소리 감시", "sound-watch", 8770, "/api/ping", _sound_watch,
            token_key="sound-watch", token_header="X-SoundWatch-Token", config_keys=("sound-watch",)),
    Service("jarvis-hub", "자비스 허브", "jarvis-hub", 8800, "/api/ping", _jarvis_hub,
            config_keys=("jarvis-hub",)),
    Service("jarvis-worker", "자비스 워커(이 PC)", "jarvis-hub", 0, "", _jarvis_worker,
            config_keys=("jarvis-worker",), needs=("jarvis-hub",)),
    Service("jarvis-worker-minipc", "자비스 워커(미니PC 역할)", "jarvis-hub", 0, "", _jarvis_worker_minipc,
            default_on=False, config_keys=("jarvis-worker-minipc",), needs=("jarvis-hub",),
            note="보통은 미니PC에서 직접 실행합니다. 한 PC에서 시험할 때만 켜세요."),
    Service("gm-trpg", "GM TRPG", "gm-trpg", 8766, "/", _gm_trpg, any_http_ok=True, default_on=False,
            note="세계(world)를 먼저 만들어야 합니다: init --gm-world 이름"),
    Service("shorts-vote", "썸네일 투표", "shorts-feedback", 8791, "/", _shorts_vote, any_http_ok=True,
            default_on=False, note="투표(vote create)를 먼저 만들어야 합니다."),
    Service("phone-home", "폰 홈", "phone-home", 8790, "/api/health", _phone_home,
            token_key="phone-home", token_header="X-Phone-Token", config_keys=("phone-home",)),
]}


def default_enabled() -> list[str]:
    return [n for n, s in SERVICES.items() if s.default_on]


# ---------------------------------------------------------------------------------------
# 작업 (주기 실행)
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Job:
    name: str
    title: str
    project: str
    build: Callable[[Ctx, list[str]], Spec]   # (ctx, 추가 인자) -> 실행 명세
    when: str = "daily"                       # daily | logon | weekly
    time: str = "07:00"
    weekday: str = ""                         # weekly: SUN ...
    config_keys: tuple = ()
    long_running: bool = False


def _mbrief(ctx: Ctx, extra: list[str]) -> Spec:
    args = ["-m", "mbrief", "run", "--config", str(ctx.cfg("mbrief")), "--speak"]
    if ctx.ntfy_on:
        args.append("--push")
    # ntfy 토픽은 mbrief.json 에 들어 있다. 토큰은 환경변수로만 넘긴다.
    env = {"SIDEKICK_TOKEN": ctx.token("sidekick"), "JARVIS_TOKEN": ctx.token("jarvis-s21")}
    return _spec(ctx, "morning-brief", args + extra, env)


def _til(ctx: Ctx, extra: list[str]) -> Spec:
    return _spec(ctx, "til-drafter", ["-m", "til_drafter", "--config", str(ctx.cfg("til-drafter"))] + extra)


def _gamecal(ctx: Ctx, extra: list[str]) -> Spec:
    return _spec(ctx, "game-calendar", ["-m", "gamecal", "run", "--config", str(ctx.cfg("gamecal"))] + extra)


def _briefing(ctx: Ctx, extra: list[str]) -> Spec:
    return _spec(ctx, "community-briefing", ["-m", "briefing", "run", "--config", str(ctx.cfg("briefing"))] + extra)


def _nagger(ctx: Ctx, extra: list[str]) -> Spec:
    return _spec(ctx, "life-nagger", ["-m", "nagger", "--config", str(ctx.cfg("nagger")), "run"] + extra,
                 {"SIDEKICK_TOKEN": ctx.token("sidekick")})


def _wallet(ctx: Ctx, extra: list[str]) -> Spec:
    return _spec(ctx, "dev-guard", ["-m", "devguard", "wallet", "alert"] + extra,
                 {"DEVGUARD_HOME": str(ctx.layout.devguard_home)})


def _sw_report(ctx: Ctx, extra: list[str]) -> Spec:
    return _spec(ctx, "sound-watch", ["-m", "soundwatch", "report", "--week", "--data-dir",
                                      str(ctx.layout.data("sound-watch"))] + extra)


def _cooloff(ctx: Ctx, extra: list[str]) -> Spec:
    args = ["-m", "cool_off", "check"]
    if ctx.ntfy_on and ctx.ntfy_topic:
        args += ["--ntfy", ctx.ntfy_topic]
    return _spec(ctx, "cool-off", args + extra)


JOBS: dict[str, Job] = {j.name: j for j in [
    Job("community-briefing", "커뮤니티 브리핑 수집", "community-briefing", _briefing, "daily", "06:30",
        config_keys=("briefing",)),
    Job("morning-brief", "아침 브리핑 (+ 폰이 읽어 줌)", "morning-brief", _mbrief, "daily", "06:50",
        config_keys=("mbrief",)),
    Job("game-calendar", "게임 캘린더 갱신", "game-calendar", _gamecal, "daily", "07:00",
        config_keys=("gamecal",)),
    Job("til-drafter", "TIL 초안 만들기", "til-drafter", _til, "daily", "23:30", config_keys=("til-drafter",)),
    Job("life-nagger", "생활 잔소리 데몬", "life-nagger", _nagger, "logon", "", config_keys=("nagger",),
        long_running=True),
    Job("devguard-wallet", "Claude 사용량 지갑 알림", "dev-guard", _wallet, "daily", "21:00",
        config_keys=("dev-guard",)),
    Job("soundwatch-report", "층간소음 주간 리포트", "sound-watch", _sw_report, "weekly", "09:00", weekday="SUN",
        config_keys=("sound-watch",)),
    Job("cooloff-check", "쿨링오프 확인", "cool-off", _cooloff, "daily", "20:30"),
]}


def job_time(ctx: Ctx, job: Job) -> str:
    return (ctx.settings.get("schedule_times") or {}).get(job.name) or job.time


def resolve_unit(name: str) -> tuple[str, object]:
    """('service'|'job', 객체). 없으면 KeyError."""
    if name in SERVICES:
        return "service", SERVICES[name]
    if name in JOBS:
        return "job", JOBS[name]
    raise KeyError(name)
