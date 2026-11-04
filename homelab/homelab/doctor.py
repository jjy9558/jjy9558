"""homelab doctor: 설치 상태를 점검하고 고칠 방법을 한국어로 알려준다.

외부 사정(프로세스 실행, 네트워크, 포트, which)은 ``Deps`` 로 주입해서 테스트에서 가짜로 바꾼다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import health as H
from . import procs, secretstore
from ._jjkit.netutil import NetError, request_json
from .configgen import PHONE_BACKENDS, required_tokens
from .registry import JOBS, SERVICES, Ctx

ICON = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}
MIN_PY = (3, 12)

# 설정 키 -> (프로젝트 폴더, 로더 호출 코드). sys.argv[1] 이 설정 파일 경로.
LOADERS: dict[str, tuple[str, str]] = {
    "sidekick": ("s21-sidekick", "import sys; from sidekick.config import load_config; load_config(sys.argv[1])"),
    "jarvis-hub": ("jarvis-hub", "import sys; from jarvis.config import load_hub_config; load_hub_config(sys.argv[1])"),
    "jarvis-worker": ("jarvis-hub", "import sys; from jarvis.config import load_worker_config as f; "
                                    "f(sys.argv[1], environ={'JARVIS_TOKEN': 'x' * 20})"),
    "jarvis-worker-minipc": ("jarvis-hub", "import sys; from jarvis.config import load_worker_config as f; "
                                           "f(sys.argv[1], environ={'JARVIS_TOKEN': 'x' * 20})"),
    "phone-home": ("phone-home", "import sys; from phonehome.config import load; load(sys.argv[1])"),
    "object-memory": ("object-memory", "import sys; from objmem.config import load; load(sys.argv[1])"),
    "sound-watch": ("sound-watch", "import sys; from soundwatch.config import load_config; load_config(sys.argv[1])"),
    "briefing": ("community-briefing", "import sys; from briefing.config import load; load(sys.argv[1])"),
    "mbrief": ("morning-brief", "import sys; from pathlib import Path; from mbrief.config import load_config; "
                                "load_config(Path(sys.argv[1]))"),
    "gamecal": ("game-calendar", "import sys; from gamecal.config import load_config; load_config(sys.argv[1])"),
    "nagger": ("life-nagger", "import sys; from nagger.config import load_config; load_config(sys.argv[1])"),
}

OPTIONAL_TOOLS = [
    ("ffmpeg", "shorts-linter · sub-pipeline 이 영상/오디오를 읽을 때 필요해요", "winget install ffmpeg (또는 https://ffmpeg.org)"),
    ("nvidia-smi", "사이드킥 '출발 체크'의 GPU 사용률 확인(gpu_busy)에 쓰여요", "NVIDIA 드라이버를 설치하면 같이 들어와요"),
    ("pdftotext", "자비스가 PDF 를 요약할 때 필요해요 (poppler)", "poppler 를 설치하고 PATH 에 넣거나 워커 설정 pdftotext 에 경로를 적으세요"),
    ("tesseract", "paper-butler 의 사진 OCR 에 쓰여요", "UB-Mannheim 빌드 설치 + 한국어 언어 데이터(kor)"),
    ("tailscale", "S21 과 PC 를 잇는 tailnet / `tailscale serve` 에 필요해요", "https://tailscale.com/download"),
]


@dataclass
class Check:
    section: str
    level: str   # ok | warn | fail
    text: str
    hint: str = ""


def _real_run(argv: list[str], cwd: Path | None, timeout: float = 20.0) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, shell=False, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    return r.returncode, out


@dataclass
class Deps:
    which: Callable[[str], str | None] = shutil.which
    run: Callable[..., tuple[int, str]] = _real_run
    opener: object = None
    port_in_use: Callable[[str, int], bool] = H.port_in_use
    alive: Callable[[int], bool] = procs.pid_alive
    py_version: tuple = field(default_factory=lambda: tuple(sys.version_info[:3]))
    platform: str = sys.platform


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _read_token_file(path: Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


# ---------------------------------------------------------------------------------------
# 토큰 일관성 (순수 함수: 파일만 읽는다)
# ---------------------------------------------------------------------------------------
def backend_token(ctx: Ctx, svc: str) -> str | None:
    """뒤쪽 서비스가 *실제로 기대하는* 토큰 (그 서비스의 설정 파일에서 읽음)."""
    if svc == "sidekick":
        d = _read_json(ctx.cfg("sidekick"))
        return ((d or {}).get("server") or {}).get("token") if d else None
    if svc == "object-memory":
        d = _read_json(ctx.cfg("object-memory"))
        return (d or {}).get("token") if d else None
    if svc == "sound-watch":
        d = _read_json(ctx.cfg("sound-watch"))
        return ((d or {}).get("server") or {}).get("token") if d else None
    if svc == "voice-journal":
        return _read_token_file(ctx.cfg("voice-journal"))
    if svc == "jarvis-hub":  # phone-home 이 쓰는 기기는 s21
        d = _read_json(ctx.cfg("jarvis-hub"))
        return (((d or {}).get("devices") or {}).get("s21") or {}).get("token") if d else None
    return None


def phone_home_token(ph_cfg: dict, name: str) -> str | None:
    s = (ph_cfg.get("services") or {}).get(name)
    if not isinstance(s, dict):
        return None
    if s.get("token"):
        return s["token"]
    if s.get("token_file"):
        return _read_token_file(Path(s["token_file"]).expanduser())
    return None


def token_consistency(ctx: Ctx) -> list[Check]:
    sec = "토큰"
    out: list[Check] = []
    tokens = ctx.secrets["tokens"]
    need, _ = required_tokens(ctx)
    missing = [n for n in need if len(tokens.get(n, "")) < secretstore.MIN_TOKEN_LEN]
    if missing:
        out.append(Check(sec, "fail", "secrets.json 에 토큰이 없거나 짧아요: %s" % ", ".join(missing),
                         "homelab init 을 실행하세요 (이미 있는 토큰은 그대로 두고 빠진 것만 만들어요)"))
        return out
    vals = [tokens[n] for n in need]
    if len(set(vals)) != len(vals):
        out.append(Check(sec, "fail", "서로 다른 서비스가 같은 토큰을 쓰고 있어요", "homelab init --rotate-tokens --force"))
    else:
        out.append(Check(sec, "ok", "토큰 %d개가 모두 충분히 길고 서로 달라요" % len(vals)))

    if not ctx.is_enabled("phone-home"):
        return out
    ph = _read_json(ctx.cfg("phone-home"))
    if ph is None:
        out.append(Check(sec, "fail", "phone-home 설정을 읽을 수 없어요: %s" % ctx.cfg("phone-home"), "homelab init"))
        return out
    if (ph.get("server") or {}).get("token") != tokens.get("phone-home"):
        out.append(Check(sec, "fail", "phone-home 클라이언트 토큰이 secrets.json 과 달라요",
                         "homelab init --force (기존 파일은 .bak 으로 백업돼요)"))
    else:
        out.append(Check(sec, "ok", "phone-home 클라이언트 토큰이 secrets.json 과 같아요"))
    for ph_name, (svc, tok_name) in PHONE_BACKENDS.items():
        if not ctx.is_enabled(svc):
            continue
        label = "phone-home → %s" % ph_name
        ph_tok = phone_home_token(ph_cfg=ph, name=ph_name)
        real = backend_token(ctx, svc)
        if ph_tok is None:
            out.append(Check(sec, "warn", "%s: phone-home 설정에 이 서비스 토큰이 없어요" % label,
                             "homelab init --force 로 phone-home 설정을 다시 만들거나 services.%s 를 직접 채우세요" % ph_name))
            continue
        if real is None:
            out.append(Check(sec, "warn", "%s: 서비스 쪽 설정/토큰 파일을 읽을 수 없어요" % label, "homelab init"))
            continue
        if ph_tok != real:
            out.append(Check(sec, "fail", "%s: phone-home 이 가진 토큰과 서비스가 기대하는 토큰이 달라요" % label,
                             "homelab init --force (둘 다 secrets.json 값으로 다시 맞춰요)"))
        elif ph_tok != tokens.get(tok_name):
            out.append(Check(sec, "warn", "%s: 둘은 서로 같지만 secrets.json 값과는 달라요" % label,
                             "직접 바꾼 토큰이면 secrets.json 의 %s 도 같은 값으로 고치세요" % tok_name))
        else:
            out.append(Check(sec, "ok", "%s: 토큰 일치" % label))
    # 자비스 워커 / sidekick 연동
    if ctx.is_enabled("jarvis-hub"):
        hub = _read_json(ctx.cfg("jarvis-hub")) or {}
        main_tok = (((hub.get("devices") or {}).get("main")) or {}).get("token")
        if main_tok and main_tok != tokens.get("jarvis-main"):
            out.append(Check(sec, "fail", "자비스 허브의 main 기기 토큰이 secrets.json 과 달라요", "homelab init --force"))
    if ctx.is_enabled("jarvis-worker") and ctx.is_enabled("sidekick"):
        wk = _read_json(ctx.cfg("jarvis-worker")) or {}
        wt = (((wk.get("handlers") or {}).get("notify.say")) or {}).get("token")
        if wt is not None and wt != tokens.get("sidekick"):
            out.append(Check(sec, "fail", "자비스 워커의 notify.say 토큰이 사이드킥 토큰과 달라요", "homelab init --force"))
    return out


# ---------------------------------------------------------------------------------------
def _module_of(argv: list[str]) -> str | None:
    if "-m" in argv:
        i = argv.index("-m")
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def run_doctor(ctx: Ctx, deps: Deps | None = None, *, llm_url: str | None = None) -> list[Check]:
    d = deps or Deps()
    checks: list[Check] = []

    def add(section: str, level: str, text: str, hint: str = "") -> None:
        checks.append(Check(section, level, text, hint))

    # 1. 파이썬
    sec = "파이썬"
    v = d.py_version
    ver = ".".join(str(x) for x in v[:3])
    if tuple(v[:2]) >= MIN_PY:
        add(sec, "ok", "홈랩을 실행한 Python %s" % ver)
    else:
        add(sec, "fail", "Python %s - 3.12 이상이 필요해요" % ver, "https://www.python.org/downloads/ 에서 3.12+ 를 설치하세요")
    rc, out = d.run([ctx.python, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"], None)
    if rc != 0:
        add(sec, "fail", "설정된 python 을 실행할 수 없어요: %s" % ctx.python,
            "homelab init --python <python 경로> --force 로 다시 정하세요")
    else:
        got = out.strip().splitlines()[-1] if out.strip() else "?"
        try:
            ok = tuple(int(x) for x in got.split(".")[:2]) >= MIN_PY
        except ValueError:
            ok = False
        add(sec, "ok" if ok else "fail", "서버를 켤 python: %s (%s)" % (ctx.python, got),
            "" if ok else "3.12 이상 python 경로를 homelab.json 의 python 에 적으세요")

    # 2. 홈랩 폴더 / 비밀
    sec = "홈랩 폴더"
    L = ctx.layout
    if L.settings_path.exists():
        add(sec, "ok", "설정 %s" % L.settings_path)
    else:
        add(sec, "fail", "homelab.json 이 없어요: %s" % L.settings_path, "homelab init")
    if L.secrets_path.exists():
        perm = secretstore.permissions_ok(L.secrets_path)
        if perm is False:
            add(sec, "warn", "secrets.json 권한이 넓어요 (그룹/다른 사용자가 읽을 수 있음)", "chmod 600 %s" % L.secrets_path)
        elif perm is None:
            add(sec, "ok", "secrets.json 은 사용자 프로필 안에 있어요: %s" % L.secrets_path)
        else:
            add(sec, "ok", "secrets.json 권한 600")
    else:
        add(sec, "fail", "secrets.json 이 없어요", "homelab init")

    # 3. 프로젝트 / import
    sec = "프로젝트"
    if not ctx.repo_root.is_dir():
        add(sec, "fail", "저장소 폴더를 찾을 수 없어요: %s" % ctx.repo_root, "homelab init --repo-root <폴더>")
    else:
        seen: set[tuple[str, str]] = set()
        units = [(n, SERVICES[n].build(ctx), SERVICES[n].title) for n in ctx.enabled_services()]
        units += [(n, JOBS[n].build(ctx, []), JOBS[n].title) for n in JOBS]
        for name, spec, title in units:
            mod = _module_of(spec.argv)
            if not mod:
                continue
            if not spec.cwd.is_dir():
                add(sec, "fail", "%s: 프로젝트 폴더가 없어요 (%s)" % (name, spec.cwd), "repo_root 를 확인하세요")
                continue
            key = (str(spec.cwd), mod)
            if key in seen:
                continue
            seen.add(key)
            rc, out = d.run([ctx.python, "-c", "import %s" % mod], spec.cwd)
            if rc == 0:
                add(sec, "ok", "%s (%s) 를 불러올 수 있어요" % (spec.cwd.name, mod))
            else:
                last = out.strip().splitlines()[-1] if out.strip() else ""
                add(sec, "fail", "%s (%s) 를 불러오지 못했어요: %s" % (spec.cwd.name, mod, last),
                    "python 버전(3.12+)과 폴더 내용을 확인하세요")

    # 4. 설정 파일 / 로더
    sec = "설정 파일"
    keys: list[str] = []
    for n in ctx.enabled_services():
        keys += list(SERVICES[n].config_keys)
    for j in JOBS.values():
        keys += list(j.config_keys)
    done: set[str] = set()
    for key in keys:
        if key in done:
            continue
        done.add(key)
        path = ctx.cfg(key)
        if not path.exists():
            add(sec, "fail", "%s 설정이 없어요: %s" % (key, path), "homelab init")
            continue
        loader = LOADERS.get(key)
        if not loader:
            add(sec, "ok", "%s: %s" % (key, path))
            continue
        folder, code = loader
        rc, out = d.run([ctx.python, "-c", code, str(path)], ctx.project(folder))
        if rc == 0:
            add(sec, "ok", "%s: 프로젝트 로더가 받아들여요" % key)
        else:
            last = out.strip().splitlines()[-1] if out.strip() else ""
            add(sec, "fail", "%s: 프로젝트 로더가 거부했어요 - %s" % (key, last[:160]),
                "homelab init --force 로 다시 만들거나 %s 를 직접 고치세요" % path)
    if "gamecal" in done and not (ctx.settings.get("appids")):
        add(sec, "warn", "game-calendar 에 추적할 게임(appid)이 없어요",
            "homelab init --appids 570,730 --force 또는 python -m gamecal add <appid> --config %s" % ctx.cfg("gamecal"))
    if ctx.settings.get("wallet_limit_usd", 0) == 0:
        add(sec, "warn", "Claude 사용량 일일 한도가 0(미설정)이라 지갑 알림이 안 와요",
            "homelab init --wallet-limit 20 --force (devguard 단가표 확인 후 prices_confirmed 를 true 로)")
    if ctx.is_enabled("gm-trpg"):
        world = Path(ctx.layout.data("gm-trpg")) / "worlds" / ((ctx.settings.get("gm_world") or "default") + ".db")
        if not world.exists():
            add(sec, "warn", "gm-trpg 세계가 없어요: %s" % world.name,
                "cd gm-trpg && python -m gmtrpg new --dir %s" % (ctx.layout.data("gm-trpg") / "worlds"))
    if ctx.is_enabled("shorts-vote"):
        polls = Path(ctx.layout.data("shorts-feedback")) / "polls"
        if not polls.is_dir() or not any(polls.iterdir()):
            add(sec, "warn", "썸네일 투표가 아직 없어요",
                "python -m shortsfb --data %s vote create 후보1.png 후보2.png" % ctx.layout.data("shorts-feedback"))

    checks += token_consistency(ctx)

    # 5. 포트
    sec = "포트"
    mgr = procs.Manager(ctx, port_in_use=d.port_in_use, alive=d.alive, opener=d.opener)
    seen_ports: dict[int, str] = {}
    for name in ctx.enabled_services():
        svc = SERVICES[name]
        port = ctx.port(name)
        if not port:
            continue
        if port in seen_ports:
            add(sec, "fail", "%s 와 %s 가 같은 포트 %d 를 써요" % (seen_ports[port], name, port),
                "homelab.json 의 ports 로 하나를 바꾸세요")
            continue
        seen_ports[port] = name
        host = procs.service_host(ctx, name)
        pid = mgr.running_pid(name)
        used = d.port_in_use(host, port)
        if pid and used:
            add(sec, "ok", "%s 포트 %d: 내가 켠 서비스가 쓰는 중 (PID %d)" % (name, port, pid))
        elif pid and not used:
            add(sec, "warn", "%s 는 떠 있는데 포트 %d 가 닫혀 있어요" % (name, port), "homelab logs %s" % name)
        elif used:
            h = mgr.health_of(svc)
            extra = ""
            if h.ok:
                extra = " (이 서비스 토큰으로 응답해요 - 직접 켜 둔 같은 서버일 수 있어요)"
            elif h.state == "auth":
                extra = " (응답은 하는데 홈랩 토큰을 거절해요 - 다른 설정으로 켜진 서버)"
            add(sec, "fail", "%s 포트 %d 를 홈랩이 아닌 프로그램이 쓰고 있어요%s" % (name, port, extra),
                "그 프로그램을 끄고 homelab up 하거나, homelab.json 의 ports 로 포트를 바꾸세요")
        else:
            add(sec, "ok", "%s 포트 %d: 비어 있어요" % (name, port))

    # 6. 선택 도구
    sec = "선택 도구"
    for tool, why, fix in OPTIONAL_TOOLS:
        if d.which(tool):
            add(sec, "ok", "%s 있음" % tool)
        else:
            add(sec, "warn", "%s 없음 - %s" % (tool, why), fix)
    add(sec, "ok", "node 는 홈랩에 필요 없어요 (sound-watch 의 선택 브라우저 시험에만 쓰여요)%s"
        % (" - 설치돼 있지만 안 써요" if d.which("node") else ""))

    # 7. 로컬 LLM
    sec = "로컬 LLM"
    url = (llm_url or ctx.llm_url).rstrip("/")
    if not url:
        add(sec, "warn", "LLM 주소를 정하지 않았어요 (요약/판정은 LLM 없이 간단 모드로 돌아요)",
            "homelab init --llm-url http://127.0.0.1:8080/v1 --force")
    else:
        try:
            status, data = request_json(url + "/models", opener=d.opener, timeout=3.0)
            n = len(data.get("data", [])) if isinstance(data, dict) and isinstance(data.get("data"), list) else None
            add(sec, "ok", "%s 응답 (HTTP %d%s)" % (url, status, ", 모델 %d개" % n if n is not None else ""))
        except NetError as e:
            add(sec, "warn", "LLM 서버에 닿지 않아요: %s (%s)" % (url, e),
                "llama-server -m <모델.gguf> --port 8080 -ngl 99 처럼 먼저 켜세요. "
                "꺼져 있어도 브리핑은 요약 없이(추출 요약), 사이드킥 알람은 수동 확인으로 돌아요")
    return checks


def render(checks: list[Check]) -> list[str]:
    lines: list[str] = []
    cur = None
    for c in checks:
        if c.section != cur:
            if cur is not None:
                lines.append("")
            lines.append("[%s]" % c.section)
            cur = c.section
        lines.append(" %s %s" % (ICON[c.level], c.text))
        if c.hint and c.level != "ok":
            lines.append("      → %s" % c.hint)
    n = {k: sum(1 for c in checks if c.level == k) for k in ICON}
    lines += ["", "요약: %s %d  %s %d  %s %d" % (ICON["ok"], n["ok"], ICON["warn"].strip(), n["warn"],
                                                  ICON["fail"], n["fail"])]
    if n["fail"]:
        lines.append("❌ 를 먼저 고친 뒤 `homelab up` 하세요.")
    elif n["warn"]:
        lines.append("⚠️  는 선택 기능이거나 나중에 해도 되는 것들이에요.")
    else:
        lines.append("모두 좋아요. `homelab up` 하세요.")
    return lines


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.level == "fail" for c in checks) else 0
