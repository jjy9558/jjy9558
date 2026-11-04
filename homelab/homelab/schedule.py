"""윈도우 작업 스케줄러(schtasks)와 cron 명령 만들기.

스케줄러가 부르는 것은 항상 ``python homelab_run.py --dir <홈랩 폴더> run <작업>`` 한 줄이다.
토큰은 명령줄에 없고(실행할 때 secrets.json 에서 환경변수로 넣는다), 경로만 들어간다.

/tr 따옴표 규칙: 안쪽 명령줄을 CRT 규칙으로 한 번 감싸고(공백·한글 경로는 "..."),
그 문자열 전체를 바깥 명령줄의 인자 하나로 다시 감싼다(안쪽 따옴표는 \\" 로). 만든 뒤 일부러 거꾸로
해석해 원래 인자와 같은지 확인한다.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .registry import JOBS, Ctx, Job, job_time

TASK_PREFIX = "Homelab-"
RUNNER = Path(__file__).resolve().parents[1] / "homelab_run.py"
_SPECIAL = set(' \t\n\v"&|<>^()%!,;=\'')


class ScheduleError(ValueError):
    pass


def win_quote(arg: str) -> str:
    """CommandLineToArgvW 가 원래 문자열로 되돌려 주도록 감싼다 (cmd 특수문자도 따옴표 안으로)."""
    if "%" in arg:
        raise ScheduleError("경로나 인자에 '%%' 가 있으면 cmd 가 환경변수로 해석해서 지원하지 않아요: %r" % arg)
    if arg and not any(c in _SPECIAL for c in arg):
        return arg
    res = ['"']
    bs = 0
    for ch in arg:
        if ch == "\\":
            bs += 1
        elif ch == '"':
            res.append("\\" * (bs * 2 + 1))
            res.append('"')
            bs = 0
        else:
            res.append("\\" * bs)
            res.append(ch)
            bs = 0
    res.append("\\" * (bs * 2))
    res.append('"')
    return "".join(res)


def win_join(args: list[str]) -> str:
    return " ".join(win_quote(a) for a in args)


def parse_win_cmdline(s: str) -> list[str]:
    """CommandLineToArgvW 규칙으로 나눈다 (자체 검증과 테스트용)."""
    args: list[str] = []
    cur: list[str] = []
    in_q = False
    started = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in " \t" and not in_q:
            if started:
                args.append("".join(cur))
                cur, started = [], False
            i += 1
            continue
        started = True
        if c == "\\":
            j = i
            while j < n and s[j] == "\\":
                j += 1
            cnt = j - i
            if j < n and s[j] == '"':
                cur.append("\\" * (cnt // 2))
                if cnt % 2:
                    cur.append('"')
                else:
                    in_q = not in_q
                i = j + 1
            else:
                cur.append("\\" * cnt)
                i = j
            continue
        if c == '"':
            in_q = not in_q
            i += 1
            continue
        cur.append(c)
        i += 1
    if started:
        args.append("".join(cur))
    return args


@dataclass
class Task:
    job: Job
    name: str
    inner: list[str]       # 스케줄러가 실행할 프로그램 + 인자
    when: str
    time: str
    weekday: str

    def schtasks_argv(self) -> list[str]:
        tr = win_join(self.inner)
        if parse_win_cmdline(tr) != self.inner:  # 자체 검증
            raise ScheduleError("/tr 따옴표 검증에 실패했어요: %r" % tr)
        argv = ["schtasks", "/create", "/tn", self.name, "/tr", tr]
        if self.when == "daily":
            argv += ["/sc", "DAILY", "/st", self.time]
        elif self.when == "weekly":
            argv += ["/sc", "WEEKLY", "/d", self.weekday or "SUN", "/st", self.time]
        elif self.when == "logon":
            argv += ["/sc", "ONLOGON", "/delay", "0000:30"]
        else:
            raise ScheduleError("알 수 없는 주기: %s" % self.when)
        argv.append("/f")
        return argv

    def schtasks_line(self) -> str:
        argv = self.schtasks_argv()
        return "schtasks " + " ".join(win_quote(a) for a in argv[1:])

    def cron_line(self, python: str) -> str:
        cmd = [python, str(RUNNER), "--dir", self.inner[self.inner.index("--dir") + 1], "run", self.job.name]
        text = shlex.join(cmd).replace("%", "\\%") + " >/dev/null 2>&1"
        if self.when == "daily":
            h, m = self.time.split(":")
            return "%d %d * * * %s" % (int(m), int(h), text)
        if self.when == "weekly":
            h, m = self.time.split(":")
            dow = {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6}[self.weekday or "SUN"]
            return "%d %d * * %d %s" % (int(m), int(h), dow, text)
        return "@reboot sleep 30 && " + text


def build_tasks(ctx: Ctx, only: list[str] | None = None, *, hidden: bool = False,
                platform: str = sys.platform) -> list[Task]:
    python = ctx.python
    if hidden and platform == "win32":
        p = Path(python)
        cand = p.with_name("pythonw.exe")
        if p.name.lower() == "python.exe" and cand.exists():
            python = str(cand)
    names = only or list(JOBS)
    bad = [n for n in names if n not in JOBS]
    if bad:
        raise ScheduleError("알 수 없는 작업: %s (가능: %s)" % (", ".join(bad), ", ".join(JOBS)))
    tasks = []
    for n in names:
        job = JOBS[n]
        inner = [python, str(RUNNER), "--dir", str(ctx.layout.root), "run", n]
        tasks.append(Task(job, TASK_PREFIX + n, inner, job.when, job_time(ctx, job), job.weekday))
    return tasks


_WHEN_KO = {"daily": "매일 {t}", "weekly": "매주 {d} {t}", "logon": "로그온할 때"}
_DAY_KO = {"SUN": "일요일", "MON": "월요일", "TUE": "화요일", "WED": "수요일", "THU": "목요일", "FRI": "금요일",
           "SAT": "토요일"}


def when_text(t: Task) -> str:
    return _WHEN_KO[t.when].format(t=t.time, d=_DAY_KO.get(t.weekday, t.weekday))


def print_lines(ctx: Ctx, tasks: list[Task]) -> list[str]:
    out = ["# === 윈도우 (cmd 에 붙여넣기 / 작업 스케줄러) ===",
           "# 각 작업은 `homelab_run.py run <작업>` 을 부르고, 토큰은 실행할 때 secrets.json 에서 읽어요.",
           "# 이미 있는 같은 이름의 작업은 /f 로 덮어써요. 지우기: schtasks /delete /tn <이름> /f", ""]
    for t in tasks:
        out.append("REM %s - %s" % (t.job.title, when_text(t)))
        out.append(t.schtasks_line())
    out += ["", "# === 리눅스 / 미니PC (crontab -e 에 붙여넣기) ===",
            "# 시간대는 그 PC 의 시간입니다. 출력은 버리고, 로그는 ~/.homelab/logs/job-<이름>.log 에 쌓여요.", ""]
    for t in tasks:
        line = t.cron_line(ctx.python)
        if t.job.name == "life-nagger":
            out.append("# %s (윈도우 전용 기능이라 리눅스에서는 쓸모가 적어요)" % t.job.title)
            out.append("# " + line)
        else:
            out.append("# %s - %s" % (t.job.title, when_text(t)))
            out.append(line)
    return out


def install(tasks: list[Task], out, *, run: Callable = subprocess.run, platform: str = sys.platform) -> int:
    if platform != "win32":
        print("`--install` 은 윈도우에서만 돼요. 이 PC 에서는 --print 로 출력한 cron 줄을 쓰세요.", file=out)
        return 2
    bad = 0
    for t in tasks:
        argv = t.schtasks_argv()
        try:
            r = run(argv, capture_output=True, shell=False, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            print("❌ %s: 실행하지 못했어요 (%s)" % (t.name, e), file=out)
            bad += 1
            continue
        if r.returncode == 0:
            print("✅ %s - %s" % (t.name, when_text(t)), file=out)
        else:
            detail = ((r.stderr or b"") + (r.stdout or b"")).decode("utf-8", "replace")
            if "�" in detail:
                detail = ((r.stderr or b"") + (r.stdout or b"")).decode("cp949", "replace")
            print("❌ %s: %s" % (t.name, detail.strip()[:200]), file=out)
            bad += 1
    return 1 if bad else 0
