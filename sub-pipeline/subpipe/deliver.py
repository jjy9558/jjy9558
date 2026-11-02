"""Assemble a delivery folder: SRT copy + QA report + disclosure line. Never overwrites the source."""
from __future__ import annotations

import os
import shutil
from typing import Optional

from . import config as cfgmod
from . import qareport, srt
from .qa import Limits, lint


class DeliverError(RuntimeError):
    pass


def deliver(src: str, outdir: str, limits: Limits, cfg: dict, force: bool = False,
            allow_errors: bool = False) -> dict:
    cues = srt.load(src)
    issues = lint(cues, limits)
    errs = sum(1 for i in issues if i.severity == "error")
    if errs and not allow_errors:
        raise DeliverError("QA 오류 %d건: 고친 뒤 다시 실행하거나 --allow-errors 를 사용하세요" % errs)
    stem = os.path.splitext(os.path.basename(src))[0]
    os.makedirs(outdir, exist_ok=True)
    dst = os.path.join(outdir, stem + ".srt")
    report = os.path.join(outdir, stem + ".qa.md")
    notice_path = os.path.join(outdir, stem + ".notice.txt")
    targets = [dst, report, notice_path]
    for t in targets:
        if os.path.exists(t) and os.path.samefile(t, src):
            raise DeliverError("납품 폴더가 원본과 같은 파일을 가리킵니다 (원본 보호): " + t)
    if not force:
        for t in targets:
            if os.path.exists(t):
                raise DeliverError("이미 존재합니다 (덮어쓰려면 --force): " + t)
    note = cfgmod.disclosure(cfg)
    shutil.copyfile(src, dst)
    with open(report, "w", encoding="utf-8", newline="\n") as f:
        f.write(qareport.render_md(stem, cues, limits, issues, note))
    with open(notice_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(note + "\n")
    return {"srt": dst, "report": report, "notice": notice_path, "errors": errs, "issues": len(issues)}
