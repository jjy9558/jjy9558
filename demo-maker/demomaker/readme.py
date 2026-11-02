"""프로젝트를 살펴보고 README 의 "사용법" 섹션(Markdown)을 만든다."""

import os
import re
import sys

from .runner import DemoError, Sandbox, build_env, run_command

START = "<!-- demo:start -->"
END = "<!-- demo:end -->"
_MARKERS = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)


# ---------- pyproject (tomllib 없이 필요한 부분만) ----------

def parse_pyproject(text):
    """[project] name/description 과 [project.scripts] 를 뽑는다. Python 3.9 에서도 동작."""
    info = {"name": None, "description": None, "scripts": {}}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"\[([^\]]+)\]", line)
        if m:
            section = m.group(1).strip()
            continue
        kv = re.match(r'^([A-Za-z0-9_.\-"]+)\s*=\s*(.+)$', line)
        if not kv:
            continue
        key, val = kv.group(1).strip('"'), kv.group(2).strip()
        sval = re.match(r'^"((?:[^"\\]|\\.)*)"|^\'([^\']*)\'', val)
        sval = (sval.group(1) if sval.group(1) is not None else sval.group(2)) if sval else None
        if section == "project" and key in ("name", "description") and sval is not None:
            info[key] = sval
        elif section == "project.scripts" and sval is not None:
            info["scripts"][key] = sval
    return info


def find_modules(project_dir, scripts):
    """`python -m <모듈>` 로 실행할 패키지 이름 후보 (스크립트 진입점 우선)."""
    mods = []
    for target in scripts.values():
        mod = target.split(":")[0].split(".")[0].strip()
        if mod and mod not in mods and os.path.isdir(os.path.join(project_dir, mod)):
            mods.append(mod)
    if not mods:
        for name in sorted(os.listdir(project_dir)):
            p = os.path.join(project_dir, name)
            if (os.path.isfile(os.path.join(p, "__main__.py")) and name.isidentifier()
                    and name not in ("tests", "test", "examples", "demomaker")):
                mods.append(name)
    return mods


def inspect_project(project_dir):
    project_dir = os.path.abspath(project_dir)
    if not os.path.isdir(project_dir):
        raise DemoError("폴더가 아니에요: %s" % project_dir)
    info = {"name": None, "description": None, "scripts": {}}
    py = os.path.join(project_dir, "pyproject.toml")
    if os.path.isfile(py):
        with open(py, encoding="utf-8") as f:
            info = parse_pyproject(f.read())
    info["dir"] = project_dir
    info["name"] = info["name"] or os.path.basename(project_dir)
    info["modules"] = find_modules(project_dir, info["scripts"])
    return info


# ---------- argparse 도움말 ----------

def run_help(project_dir, module, args=(), timeout=20):
    """`python -m module [args] --help` 를 샌드박스 HOME 에서 실행해 도움말 텍스트를 돌려준다."""
    argv = [sys.executable, "-m", module] + list(args) + ["--help"]
    with Sandbox() as sb:
        env = build_env(sb, {"PYTHONPATH": project_dir}, width=80)
        chunks, code, _ = run_command(argv, project_dir, env, timeout)
    text = "".join(c[1] for c in chunks).rstrip()
    if code != 0 or not text:
        raise DemoError("--help 실행 실패 (python -m %s %s, 종료코드 %s)" % (module, " ".join(args), code))
    return text


def parse_subcommands(help_text):
    """argparse 도움말의 {a,b,c} 블록 아래 (이름, 설명) 목록."""
    lines = help_text.splitlines()
    in_section = False
    for i, line in enumerate(lines):
        if line and not line[0].isspace():
            in_section = line.rstrip().endswith(":") and not line.startswith("usage:")
            continue
        m = re.match(r"^(\s+)\{([\w\-,.]+)\}", line) if in_section else None
        if not m:
            continue
        indent = len(m.group(1))
        names = m.group(2).split(",")
        subs, cur = [], None
        for nxt in lines[i + 1:]:
            if not nxt.strip():
                break
            ind = len(nxt) - len(nxt.lstrip())
            if ind <= indent:
                break
            body = nxt.strip()
            em = re.match(r"^([\w\-.]+)(?:\s{2,}(.*))?$", body)
            if em and em.group(1) in names and ind == indent + 2:
                cur = [em.group(1), (em.group(2) or "").strip()]
                subs.append(cur)
            elif cur is not None:
                cur[1] = (cur[1] + " " + body).strip()
        if subs:
            return [(n, d) for n, d in subs]
        return [(n, "") for n in names]
    return []


def usage_command(help_text):
    m = re.search(r"^usage:\s*(.*?)(?:\n\n|\Z)", help_text, re.S | re.M)
    if not m:
        return None
    return " ".join(m.group(1).split())


# ---------- Markdown 만들기 ----------

def build_section(info, svg_path, helps, subs, sub_helps=None, heading="## 사용법"):
    """helps: 최상위 --help 텍스트, subs: [(이름, 설명)], sub_helps: {이름: --help 텍스트}"""
    name = info["name"]
    cmd = next(iter(info["scripts"]), None)
    mod = info["modules"][0] if info["modules"] else None
    out = [START, heading, ""]
    if svg_path:
        out += ["![%s 데모](%s)" % (name, svg_path), ""]
    if info.get("description"):
        out += [info["description"], ""]
    runs = []
    if cmd:
        runs.append("%s --help" % cmd + ("      # pip install . 로 설치한 뒤" if mod else ""))
    if mod:
        runs.append("python -m %s --help" % mod)
    if runs:
        out += ["```bash"] + runs + ["```", ""]
    if subs:
        out += ["### 명령어", "", "| 명령 | 설명 |", "| --- | --- |"]
        for n, d in subs:
            out.append("| `%s` | %s |" % (n, d.replace("|", "\\|") or "-"))
        out.append("")
    if helps:
        out += ["<details>", "<summary>--help 출력</summary>", "", "```text", helps, "```", "", "</details>", ""]
    for n, text in (sub_helps or {}).items():
        out += ["<details>", "<summary><code>%s --help</code></summary>" % n, "", "```text", text, "```", "", "</details>", ""]
    out.append(END)
    return "\n".join(out) + "\n"


def make_section(project_dir, svg_path="docs/demo.svg", with_sub_help=True, heading="## 사용법"):
    info = inspect_project(project_dir)
    if not info["modules"]:
        raise DemoError("`python -m` 으로 실행할 패키지를 찾지 못했어요 (pyproject 의 scripts 또는 __main__.py 필요).")
    mod = info["modules"][0]
    helps = run_help(info["dir"], mod)
    subs = parse_subcommands(helps)
    sub_helps = {}
    if with_sub_help:
        for n, _ in subs[:30]:
            try:
                sub_helps[n] = run_help(info["dir"], mod, [n])
            except DemoError:
                pass
    return build_section(info, svg_path, helps, subs, sub_helps, heading), info


def insert_section(readme_text, section):
    """마커 사이를 바꾸거나, 마커가 없으면 끝에 덧붙인다. 같은 입력으로 여러 번 해도 결과가 같다."""
    nl = "\r\n" if "\r\n" in readme_text else "\n"
    block = section.strip("\r\n").replace("\r\n", "\n").replace("\n", nl)
    if _MARKERS.search(readme_text):
        return _MARKERS.sub(lambda m: block, readme_text, count=1)
    body = readme_text.rstrip()
    return (body + nl + nl if body else "") + block + nl


def write_readme(path, section):
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as f:
            old = f.read()
    else:
        old = ""
    new = insert_section(old, section)
    changed = new != old
    if changed:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)
    return changed
