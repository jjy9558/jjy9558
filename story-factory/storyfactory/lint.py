"""shorts-linter 호출(전부 subprocess). 없으면 '생략'으로 표시하고 정상 진행한다.

- ``promise``: shorts-linter 에는 단독 명령이 없어(영상 ``check`` 안에 들어 있음) 작은 ``-c`` 스크립트로
  ``shortslint.promise.check_promise`` 만 실행한다. 영상 없이도 대본-제목 약속을 볼 수 있다.
- ``titles`` / ``disclose`` / ``similar``: shortslint CLI 명령을 그대로 호출한다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

TIMEOUT = 60

_PROMISE_SCRIPT = r"""
import json, sys
from shortslint.config import load_config
from shortslint import promise, metadata, titles
meta = metadata.load_meta(sys.argv[1])
tr = titles.load_transcript(meta, sys.argv[1])
res = promise.check_promise(meta, tr, load_config(None))
print(json.dumps([r.to_dict() for r in res], ensure_ascii=False))
"""
_RANK_RE = re.compile(r"^\s*\d+\.\s+\[(\d+)점\]\s+(.*?)\s{2}\(길이")


def find_linter_dir(explicit: str | Path | None = None) -> Path | None:
    """shorts-linter 폴더(shortslint 패키지가 든 곳). 인자 > SHORTSLINT_DIR > 형제 폴더 순."""
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("SHORTSLINT_DIR"):
        cands.append(Path(os.environ["SHORTSLINT_DIR"]))
    cands.append(Path(__file__).resolve().parents[2] / "shorts-linter")
    for c in cands:
        if (c / "shortslint" / "__init__.py").is_file():
            return c
    return None


@dataclass
class LintReport:
    available: bool = False
    note: str = ""
    promise: list = field(default_factory=list)       # [{name,status,message}]
    title_scores: list = field(default_factory=list)  # [(score, title)] 높은 점수 먼저
    disclose: str = ""
    similar_risk: bool = False
    similar_text: str = ""

    @property
    def promise_status(self) -> str:
        order = {"fail": 2, "warn": 1}
        worst = "ok"
        for r in self.promise:
            if order.get(r.get("status"), 0) > order.get(worst, 0):
                worst = r["status"]
        return worst if self.promise else "skip"

    @property
    def best_title_score(self) -> int:
        return self.title_scores[0][0] if self.title_scores else 0

    def rank_key(self) -> tuple:
        """클수록 좋음: 약속 일치 > 유사도 위험 없음 > 제목 점수 80 이상(그 안에서는 소재 우선순위가 정한다)."""
        pr = {"ok": 2, "skip": 1, "warn": 1, "fail": 0}[self.promise_status] if self.available else 1
        return (pr, 0 if self.similar_risk else 1, 1 if self.best_title_score >= 80 else 0)


class Linter:
    def __init__(self, linter_dir: Path | None, cwd: Path):
        self.dir = linter_dir
        self.cwd = Path(cwd)

    @property
    def available(self) -> bool:
        return self.dir is not None

    def _run(self, args: list[str]) -> tuple[int, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(self.dir), env.get("PYTHONPATH", "")]))
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        p = subprocess.run([sys.executable, *args], cwd=str(self.cwd), env=env, capture_output=True,
                           timeout=TIMEOUT, shell=False)
        out = p.stdout.decode("utf-8", errors="replace")
        return p.returncode, out

    # -- 개별 명령 ---------------------------------------------------------
    def titles(self, meta_path: Path) -> list[tuple[int, str]]:
        code, out = self._run(["-m", "shortslint", "titles", str(meta_path)])
        if code != 0:
            raise RuntimeError("titles 실패 (코드 %d)" % code)
        rows = []
        for line in out.splitlines():
            m = _RANK_RE.match(line)
            if m:
                rows.append((int(m.group(1)), m.group(2).strip()))
        return rows

    def promise(self, meta_path: Path) -> list[dict]:
        code, out = self._run(["-c", _PROMISE_SCRIPT, str(meta_path)])
        if code != 0:
            raise RuntimeError("promise 실패 (코드 %d)" % code)
        data = json.loads(out.strip().splitlines()[-1])
        return [{"name": r["name"], "status": r["status"], "message": r["message"]} for r in data]

    def disclose(self, meta_path: Path) -> str:
        code, out = self._run(["-m", "shortslint", "disclose", str(meta_path)])
        if code != 0:
            raise RuntimeError("disclose 실패 (코드 %d)" % code)
        return out.strip()

    def similar(self, folder: Path, history: Path | None = None) -> dict[str, tuple[bool, str]]:
        """{대본 이름: (위험 여부, 한 줄)}"""
        args = ["-m", "shortslint", "similar", str(folder)]
        if history:
            args += ["--history", str(history)]
        code, out = self._run(args)
        res: dict[str, tuple[bool, str]] = {}
        if code not in (0, 1):
            return res
        for line in out.splitlines():
            m = re.match(r"^(\S+)\s+([^:\s]+):\s*(.*)$", line)
            if m and not line.startswith(" "):
                res[m.group(2)] = ("재활용 콘텐츠 위험" in line, m.group(3).strip())
        return res


def lint_all(linter: Linter, out_dir: Path, ids: list[str], history: Path | None = None,
             rewrite_title=None) -> dict[str, LintReport]:
    """각 후보(<id>.meta.json + <id>.txt)를 린트한다. 도구가 없거나 실패하면 available=False + 사유.

    rewrite_title(card_id, best_title): titles 점수가 가장 높은 제목으로 meta 를 갱신하는 콜백(선택).
    """
    reports = {i: LintReport() for i in ids}
    if not linter.available:
        for r in reports.values():
            r.note = "shorts-linter 를 찾지 못해 린트를 생략했습니다"
        return reports
    sim: dict = {}
    try:
        sim = linter.similar(Path(out_dir), history)
    except (OSError, subprocess.SubprocessError) as e:
        sim = {}
        for r in reports.values():
            r.note = "similar 생략 (%s)" % type(e).__name__
    for cid in ids:
        r = reports[cid]
        meta_path = Path(out_dir) / (cid + ".meta.json")
        try:
            r.title_scores = linter.titles(meta_path)
            if r.title_scores and rewrite_title is not None:
                rewrite_title(cid, r.title_scores[0][1])
            r.promise = linter.promise(meta_path)
            r.disclose = linter.disclose(meta_path)
            r.available = True
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, IndexError) as e:
            r.available = False
            r.note = "shorts-linter 실행 실패로 생략 (%s)" % type(e).__name__
            continue
        if cid in sim:
            r.similar_risk, r.similar_text = sim[cid]
    return reports
