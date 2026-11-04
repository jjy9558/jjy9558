"""건강검진표: 코드 저장소 상태 점검 카드(읽기 전용; --run 일 때만 테스트를 실행)."""
import os
import subprocess
import sys
from pathlib import Path

from .report import Report

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache", "_jjkit"}
TEXT_EXT = {".py", ".js", ".ts", ".md", ".txt", ".html", ".css", ".json", ".sh", ".yml", ".yaml", ".toml"}
OK, WARN = "✅", "⚠️"


def walk(root: Path):
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for f in sorted(files):
            yield Path(d) / f


def detect_tests(repo: Path):
    """실행할 테스트 명령(인자 리스트)과 설명. 없으면 (None, 사유)."""
    if (repo / "run_all_tests.py").is_file():
        return [sys.executable, "run_all_tests.py"], "run_all_tests.py"
    has_pytest_cfg = (repo / "pytest.ini").is_file() or (repo / "conftest.py").is_file() or \
        "[tool.pytest" in _read(repo / "pyproject.toml")
    tests = repo / "tests"
    if has_pytest_cfg:
        return [sys.executable, "-m", "pytest"], "pytest"
    if tests.is_dir() and any(tests.glob("test*.py")):
        return [sys.executable, "-m", "unittest", "discover", "-s", "tests"], "unittest discovery"
    if any(repo.glob("test*.py")):
        return [sys.executable, "-m", "unittest", "discover"], "unittest discovery"
    return None, "테스트 파일을 찾지 못함"


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def run_tests(cmd, cwd: Path, timeout: int):
    """(성공 여부, 요약). shell 없이 인자 리스트로 실행."""
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return False, f"{timeout}초 시간 초과"
    except OSError as e:
        return False, f"실행 실패: {e}"
    tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
    return r.returncode == 0, f"종료 코드 {r.returncode}; {tail[0][:100]}"


def git_summary(repo: Path):
    """git status --porcelain 요약. git 저장소가 아니면 None."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True,
                           timeout=20, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    mod = new = 0
    for ln in r.stdout.splitlines():
        if ln.startswith("??"):
            new += 1
        elif ln.strip():
            mod += 1
    return {"modified": mod, "untracked": new}


def large_files(repo: Path, limit_kb: int):
    out = []
    for p in walk(repo):
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        if sz > limit_kb * 1024:
            out.append((sz, p.relative_to(repo).as_posix()))
    return sorted(out, reverse=True)


def count_todos(repo: Path) -> int:
    n = 0
    for p in walk(repo):
        if p.suffix in TEXT_EXT:
            n += sum(1 for ln in _read(p).splitlines() if "TODO" in ln or "FIXME" in ln)
    return n


def missing_readmes(repo: Path):
    return [d.name for d in sorted(repo.iterdir())
            if d.is_dir() and d.name not in SKIP_DIRS and not d.name.startswith(".")
            and not any((d / n).is_file() for n in ("README.md", "README", "readme.md"))
            and any(p.is_file() for p in d.rglob("*.py"))]


def sync_check(repo: Path):
    """tools/jjkit_sync.py가 있으면 --check 실행. (상태 None=해당없음)"""
    tool = repo / "tools" / "jjkit_sync.py"
    if not tool.is_file():
        return None, "tools/jjkit_sync.py 없음(건너뜀)"
    try:
        r = subprocess.run([sys.executable, str(tool), "--check-all"], cwd=str(repo), capture_output=True, text=True,
                           timeout=60, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"실행 실패: {e}"
    return r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-1:][0][:100] if (r.stdout + r.stderr).strip() else f"종료 코드 {r.returncode}"


def build_report(repo, run=False, timeout=300, large_kb=1024) -> Report:
    repo = Path(repo)
    rep = Report(f"건강검진표: {repo.resolve().name}")
    rep.intro.append("읽기 전용 점검입니다. --run 을 줄 때만 테스트를 실행합니다.")
    rows = []
    cmd, how = detect_tests(repo)
    if cmd is None:
        rows.append([WARN, "테스트", how])
    elif run:
        ok, msg = run_tests(cmd, repo, timeout)
        rows.append([OK if ok else WARN, "테스트 실행", f"{how}: {msg}"])
    else:
        rows.append([OK, "테스트 명령", f"{how} 감지(실행하지 않음, --run 으로 실행)"])
    g = git_summary(repo)
    if g is None:
        rows.append([WARN, "git 상태", "git 저장소가 아니거나 git 사용 불가"])
    else:
        clean = not g["modified"] and not g["untracked"]
        rows.append([OK if clean else WARN, "git 상태", "깨끗함" if clean else f"변경 {g['modified']}개, 추적 안 됨 {g['untracked']}개"])
    big = large_files(repo, large_kb)
    rows.append([WARN if big else OK, "큰 파일", f"{large_kb}KB 초과 {len(big)}개" + (": " + ", ".join(f"{p}({s // 1024}KB)" for s, p in big[:5]) if big else "")])
    t = count_todos(repo)
    rows.append([WARN if t else OK, "TODO/FIXME", f"{t}개"])
    mr = missing_readmes(repo)
    rows.append([WARN if mr else OK, "하위 폴더 README", ("없음: " + ", ".join(mr)) if mr else "모두 있음"])
    st, msg = sync_check(repo)
    if st is None:
        rows.append(["-", "jjkit 동기화", msg])
    else:
        rows.append([OK if st else WARN, "jjkit 동기화", msg])
    warn = sum(1 for r in rows if r[0] == WARN)
    rep.add("검진 결과", [f"주의 {warn}건 / 전체 {len(rows)}항목"], ["상태", "항목", "내용"], rows)
    return rep
