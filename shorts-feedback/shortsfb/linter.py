"""shorts-linter 연동(선택). 폴더가 있으면 subprocess로 ``titles``(약속 일치 점수)를 호출하고, 없으면 건너뛴다.

찾는 순서: 환경변수 ``SHORTSFB_LINTER`` -> 저장소의 형제 폴더 ``shorts-linter``.
shorts-linter에는 ``promise`` 하위 명령이 없고, 약속 일치 검사(promise.py)는 ``titles``/``check`` 안에서 돈다.
여기서는 메타(meta.json)의 제목 후보를 점수 매기는 ``titles``를 쓴다.
"""
import os
import subprocess
import sys
from pathlib import Path


def find_linter() -> Path | None:
    cands = []
    env = os.environ.get("SHORTSFB_LINTER")
    if env:
        cands.append(Path(env))
    cands.append(Path(__file__).resolve().parents[2] / "shorts-linter")
    for c in cands:
        if (c / "shortslint" / "__main__.py").is_file():
            return c
    return None


def run_promise(meta_path, timeout: float = 60.0, linter: Path | None = None) -> dict:
    """``{"available", "ok", "lines", "note"}``. 어떤 실패든 예외 없이 note에 이유를 담는다."""
    folder = linter or find_linter()
    if folder is None:
        return {"available": False, "ok": False, "lines": [], "note": "shorts-linter를 찾지 못해 건너뜀"}
    meta = Path(meta_path)
    if not meta.is_file():
        return {"available": True, "ok": False, "lines": [], "note": "meta.json이 없어 건너뜀: %s" % meta}
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        p = subprocess.run([sys.executable, "-m", "shortslint", "titles", str(meta.resolve())], cwd=str(folder),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"available": True, "ok": False, "lines": [], "note": "shorts-linter 실행 실패: %s" % type(e).__name__}
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()][:40]
    return {"available": True, "ok": p.returncode == 0, "lines": lines,
            "note": "" if p.returncode == 0 else "shorts-linter 종료 코드 %d" % p.returncode}
