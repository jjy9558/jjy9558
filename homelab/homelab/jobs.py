"""``homelab run <작업>``: 토큰을 환경변수로 넣어 프로젝트의 주기 작업을 한 번 실행하고 로그에 남긴다.

작업 스케줄러/cron 은 이 명령을 부른다. 그래서 스케줄 명령줄에는 토큰이 없고, 경로만 들어간다.
"""
from __future__ import annotations

import os
import subprocess
from typing import TextIO

from . import logrun
from .registry import JOBS, Ctx


def run_job(ctx: Ctx, name: str, extra: list[str], out: TextIO, *, popen=subprocess.Popen) -> int:
    job = JOBS[name]
    spec = job.build(ctx, extra)
    if not spec.cwd.is_dir():
        print("프로젝트 폴더가 없어요: %s (homelab.json 의 repo_root 확인)" % spec.cwd, file=out)
        return 2
    for key in job.config_keys:
        cfg = ctx.cfg(key)
        if not cfg.exists():
            print("설정 파일이 없어요: %s → 먼저 `homelab init`" % cfg, file=out)
            return 2
    log_path = ctx.layout.log_file("job-" + name)
    max_bytes = int(float(ctx.settings.get("log_max_mb") or 2) * 1024 * 1024)
    backups = int(ctx.settings.get("log_backups") or 3)
    log = logrun.RotatingLog(log_path, max_bytes, backups)
    env = dict(os.environ)
    env.update(spec.env)
    log.write(("[homelab %s] 작업 시작: %s\n" % (logrun.stamp(), name)).encode("utf-8"))
    kwargs = {}
    if os.name == "nt":  # pragma: no cover
        kwargs["creationflags"] = 0x08000000
    try:
        proc = popen(spec.argv, cwd=str(spec.cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, bufsize=0, shell=False, **kwargs)
    except OSError as e:
        msg = "실행하지 못했어요: %s (python 경로: %s)" % (e, ctx.python)
        log.write((msg + "\n").encode("utf-8", "replace"))
        log.close()
        print(msg, file=out)
        return 127
    try:
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            log.write(chunk)
            out.write(chunk.decode("utf-8", errors="replace"))
            try:
                out.flush()
            except (OSError, ValueError):
                pass
    except KeyboardInterrupt:
        proc.terminate()
    rc = proc.wait()
    log.write(("[homelab %s] 종료 코드 %s\n" % (logrun.stamp(), rc)).encode("utf-8"))
    log.close()
    return rc
