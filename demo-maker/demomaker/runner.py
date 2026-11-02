"""데모 명령을 샌드박스(임시 HOME)에서 실제로 실행하고 출력을 시간과 함께 기록한다."""

import codecs
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE)", re.I)
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)(?::([^}]*))?\s*\}\}")


class DemoError(RuntimeError):
    pass


class Sandbox:
    """임시 디렉터리 하나에 home/ work/ tmp/ 를 만든다. with 문을 나가면 지워진다."""

    def __init__(self):
        self.root = None

    def __enter__(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="demomaker-"))
        for d in ("home", "work", "tmp", "config", "data", "cache"):
            os.makedirs(os.path.join(self.root, d))
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.root, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.root, name)

    @property
    def home(self):
        return self.path("home")

    @property
    def work(self):
        return self.path("work")


def build_env(sandbox, script_env=None, width=80, base=None):
    """사용자 환경 -> 비밀값 제거 -> 기본값 -> 스크립트 env -> 샌드박스 경로(강제) 순서로 합친다."""
    base = os.environ if base is None else base
    env = {k: v for k, v in base.items() if not _SECRET_NAME.search(k)}
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",   # 다른 프로젝트 폴더에 __pycache__ 를 남기지 않음
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "COLUMNS": str(width),
        "TERM": "xterm-256color",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
    })
    env.update(script_env or {})
    env.update({
        "HOME": sandbox.home,
        "USERPROFILE": sandbox.home,
        "APPDATA": sandbox.path("config"),
        "LOCALAPPDATA": sandbox.path("data"),
        "XDG_CONFIG_HOME": sandbox.path("config"),
        "XDG_DATA_HOME": sandbox.path("data"),
        "XDG_CACHE_HOME": sandbox.path("cache"),
        "TMPDIR": sandbox.path("tmp"),
        "TEMP": sandbox.path("tmp"),
        "TMP": sandbox.path("tmp"),
    })
    return env


def expand(value, ctx):
    """{{sandbox}} {{home}} {{work}} {{script_dir}} {{project}} {{days_ago:N}} {{hours_ago:N}} 를 치환한다.
    문자열, 리스트, 딕셔너리를 재귀적으로 처리한다."""
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda m: _placeholder(m, ctx), value)
    if isinstance(value, list):
        return [expand(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: expand(v, ctx) for k, v in value.items()}
    return value


def _placeholder(m, ctx):
    name, arg = m.group(1), m.group(2)
    if name in ("days_ago", "hours_ago", "minutes_ago"):
        try:
            n = float(arg)
        except (TypeError, ValueError):
            raise DemoError("{{%s:N}} 에는 숫자가 필요해요." % name)
        unit = {"days_ago": "days", "hours_ago": "hours", "minutes_ago": "minutes"}[name]
        return (ctx["now"] - timedelta(**{unit: n})).replace(microsecond=0).isoformat()
    if name in ctx and name != "now":
        return str(ctx[name])
    raise DemoError("알 수 없는 자리표시자: %s" % m.group(0))


def resolve_argv(run, ctx):
    """run 문자열을 shlex 로 쪼개 인자 목록으로 만든다 (shell=False 로 실행하기 위해).
    python/python3 는 지금 쓰는 인터프리터로 바꾼다."""
    try:
        argv = shlex.split(run)
    except ValueError as e:
        raise DemoError("명령을 해석할 수 없어요 (%s): %s" % (e, run))
    if not argv:
        raise DemoError("빈 명령이에요.")
    argv = [expand(a, ctx) for a in argv]
    if argv[0] in ("python", "python3"):
        argv[0] = sys.executable
    return argv


def run_command(argv, cwd, env, timeout=30):
    """argv 를 shell=False 로 실행. (출력 조각 [(경과초, 텍스트)], 종료코드, 걸린 시간)을 돌려준다.
    stdout/stderr 는 도착한 순서대로 섞는다."""
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        )
    except OSError as e:
        raise DemoError("명령을 실행할 수 없어요 (%s): %s" % (e, " ".join(argv)))
    q = queue.Queue()

    def pump(stream):
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            data = os.read(stream.fileno(), 65536)
            if not data:
                tail = dec.decode(b"", final=True)
                if tail:
                    q.put((time.monotonic() - t0, tail))
                break
            q.put((time.monotonic() - t0, dec.decode(data)))
        q.put(None)

    threads = [threading.Thread(target=pump, args=(s,), daemon=True) for s in (proc.stdout, proc.stderr)]
    for t in threads:
        t.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait()
    for t in threads:
        t.join(5)
    chunks, done = [], 0
    while not q.empty():
        item = q.get()
        if item is None:
            done += 1
        else:
            chunks.append(item)
    chunks.sort(key=lambda c: c[0])
    for s in (proc.stdout, proc.stderr):
        s.close()
    if timed_out:
        raise DemoError("시간 초과(%ss): %s" % (timeout, " ".join(argv)))
    return chunks, proc.returncode, time.monotonic() - t0


class Recording:
    """실행 결과. render 가 읽는 입력이자 JSON 으로 저장할 수 있는 형식."""

    def __init__(self, title, width, height, line_delay, steps):
        self.title, self.width, self.height, self.line_delay, self.steps = title, width, height, line_delay, steps

    def to_dict(self):
        return {"title": self.title, "width": self.width, "height": self.height,
                "line_delay": self.line_delay, "steps": self.steps}

    @classmethod
    def from_dict(cls, d):
        return cls(d["title"], d["width"], d["height"], d.get("line_delay", 0.05), d["steps"])

    def dumps(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=1)


def record(script, now=None):
    """스크립트를 실제로 실행해 Recording 을 만든다. 모든 명령은 임시 HOME 샌드박스에서 돈다."""
    now = now or datetime.now()
    steps = []
    with Sandbox() as sb:
        ctx = {"sandbox": sb.root, "home": sb.home, "work": sb.work, "script_dir": script.base_dir,
               "now": now, "project": ""}
        if script.project:
            ctx["project"] = os.path.normpath(os.path.join(script.base_dir, script.project))
        cwd_raw = script.cwd if script.cwd is not None else (script.project or ".")
        cwd = os.path.normpath(os.path.join(script.base_dir, expand(cwd_raw, ctx)))
        if not os.path.isdir(cwd):
            raise DemoError("cwd 폴더가 없어요: %s" % cwd)
        env = build_env(sb, expand(script.env, ctx), script.width)

        for step in script.steps:
            t = step["type"]
            if t == "setup":
                _do_setup(step, sb, ctx)
            elif t == "pause":
                steps.append({"type": "pause", "seconds": float(step.get("seconds", 1))})
            elif t == "comment":
                steps.append({"type": "comment", "text": step["text"],
                              "typing_speed": float(step.get("typing_speed", 0.03))})
            else:
                argv = resolve_argv(step["run"], ctx)
                chunks, code, dur = run_command(argv, cwd, env, float(step.get("timeout", script.timeout)))
                if code != 0 and not step.get("allow_fail"):
                    out = "".join(c[1] for c in chunks)[-500:]
                    raise DemoError("명령이 종료코드 %s 로 끝났어요: %s\n%s" % (code, step["run"], out))
                if step.get("hidden"):
                    continue
                steps.append({
                    "type": "cmd", "text": step.get("show") or step["run"],
                    "typing_speed": float(step.get("typing_speed", 0.05)),
                    "output": [[round(dt, 3), txt] for dt, txt in chunks],
                    "exit_code": code, "duration": round(dur, 3),
                    "max_lines": step.get("max_lines"),
                })
    return Recording(script.title, script.width, script.height, script.line_delay, steps)


def _do_setup(step, sb, ctx):
    for rel, content in step.get("files", {}).items():
        rel = expand(rel, ctx)
        dest = os.path.normpath(os.path.join(sb.root, rel))
        if not (dest + os.sep).startswith(sb.root + os.sep):
            raise DemoError("샌드박스 밖에 쓸 수 없어요: %s" % rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        content = expand(content, ctx)
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        with open(dest, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
