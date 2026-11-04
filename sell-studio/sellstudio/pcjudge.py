"""PC 부품이면 used-pc-judge 를 subprocess 로 한 번 불러 본다. 없거나 실패하면 건너뛰었다고 표시한다.

찾는 순서: --judge-cmd(또는 SELLSTUDIO_JUDGE_CMD) -> --judge-dir(또는 SELLSTUDIO_USEDPC_DIR) -> 저장소의 형제 폴더
``../used-pc-judge`` -> PATH 의 ``used-pc-judge`` 명령. 네트워크를 쓰지 않는다(판정기 쪽 LLM은 기본 꺼짐).
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PC_KEYWORDS = re.compile(
    r"RTX|GTX|GeForce|지포스|Radeon|라데온|\bRX\s?\d{3,4}|라이젠|Ryzen|\bi[3579]-?\d{4,5}|인텔\s*코어|Core\s*i[3579]|"
    r"DDR[345]|\bSSD\b|\bNVMe\b|메인보드|그래픽\s*카드|\bVGA\b|파워\s*서플라이|\b\d{3,4}\s?W\s*파워|\bCPU\b|\bGPU\b|"
    r"램\s*\d+\s*GB|\bRAM\b|PC\s*케이스|쿨러", re.I)


def looks_like_pc(data: dict) -> bool:
    if data.get("kind") == "pc":
        return True
    blob = " ".join([data.get("name", ""), data.get("spec", ""), data.get("notes", "")]
                    + [p.get("caption", "") + " " + p.get("item", "") for p in data.get("photos", [])])
    return bool(PC_KEYWORDS.search(blob))


def find_judge(judge_cmd: str | None = None, judge_dir: str | None = None) -> tuple[list[str], str | None] | None:
    """(argv 앞부분, cwd) 또는 None."""
    judge_cmd = judge_cmd or os.environ.get("SELLSTUDIO_JUDGE_CMD")
    if judge_cmd:
        # Windows 경로의 역슬래시가 POSIX 모드 shlex 에서 사라지므로 nt 에서는 non-posix 로 나누고 따옴표를 벗긴다.
        if os.name == "nt":
            argv = [a[1:-1] if len(a) > 1 and a[0] == a[-1] and a[0] in "\"'" else a
                    for a in shlex.split(judge_cmd, posix=False)]
        else:
            argv = shlex.split(judge_cmd)
        if argv and (shutil.which(argv[0]) or Path(argv[0]).exists()):
            return argv, None
        return None
    judge_dir = judge_dir or os.environ.get("SELLSTUDIO_USEDPC_DIR")
    cands = [Path(judge_dir)] if judge_dir else [Path(__file__).resolve().parents[2] / "used-pc-judge"]
    for d in cands:
        if (d / "usedpc" / "__main__.py").is_file():
            return [sys.executable, "-m", "usedpc"], str(d)
    exe = shutil.which("used-pc-judge")
    if exe and not judge_dir:
        return [exe], None
    return None


def listing_text(data: dict, price: int | None) -> str:
    lines = [data.get("name", "") or "PC 부품", data.get("spec", "")]
    if price:
        lines.append("가격 %s원" % format(price, ","))
    if data.get("condition"):
        lines.append("상태 " + data["condition"])
    lines += list(data.get("defects", []))
    return "\n".join(x for x in lines if x)


def run_pc_check(data: dict, price: int | None = None, judge_cmd: str | None = None,
                 judge_dir: str | None = None, timeout: float = 30.0) -> dict:
    """결과: {status: 'ran'|'skipped'|'not_pc', reason, output}. 예외를 밖으로 내지 않는다."""
    if not looks_like_pc(data):
        return {"status": "not_pc", "reason": "PC 부품 키워드가 없어 조회하지 않음", "output": ""}
    found = find_judge(judge_cmd, judge_dir)
    if found is None:
        return {"status": "skipped", "reason": "used-pc-judge 를 찾지 못해 건너뜀 (--judge-dir 또는 --judge-cmd 로 지정)",
                "output": ""}
    argv, cwd = found
    with tempfile.TemporaryDirectory(prefix="sellstudio-") as tmp:
        f = Path(tmp) / "listing.txt"
        f.write_text(listing_text(data, price), encoding="utf-8")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        try:
            r = subprocess.run(argv + ["judge", str(f), "--no-questions"], cwd=cwd, capture_output=True,
                               timeout=timeout, env=env)
        except (OSError, subprocess.SubprocessError) as e:
            return {"status": "skipped", "reason": "used-pc-judge 실행 실패로 건너뜀 (%s)" % type(e).__name__, "output": ""}
    out = r.stdout.decode("utf-8", "replace").strip()
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        return {"status": "skipped", "reason": "used-pc-judge 가 오류로 끝나 건너뜀 (코드 %d: %s)" % (r.returncode, err[0][:80]),
                "output": ""}
    lines = out.splitlines()
    if len(lines) > 40:
        lines = lines[:40] + ["... (이하 생략)"]
    return {"status": "ran", "reason": "used-pc-judge 조회 완료", "output": "\n".join(lines)}


def render_pc_check(res: dict | None) -> list[str]:
    if not res:
        return []
    if res["status"] == "not_pc":
        return []
    head = "PC 부품 조회(used-pc-judge): " + res["reason"]
    if res["status"] == "ran":
        return [head, "  (판정은 used-pc-judge의 임시 가격표 기준입니다. 참고용)"] + ["  | " + l for l in res["output"].splitlines()]
    return [head]
