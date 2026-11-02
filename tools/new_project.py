#!/usr/bin/env python3
"""새 프로젝트 뼈대를 만든다 (표준 라이브러리만, Python 3.12+).

사용법::

    python tools/new_project.py morning-brief --pkg mbrief --desc "아침 방송국" --modules llm,notify,textwidth

만드는 것::

    <name>/
      pyproject.toml            requires-python >=3.12, 의존성 없음, 콘솔 스크립트 <name>
      README.md                 한국어 템플릿
      docs/demo.json            demo-maker 시나리오 뼈대
      <pkg>/__init__.py         __version__
      <pkg>/__main__.py         python -m <pkg>
      <pkg>/cli.py              argparse 뼈대: main(argv, out) -> int, 하위 명령 hello
      <pkg>/_jjkit/             --modules 를 주면 tools/jjkit_sync.py 로 복사
      tests/__init__.py
      tests/test_cli.py         python -m unittest discover -s tests 로 바로 통과

이미 있는 폴더에는 만들지 않는다.
"""
import argparse
import importlib.util
import json
import keyword
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
PKG_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _load_sync():
    spec = importlib.util.spec_from_file_location("jjkit_sync", HERE / "jjkit_sync.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
def pyproject(name: str, pkg: str, desc: str) -> str:
    return f'''[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "0.1.0"
description = {json.dumps(desc, ensure_ascii=False)}
requires-python = ">=3.12"
dependencies = []

[project.scripts]
{name} = "{pkg}.cli:main"

[tool.setuptools.packages.find]
include = ["{pkg}", "{pkg}.*"]
'''


def init_py(name: str, desc: str) -> str:
    return f'"""{name}: {desc}"""\n\n__version__ = "0.1.0"\n'


MAIN_PY = "from .cli import main\n\nraise SystemExit(main())\n"


def cli_py(name: str, pkg: str, desc: str) -> str:
    return f'''"""명령줄 진입점: python -m {pkg} <명령>."""
import argparse
import sys
from typing import TextIO

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="{name}", description={desc!r})
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hello", help="동작 확인용 인사")
    h.add_argument("who", nargs="?", default="세상")
    return p


def cmd_hello(args: argparse.Namespace, out: TextIO) -> int:
    print(f"안녕하세요, {{args.who}}!", file=out)
    return 0


COMMANDS = {{"hello": cmd_hello}}


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # cp949 콘솔에서 한글/이모지로 죽지 않게
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return COMMANDS[args.cmd](args, out or sys.stdout)
'''


def test_cli_py(pkg: str) -> str:
    return f'''import io
import unittest

from {pkg}.cli import main


class CliTests(unittest.TestCase):
    def test_hello(self):
        buf = io.StringIO()
        self.assertEqual(main(["hello", "테스트"], out=buf), 0)
        self.assertIn("안녕하세요, 테스트!", buf.getvalue())

    def test_requires_command(self):
        with self.assertRaises(SystemExit):
            main([], out=io.StringIO())


if __name__ == "__main__":
    unittest.main()
'''


def readme(name: str, pkg: str, desc: str, modules: list[str]) -> str:
    if modules:
        jj = (f"`{pkg}/_jjkit/`에 jjkit 복사본({', '.join(modules)})이 들어 있습니다. "
              "**직접 고치지 말고** 원본 `jjkit/jjkit/`을 고친 뒤 저장소 루트에서 다시 복사하세요.\n\n"
              f"```\npython tools/jjkit_sync.py {name}\npython tools/jjkit_sync.py --check {name}\n```\n\n"
              f"코드에서는 `from ._jjkit import {modules[0]}` 처럼 씁니다.\n")
    else:
        jj = ("아직 jjkit 모듈을 쓰지 않습니다. 필요하면 저장소 루트에서:\n\n"
              f"```\npython tools/jjkit_sync.py {name} --pkg {pkg} --modules llm,notify\n```\n")
    return f'''# {name}

{desc}

<!-- demo:start -->
<!-- demo:end -->

## 설치

Python 3.12 이상, 외부 패키지 없음. 폴더에서 바로 실행합니다.

```
cd {name}
python -m {pkg} hello
```

선택: `pip install .` 하면 `{name} hello` 처럼 쓸 수 있습니다.

## 사용 예

```
python -m {pkg} hello            # 동작 확인
python -m {pkg} --help
```

## 데이터와 개인정보

- 데이터 위치: (적기)
- 외부로 나가는 것: (없음 / 적기)

## 공통 기반 (jjkit)

{jj}
## 테스트

```
cd {name}
python -m unittest discover -s tests -v
```

네트워크 없이 돕니다(가짜 opener와 픽스처 사용).
'''


def demo_json(name: str, pkg: str) -> str:
    data = {
        "title": f"{name}",
        "project": "..",
        "width": 84,
        "height": 20,
        "steps": [
            {"type": "comment", "text": "# 동작 확인"},
            {"type": "cmd", "run": f"python -m {pkg} hello", "show": f"{name} hello"},
            {"type": "pause", "seconds": 1.0},
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------------------
def create(name: str, pkg: str | None = None, desc: str = "", modules: list[str] | None = None,
           root: Path = ROOT, src: Path | None = None) -> Path:
    if not NAME_RE.match(name):
        raise ValueError("프로젝트 이름은 소문자, 숫자, 하이픈만 (예: morning-brief): %r" % name)
    pkg = pkg or name.replace("-", "_")
    if not PKG_RE.match(pkg) or keyword.iskeyword(pkg):
        raise ValueError("패키지 이름은 소문자 파이썬 식별자여야 합니다: %r" % pkg)
    desc = desc or f"{name} (설명을 적어 주세요)"
    proj = Path(root) / name
    if proj.exists():
        raise FileExistsError("이미 있습니다: %s" % proj)
    sync = _load_sync()
    src = src or (Path(root) / "jjkit" / "jjkit")
    if not src.is_dir():
        src = sync.SRC
    final_mods = sync.with_deps(modules, src) if modules else []
    files = {
        "pyproject.toml": pyproject(name, pkg, desc),
        "README.md": readme(name, pkg, desc, [m for m in final_mods if m != "netutil"] or final_mods),
        "docs/demo.json": demo_json(name, pkg),
        f"{pkg}/__init__.py": init_py(name, desc),
        f"{pkg}/__main__.py": MAIN_PY,
        f"{pkg}/cli.py": cli_py(name, pkg, desc),
        "tests/__init__.py": "",
        "tests/test_cli.py": test_cli_py(pkg),
    }
    for rel, text in files.items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    if final_mods:
        sync.sync(proj, pkg, final_mods, src)
    return proj


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="새 프로젝트 뼈대를 만든다.")
    ap.add_argument("name", help="폴더 이름 (예: morning-brief)")
    ap.add_argument("--pkg", help="패키지 이름 (기본: 이름의 '-'를 '_'로)")
    ap.add_argument("--desc", default="", help="한 줄 설명 (README, pyproject)")
    ap.add_argument("--modules", default="", help="같이 복사할 jjkit 모듈, 쉼표로 (예: llm,notify)")
    ap.add_argument("--root", default=str(ROOT), help="만들 위치 (기본: 저장소 루트)")
    a = ap.parse_args(argv)
    mods = [m.strip() for m in a.modules.split(",") if m.strip()]
    try:
        proj = create(a.name, a.pkg, a.desc, mods, Path(a.root))
    except (ValueError, FileExistsError) as e:
        print("오류: %s" % e, file=sys.stderr)
        return 2
    except Exception as e:
        if type(e).__name__ == "SyncError":
            print("오류: %s" % e, file=sys.stderr)
            return 2
        raise
    pkg = a.pkg or a.name.replace("-", "_")
    print("만들었습니다: %s" % proj)
    print("  cd %s && python -m %s hello" % (a.name, pkg))
    print("  python -m unittest discover -s tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
