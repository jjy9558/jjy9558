"""Ren'Py: .rpy 소스에서 대사/선택지를 뽑아 ``game/tl/<언어>/`` 번역 파일을 만든다.

.rpyc/.rpa 만 있는 게임은 지원하지 않는다 (README 참고, 언패커는 포함하지 않음).

주의: 대사용 ``translate <언어> <id>:`` 블록의 id 는 Ren'Py 가 만드는 방식
(마지막 label 이름 + 문장 코드의 md5 앞 8자리)을 재현한 것이다. 게임/버전에 따라
id 가 안 맞으면 해당 블록은 그냥 무시되므로 README 의 대처법을 참고할 것.
선택지/캐릭터 이름은 id 가 필요 없는 ``translate <언어> strings:`` 로 만든다.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Dict, Iterator, List, Optional, Set, Tuple

from ..backup import BackupStore
from ..masking import Masker, renpy_masker
from ..units import ApplyReport, ExtractResult, Unit
from .base import ApplyOptions, Engine

# 번역 대상이 될 수 없는 문장의 첫 단어
KEYWORDS = {
    "label", "menu", "jump", "call", "return", "scene", "show", "hide", "with", "play", "queue", "stop",
    "pause", "window", "define", "default", "init", "python", "screen", "transform", "style", "image",
    "if", "elif", "else", "while", "for", "pass", "nvl", "voice", "camera", "translate", "layeredimage",
    "testcase", "rpy", "def", "class", "import", "from", "global", "del", "assert", "try", "except",
    "finally", "raise", "break", "continue", "yield", "print", "on", "use", "at", "contains", "block",
    "parallel", "choice", "time", "function", "repeat", "linear", "ease", "easein", "easeout", "text",
}
# 이 블록 안은 스크립트(대사) 문맥이 아니다
NONSCRIPT = {
    "init", "screen", "python", "transform", "style", "image", "layeredimage", "translate", "testcase",
    "define", "default", "rpy", "show", "scene", "hide", "camera", "window", "on", "contains", "block",
    "parallel", "choice", "time", "function", "repeat", "at", "with", "use", "def", "class",
}

_TOKEN_RE = re.compile(r"-?[A-Za-z_][\w.]*|@")
_FIRSTWORD_RE = re.compile(r"[A-Za-z_]\w*")
_LABEL_RE = re.compile(r"^label\s+([A-Za-z_.][\w.]*)")
_CHAR_RE = re.compile(
    r"^(?:define|default)\s+(?:-?\d+\s+)?([A-Za-z_][\w.]*)\s*=\s*"
    r"(?:[A-Za-z_][\w.]*\.)?(?:Character|DynamicCharacter|ADVCharacter|NVLCharacter)\s*\(\s*(?:_\(\s*)?(?=[\"'])"
)
_CHOICE_REST_RE = re.compile(r"^\s*(?:\(.*?\)\s*)?(?:if\s+.+)?:\s*$")
_MOD_RE = re.compile(r"\s*(nointeract|id\s+\S+|with\s+[\w.]+(?:\([^)]*\))?)")


# ---------------------------------------------------------------------------
# 문자열 리터럴 유틸
# ---------------------------------------------------------------------------
def parse_string(s: str, pos: int) -> Optional[Tuple[str, str, int]]:
    """``s[pos]`` 에서 시작하는 문자열 리터럴. (원문 내용, 따옴표, 끝 위치) 또는 None."""
    if pos >= len(s) or s[pos] not in "\"'":
        return None
    q = s[pos]
    delim = q * 3 if s.startswith(q * 3, pos) else q
    i = start = pos + len(delim)
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s.startswith(delim, i):
            return s[start:i], delim, i + len(delim)
        i += 1
    return None


def escape_bare_quotes(text: str) -> str:
    out: List[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            out.append(text[i:i + 2])
            i += 2
            continue
        out.append('\\"' if c == '"' else c)
        i += 1
    return "".join(out)


def to_double_quoted(raw: str, delim: str) -> str:
    """어떤 따옴표로 쓰였든 큰따옴표 리터럴 안에 그대로 넣을 수 있는 내용으로."""
    return escape_bare_quotes(raw) if delim[0] == "'" or len(delim) == 3 else raw


def sanitize_literal(text: str) -> str:
    """큰따옴표 문자열 안에 안전하게 들어가도록 정리 (번역문용)."""
    t = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    t = escape_bare_quotes(t)
    # 끝의 홀수 개 역슬래시는 닫는 따옴표를 이스케이프해 버린다
    m = re.search(r"\\+$", t)
    if m and len(m.group(0)) % 2 == 1:
        t = t[:-1]
    return t


_ESC = {"n": "\n", '"': '"', "'": "'", "\\": "\\", " ": " ", "t": "\t"}


def decode_literal(raw: str) -> str:
    out: List[str] = []
    i = 0
    prev_space = False
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            n = raw[i + 1]
            out.append(_ESC.get(n, c + n))
            prev_space = False
            i += 2
            continue
        if c == " ":
            if prev_space:
                i += 1
                continue
            prev_space = True
        else:
            prev_space = False
        out.append(c)
        i += 1
    return "".join(out)


def encode_say(text: str) -> str:
    s = text.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
    return re.sub(r"(?<= ) ", "\\\\ ", s)


def get_code(who: Optional[str], attrs: List[str], what_literal: str, mods: List[str]) -> str:
    parts: List[str] = []
    if who:
        parts.append(who)
    parts.extend(attrs)
    parts.append('"' + what_literal + '"')
    parts.extend(mods)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 논리 줄 분리
# ---------------------------------------------------------------------------
def _scan(line: str, q: Optional[str], depth: int) -> Tuple[str, Optional[str], int]:
    """한 물리 줄을 훑어 (주석 제거된 코드, 줄 끝 시점의 열린 문자열, 괄호 깊이)를 돌려준다."""
    j = 0
    n = len(line)
    while j < n:
        c = line[j]
        if q:
            if c == "\\":
                j += 2
                continue
            if line.startswith(q, j):
                j += len(q)
                q = None
                continue
            j += 1
            continue
        if c in "\"'":
            if line.startswith(c * 3, j):
                q = c * 3
                j += 3
            else:
                q = c
                j += 1
            continue
        if c == "#":
            return line[:j], q, depth
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        j += 1
    return line, q, depth


def logical_lines(text: str) -> Iterator[Tuple[int, int, str]]:
    """(시작 줄 번호, 들여쓰기, 주석 제거된 논리 줄). 열린 문자열/괄호는 다음 줄과 이어 붙인다."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    n = len(lines)
    i = 0
    while i < n:
        start = i
        q: Optional[str] = None
        depth = 0
        pieces: List[str] = []
        while True:
            code, q, depth = _scan(lines[i], q, depth)
            pieces.append(code)
            if (q is None and depth <= 0 and not (q is None and code.rstrip().endswith("\\"))) or i + 1 >= n:
                break
            if i - start > 60:  # 짝이 안 맞는 파일 보호
                i = start
                pieces = [_scan(lines[start], None, 0)[0]]
                break
            i += 1
        i += 1
        code = "\n".join(pieces).rstrip()
        if not code.strip():
            continue
        first = lines[start]
        indent = len(first) - len(first.lstrip(" \t"))
        indent += first[:indent].count("\t") * 3
        yield start + 1, indent, code.strip()


# ---------------------------------------------------------------------------
# 문장 해석
# ---------------------------------------------------------------------------
def parse_say(code: str) -> Optional[dict]:
    s = code.strip()
    fw = _FIRSTWORD_RE.match(s)
    if fw and fw.group(0) in KEYWORDS:
        return None
    pos = 0
    words: List[str] = []
    str_who: Optional[str] = None
    main: Optional[Tuple[str, str, int]] = None
    while pos < len(s):
        while pos < len(s) and s[pos].isspace():
            pos += 1
        if pos >= len(s):
            break
        if s[pos] in "\"'":
            st = parse_string(s, pos)
            if st is None:
                return None
            nxt = st[2]
            while nxt < len(s) and s[nxt].isspace():
                nxt += 1
            if not words and str_who is None and nxt < len(s) and s[nxt] in "\"'":
                str_who = '"' + encode_say(decode_literal(to_double_quoted(st[0], st[1]))) + '"'
                pos = nxt
                continue
            main = st
            break
        m = _TOKEN_RE.match(s, pos)
        if not m:
            return None
        words.append(m.group(0))
        pos = m.end()
    if main is None or "\n" in main[0]:
        return None
    rest = s[main[2]:]
    mods: List[str] = []
    while rest.strip():
        m = _MOD_RE.match(rest)
        if not m:
            return None
        mods.append(" ".join(m.group(1).split()))
        rest = rest[m.end():]
    who = str_who or (words[0] if words else None)
    attrs = words[1:] if str_who is None else words
    order = {"nointeract": 0, "id": 1, "with": 2}
    mods.sort(key=lambda x: order.get(x.split()[0], 3))
    return {"who": who, "attrs": attrs, "raw": to_double_quoted(main[0], main[1]), "mods": mods}


def parse_choice(code: str) -> Optional[str]:
    s = code.strip()
    if not s or s[0] not in "\"'" or not s.endswith(":"):
        return None
    st = parse_string(s, 0)
    if st is None or "\n" in st[0]:
        return None
    if not _CHOICE_REST_RE.match(s[st[2]:]):
        return None
    return to_double_quoted(st[0], st[1])


def say_identifier(label: Optional[str], code: str, used: Set[str]) -> str:
    digest = hashlib.md5(code.encode("utf-8") + b"\r\n").hexdigest()[:8]
    base = (label.replace(".", "_") + "_" + digest) if label else "_" + digest
    ident, i = base, 0
    while ident in used:
        i += 1
        ident = f"{base}_{i}"
    used.add(ident)
    return ident


def parse_rpy(text: str, rel: str, used_ids: Set[str]) -> Tuple[List[Unit], Dict[str, str]]:
    """한 파일에서 Unit 목록과 {캐릭터 변수: 이름} 을 뽑는다."""
    units: List[Unit] = []
    chars: Dict[str, str] = {}
    stack: List[Tuple[int, str]] = []
    last_global: Optional[str] = None
    label: Optional[str] = None
    prev = ""
    for lineno, indent, code in logical_lines(text):
        while stack and stack[-1][0] >= indent:
            stack.pop()
        kinds = [k for _, k in stack]
        in_script = "label" in kinds and not any(k in NONSCRIPT for k in kinds)
        fw = _FIRSTWORD_RE.match(code)
        head = fw.group(0) if fw else ""
        opens_block = code.endswith(":")

        cm = _CHAR_RE.match(code)
        if cm:
            st = parse_string(code, cm.end())
            if st and "\n" not in st[0]:
                name = to_double_quoted(st[0], st[1])
                chars[cm.group(1)] = name
                units.append(Unit(file=rel, kind="name", source=name, line=lineno))
        if head == "label":
            lm = _LABEL_RE.match(code)
            if lm:
                nm = lm.group(1)
                if nm.startswith("."):
                    label = (last_global or "") + nm
                else:
                    last_global = nm
                    label = nm
        if in_script:
            parent = kinds[-1] if kinds else ""
            if parent == "menu":
                ch = parse_choice(code)
                if ch is not None:
                    units.append(Unit(file=rel, kind="choice", source=ch, line=lineno, prev=prev))
            if parent != "menu" or not code.endswith(":"):
                say = parse_say(code) if not opens_block else None
                if say is not None:
                    via_strings = parent == "menu"
                    extra = {"who": say["who"], "attrs": say["attrs"], "mods": say["mods"], "label": label}
                    if via_strings:
                        extra["via_strings"] = True
                    else:
                        what_lit = encode_say(decode_literal(say["raw"]))
                        extra["tid"] = say_identifier(label, get_code(say["who"], say["attrs"], what_lit, say["mods"]),
                                                      used_ids)
                    units.append(Unit(file=rel, kind="dialogue", source=say["raw"], speaker=say["who"] or "",
                                      prev=prev, line=lineno, extra=extra))
                    prev = say["raw"]
        if opens_block:
            if head:
                stack.append((indent, head))
            elif code[0] in "\"'":
                stack.append((indent, "choice_item"))
            else:
                stack.append((indent, "other"))
    return units, chars


# ---------------------------------------------------------------------------
class RenPyEngine(Engine):
    name = "renpy"

    def __init__(self, game_dir, game_rel: str) -> None:
        super().__init__(game_dir)
        self.game_rel = game_rel  # "game" 또는 "" (game 폴더 자체를 지정한 경우)
        self.label = "Ren'Py"

    @classmethod
    def detect(cls, game_dir: Path) -> Optional["RenPyEngine"]:
        game_dir = Path(game_dir)
        g = game_dir / "game"
        if g.is_dir() and (any(g.rglob("*.rpy")) or any(g.glob("*.rpyc")) or any(g.glob("*.rpa"))
                           or (game_dir / "renpy").is_dir()):
            return cls(game_dir, "game")
        if any(game_dir.glob("*.rpy")) or any(game_dir.glob("*.rpyc")) or any(game_dir.glob("*.rpa")):
            return cls(game_dir, "")
        return None

    def describe(self) -> str:
        return f"Ren'Py ({self.game_rel or '.'})"

    def masker(self) -> Masker:
        return renpy_masker()

    def finalize(self, source: str, target: str) -> Optional[str]:
        t = sanitize_literal(target)
        return t if t.strip() else None

    def _rel(self, *parts: str) -> str:
        return "/".join(p for p in ((self.game_rel,) + parts) if p)

    def _gdir(self) -> Path:
        return self.game_dir / self.game_rel if self.game_rel else self.game_dir

    def _sources(self) -> List[str]:
        gdir = self._gdir()
        out = []
        for p in sorted(gdir.rglob("*.rpy")):
            rel_in_game = p.relative_to(gdir)
            if rel_in_game.parts[0] in ("tl", "cache", "saves", "renpy") or any(x.startswith(".") for x in rel_in_game.parts):
                continue
            out.append(self._rel(*rel_in_game.parts))
        return out

    def extract(self, store: BackupStore) -> ExtractResult:
        res = ExtractResult()
        gdir = self._gdir()
        files = self._sources()
        if not files:
            n_c = len(list(gdir.rglob("*.rpyc")))
            n_a = len(list(gdir.glob("*.rpa")))
            res.ok = False
            res.notes.append(
                f".rpy 소스가 없습니다 (.rpyc {n_c}개, .rpa {n_a}개). 이 도구는 컴파일된 파일을 풀어 주지 않습니다. "
                "직접 구한 외부 도구(예: unrpa, unrpyc)로 .rpa/.rpyc 에서 .rpy 를 복원한 뒤 다시 실행하세요. "
                "자세한 내용은 README 를 참고하세요."
            )
            return res
        used: Set[str] = set()
        all_chars: Dict[str, str] = {}
        per_file: List[Tuple[str, List[Unit]]] = []
        for rel in files:
            raw = store.original_bytes(rel)
            if raw is None:
                continue
            text = raw.decode("utf-8-sig", errors="replace")
            units, chars = parse_rpy(text, rel, used)
            all_chars.update(chars)
            per_file.append((rel, units))
            res.files += 1
        for _, units in per_file:
            for u in units:
                if u.kind == "dialogue":
                    who = u.speaker
                    u.speaker = (who.strip('"') if who.startswith('"') else all_chars.get(who, "")) if who else ""
                res.units.append(u)
        if (gdir / "tl" / "korean").exists():
            res.notes.append("이미 game/tl/korean 폴더가 있습니다. Ren'Py 가 생성한 기존 번역 파일과 id 가 겹치면 "
                             "'already defined' 오류가 날 수 있으니 확인하세요.")
        return res

    # -- 적용 ---------------------------------------------------------
    def apply(self, store: BackupStore, translations: Dict[str, str], options: ApplyOptions) -> ApplyReport:
        rep = ApplyReport()
        lang = options.language
        ext = self.extract(store)
        rep.notes.extend(ext.notes)
        if not ext.ok:
            return rep
        tl_dir = self._rel("tl", lang)
        by_file: Dict[str, List[Unit]] = {}
        for u in ext.units:
            by_file.setdefault(u.file, []).append(u)
        seen_old: Set[str] = set()
        for rel in sorted(by_file):
            us = by_file[rel]
            sub = PurePosixPath(rel)
            if self.game_rel:
                sub = sub.relative_to(self.game_rel)
            name = str(sub)
            if name == "_lang.rpy":
                name = "_lang_game.rpy"
            out: List[str] = [f"# hpatch 자동 생성 ({rel}) - 개인용. 재배포 금지.\n\n"]
            n_items = 0
            for u in us:
                if u.kind != "dialogue" or u.extra.get("via_strings"):
                    continue
                tr = translations.get(u.source)
                if tr is None:
                    rep.untranslated += self._needs(u.source)
                    continue
                what = encode_say(decode_literal(u.source))
                orig = get_code(u.extra["who"], u.extra["attrs"], what, u.extra["mods"])
                new = get_code(u.extra["who"], u.extra["attrs"], tr, u.extra["mods"])
                out.append(f"translate {lang} {u.extra['tid']}:\n\n    # {orig}\n    {new}\n\n")
                n_items += 1
            strings: List[str] = []
            for u in us:
                is_str = u.kind in ("choice", "name") or (u.kind == "dialogue" and u.extra.get("via_strings"))
                if not is_str:
                    continue
                tr = translations.get(u.source)
                if tr is None:
                    rep.untranslated += self._needs(u.source)
                    continue
                if u.source in seen_old:  # 중복 old 는 Ren'Py 오류이므로 전체에서 한 번만
                    continue
                seen_old.add(u.source)
                strings.append(f"    # {rel}:{u.line}\n    old \"{u.source}\"\n    new \"{tr}\"\n\n")
                n_items += 1
            if strings:
                out.append(f"translate {lang} strings:\n\n" + "".join(strings))
            if n_items:
                path = f"{tl_dir}/{name}"
                store.write(path, "".join(out).rstrip("\n").encode("utf-8") + b"\n")
                rep.written.append(path)
                rep.replaced += n_items
        font_rel = None
        if options.font:
            fp = Path(options.font)
            if fp.is_file() and fp.suffix.lower() in (".ttf", ".otf"):
                font_rel = f"tl/{lang}/{fp.name}"
                store.write(self._rel(font_rel), fp.read_bytes())
                rep.written.append(self._rel(font_rel))
            else:
                rep.notes.append(f"폰트 파일을 찾을 수 없거나 .ttf/.otf 가 아닙니다 (Ren'Py): {options.font}")
        lang_path = f"{tl_dir}/_lang.rpy"
        store.write(lang_path, lang_rpy(lang, font_rel, options.default_language).encode("utf-8"))
        rep.written.append(lang_path)
        return rep

    def _needs(self, source: str) -> int:
        m = self.masker()
        return 1 if m.needs_translation(m.mask(source)) else 0


def lang_rpy(lang: str, font_rel: Optional[str], default_language: bool = True) -> str:
    lines = [
        "# hpatch: 언어 전환 설정 (개인용, 필요 없으면 이 파일을 지워도 됩니다)",
        "#",
        "# 이 폴더의 번역은 언어가 '%s' 일 때만 적용됩니다." % lang,
        "# - 아래 설정은 처음부터 이 언어로 시작하게 합니다 (config.default_language).",
        "#   설정 화면에서 바꾸려면 버튼에 Language(\"%s\") 액션을 쓰거나," % lang,
        "#   게임 안 콘솔(Shift+O, 개발자 모드)에서 renpy.change_language(\"%s\") 를 실행하세요." % lang,
        "",
    ]
    if default_language:
        lines += ["init -10 python:", "    config.default_language = \"%s\"" % lang, ""]
    else:
        lines += ["# (default_language 설정 생략됨: --no-default-lang)", ""]
    if font_rel:
        lines += [
            "# 한글 폰트 (--font 로 복사됨). DejaVu 등 기본 폰트는 한글 글리프가 없어 네모(두부)로 보입니다.",
            "translate %s python:" % lang,
            "    _hpatch_font = \"%s\"" % font_rel,
            "    for _n in (\"text_font\", \"name_text_font\", \"interface_text_font\", \"button_text_font\",",
            "               \"choice_button_text_font\", \"system_font\"):",
            "        if hasattr(gui, _n):",
            "            setattr(gui, _n, _hpatch_font)",
            "",
        ]
    else:
        lines += [
            "# 한글이 네모로 보이면 한글 폰트(.ttf)를 game/ 안에 넣고 아래 주석을 풀어 경로를 맞추세요.",
            "# translate %s python:" % lang,
            "#     gui.text_font = gui.name_text_font = gui.interface_text_font = \"NanumGothic.ttf\"",
            "",
        ]
    return "\n".join(lines)
