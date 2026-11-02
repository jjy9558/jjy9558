#!/usr/bin/env python3
"""jjkit 원본(``jjkit/jjkit/*.py``)을 프로젝트 패키지 안 ``_jjkit/``로 복사(vendoring)하고 검사한다.

사용법::

    python tools/jjkit_sync.py morning-brief --pkg mbrief --modules llm,notify,textwidth
    python tools/jjkit_sync.py morning-brief            # 이미 복사된 모듈만 최신으로 (패키지 자동 탐지)
    python tools/jjkit_sync.py --check morning-brief    # 이 프로젝트 복사본 검사
    python tools/jjkit_sync.py --check-all              # 저장소 전체 검사 (CI)
    python tools/jjkit_sync.py --list                   # 모듈 목록과 복사 현황

복사본 첫 줄은 ``# generated from jjkit@<sha256 앞 8자리>; 직접 고치지 말 것`` 이다.
해시는 원본 파일 내용(줄끝 LF로 정규화)으로 계산한다. 검사는 두 가지를 본다.

- 낡음: 헤더 해시 != 지금 원본 해시  -> 원본이 바뀌었으니 다시 동기화
- 수정됨: 헤더 아래 본문 해시 != 헤더 해시 -> 복사본을 손으로 고쳤음(원본을 고치고 다시 동기화)

모듈 사이 의존성(``from .netutil import ...``)은 자동으로 같이 복사한다.
복사본이 하나도 없으면 ``--check-all``은 통과한다. 표준 라이브러리만 쓴다.
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "jjkit" / "jjkit"
COPY_DIR = "_jjkit"
HEADER = "# generated from jjkit@{sha}; 직접 고치지 말 것"
HEADER_RE = re.compile(r"^# generated from jjkit@([0-9a-f]{8});")
_DEP_RE = re.compile(r"^\s*from \.(\w+) import|^\s*from \. import ([\w, ]+)", re.M)
_SKIP_PROJECT_DIRS = {"jjkit", "tools"}


class SyncError(Exception):
    pass


# ---------------------------------------------------------------------------
def normalize(data: bytes) -> bytes:
    """줄끝을 LF로 (Windows 체크아웃의 CRLF 때문에 해시가 달라지지 않게)."""
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def sha8(data: bytes) -> str:
    return hashlib.sha256(normalize(data)).hexdigest()[:8]


def available_modules(src: Path = SRC) -> list[str]:
    return sorted(p.stem for p in src.glob("*.py") if p.name != "__init__.py")


def module_deps(name: str, src: Path = SRC) -> set[str]:
    text = (src / (name + ".py")).read_text(encoding="utf-8")
    out = set()
    for a, b in _DEP_RE.findall(text):
        if a:
            out.add(a)
        if b:
            out.update(x.strip() for x in b.split(",") if x.strip())
    return out


def with_deps(names: list[str] | set[str], src: Path = SRC) -> list[str]:
    avail = set(available_modules(src))
    unknown = sorted(set(names) - avail)
    if unknown:
        raise SyncError("jjkit에 없는 모듈: %s (있는 것: %s)" % (", ".join(unknown), ", ".join(sorted(avail))))
    todo, seen = list(names), set()
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        todo.extend(d for d in module_deps(n, src) if d in avail and d not in seen)
    return sorted(seen)


def render_copy(src_bytes: bytes) -> str:
    body = normalize(src_bytes).decode("utf-8")
    return HEADER.format(sha=sha8(src_bytes)) + "\n" + body


def render_init(modules: list[str]) -> str:
    body = ('"""jjkit 복사본 (tools/jjkit_sync.py가 만든 파일). 원본: jjkit/jjkit/."""\n'
            "MODULES = (%s)\n" % "".join('"%s", ' % m for m in modules).rstrip(" "))
    return HEADER.format(sha=sha8(body.encode("utf-8"))) + "\n" + body


def split_copy(text: str) -> tuple[str | None, str]:
    """복사본 텍스트 -> (헤더 해시 또는 None, 본문)."""
    first, _, rest = text.partition("\n")
    m = HEADER_RE.match(first)
    return (m.group(1), rest) if m else (None, text)


# ---------------------------------------------------------------------------
def find_package(project: Path) -> str:
    cands = sorted(p.name for p in project.iterdir()
                   if p.is_dir() and (p / "__init__.py").exists() and p.name not in ("tests", "docs"))
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise SyncError("%s 안에서 파이썬 패키지를 찾지 못했습니다 (--pkg 로 지정)" % project)
    raise SyncError("패키지가 여러 개입니다: %s (--pkg 로 지정)" % ", ".join(cands))


def copied_modules(copy_dir: Path) -> list[str]:
    if not copy_dir.is_dir():
        return []
    return sorted(p.stem for p in copy_dir.glob("*.py") if p.name != "__init__.py")


def _write_if_changed(path: Path, text: str) -> bool:
    data = text.encode("utf-8")
    if path.exists() and normalize(path.read_bytes()) == data:
        return False
    path.write_bytes(data)
    return True


def sync(project: Path, pkg: str | None = None, modules: list[str] | None = None,
         src: Path = SRC) -> list[str]:
    """복사하고 바뀐 파일 경로(프로젝트 기준 상대 경로) 목록을 돌려준다.

    modules가 None이면 이미 복사된 모듈(없으면 전부)을 갱신한다. 이미 있던 모듈은 지우지 않는다.
    """
    project = Path(project)
    if not project.is_dir():
        raise SyncError("프로젝트 폴더가 없습니다: %s" % project)
    pkg = pkg or find_package(project)
    pkg_dir = project / pkg
    if not (pkg_dir / "__init__.py").exists():
        raise SyncError("%s 는 패키지가 아닙니다 (__init__.py 없음)" % pkg_dir)
    copy_dir = pkg_dir / COPY_DIR
    existing = copied_modules(copy_dir)
    wanted = set(existing) | set(modules if modules is not None else (existing or available_modules(src)))
    final = with_deps(wanted, src)
    copy_dir.mkdir(exist_ok=True)
    changed = []
    for name in final:
        if _write_if_changed(copy_dir / (name + ".py"), render_copy((src / (name + ".py")).read_bytes())):
            changed.append(name + ".py")
    if _write_if_changed(copy_dir / "__init__.py", render_init(final)):
        changed.append("__init__.py")
    return [str(Path(pkg) / COPY_DIR / c) for c in changed]


def find_copies(root: Path = ROOT) -> list[Path]:
    """``<root>/<project>/<pkg>/_jjkit`` 폴더 목록."""
    out = []
    for proj in sorted(Path(root).iterdir()):
        if not proj.is_dir() or proj.name.startswith(".") or proj.name in _SKIP_PROJECT_DIRS:
            continue
        for pkg in sorted(proj.iterdir()):
            d = pkg / COPY_DIR
            if pkg.is_dir() and d.is_dir():
                out.append(d)
    return out


def check_copy_dir(copy_dir: Path, src: Path = SRC) -> list[str]:
    """문제 목록(빈 목록이면 통과)."""
    problems = []
    avail = set(available_modules(src))
    mods = copied_modules(copy_dir)
    for name in mods:
        f = copy_dir / (name + ".py")
        sha, body = split_copy(normalize(f.read_bytes()).decode("utf-8", errors="replace"))
        if sha is None:
            problems.append("%s: 생성 헤더가 없습니다" % f)
            continue
        if sha8(body.encode("utf-8")) != sha:
            problems.append("%s: 복사본을 직접 고쳤습니다 (원본 jjkit/jjkit/%s.py를 고치고 다시 동기화)" % (f, name))
            continue
        if name not in avail:
            problems.append("%s: jjkit에 더 이상 없는 모듈입니다 (파일을 지우고 다시 동기화)" % f)
            continue
        if sha8((src / (name + ".py")).read_bytes()) != sha:
            problems.append("%s: 낡았습니다 (jjkit@%s, 원본은 jjkit@%s)"
                            % (f, sha, sha8((src / (name + ".py")).read_bytes())))
    for name in mods:
        if name in avail:
            for dep in sorted(module_deps(name, src) & avail):
                if dep not in mods:
                    problems.append("%s: %s 가 쓰는 %s.py 가 없습니다" % (copy_dir, name, dep))
    init = copy_dir / "__init__.py"
    if not init.exists() or normalize(init.read_bytes()).decode("utf-8", "replace") != render_init(mods):
        problems.append("%s: __init__.py 가 모듈 목록과 다릅니다" % init)
    return problems


def check_project(project: Path, src: Path = SRC) -> list[str]:
    project = Path(project)
    problems = []
    for pkg in sorted(project.iterdir()) if project.is_dir() else []:
        d = pkg / COPY_DIR
        if pkg.is_dir() and d.is_dir():
            problems += check_copy_dir(d, src)
    return problems


def check_all(root: Path = ROOT, src: Path = SRC) -> tuple[int, list[str]]:
    dirs = find_copies(root)
    problems = []
    for d in dirs:
        problems += check_copy_dir(d, src)
    return len(dirs), problems


# ---------------------------------------------------------------------------
def _resolve_project(arg: str, root: Path) -> Path:
    p = Path(arg)
    if p.is_dir():
        return p.resolve()
    if (root / arg).is_dir():
        return (root / arg).resolve()
    raise SyncError("프로젝트 폴더가 없습니다: %s" % arg)


def _rel(p: Path, root: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(root.resolve()))
    except ValueError:
        return str(p)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="jjkit 모듈을 프로젝트에 복사하고 검사한다.")
    ap.add_argument("project", nargs="?", help="프로젝트 폴더 (예: morning-brief)")
    ap.add_argument("--pkg", help="패키지 이름 (생략하면 자동 탐지)")
    ap.add_argument("--modules", help="복사할 모듈, 쉼표로 (예: llm,notify). 생략하면 이미 있는 것/전부")
    ap.add_argument("--check", action="store_true", help="이 프로젝트의 복사본이 최신인지 검사")
    ap.add_argument("--check-all", action="store_true", help="저장소의 모든 복사본 검사 (CI)")
    ap.add_argument("--list", action="store_true", help="모듈 목록과 복사 현황")
    ap.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)
    ap.add_argument("--src", default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    src = Path(a.src).resolve() if a.src else root / "jjkit" / "jjkit"
    try:
        if not src.is_dir():
            raise SyncError("jjkit 원본 폴더가 없습니다: %s" % src)
        if a.list:
            for m in available_modules(src):
                deps = sorted(module_deps(m, src) & set(available_modules(src)))
                print("%-12s jjkit@%s%s" % (m, sha8((src / (m + ".py")).read_bytes()),
                                             "  (의존: %s)" % ", ".join(deps) if deps else ""))
            for d in find_copies(root):
                print("복사본: %s [%s]" % (_rel(d, root), ", ".join(copied_modules(d))))
            return 0
        if a.check_all:
            n, problems = check_all(root, src)
            for p in problems:
                print("실패: " + p, file=sys.stderr)
            if problems:
                print("jjkit 복사본 %d곳 중 문제 %d개. 고치기: python tools/jjkit_sync.py <프로젝트>"
                      % (n, len(problems)), file=sys.stderr)
                return 1
            print("jjkit 복사본 검사 통과 (%d곳)" % n)
            return 0
        if not a.project:
            ap.error("프로젝트 폴더를 주거나 --check-all / --list 를 쓰세요")
        project = _resolve_project(a.project, root)
        if a.check:
            problems = check_project(project, src)
            for p in problems:
                print("실패: " + p, file=sys.stderr)
            if problems:
                return 1
            print("통과: %s" % _rel(project, root))
            return 0
        mods = [m.strip() for m in a.modules.split(",") if m.strip()] if a.modules else None
        changed = sync(project, a.pkg, mods, src)
        for c in changed:
            print("갱신: %s" % c)
        if not changed:
            print("이미 최신입니다.")
        return 0
    except SyncError as e:
        print("오류: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
