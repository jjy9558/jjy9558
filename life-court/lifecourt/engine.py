"""판결문 공통 엔진: 시드 고정 선택, 판결문 구조, 글자 폭 맞춘 텍스트/마크다운/HTML 출력."""
from __future__ import annotations

import hashlib
import html
import random
import re
from dataclasses import dataclass, field

from ._jjkit.textwidth import pad, width

DISCLAIMER = "※ 이 판결문은 오락용입니다. 법적 효력은 없고, 항소는 기각됩니다."
BOX_WIDTH = 60


class Picker:
    """같은 시드와 키면 항상 같은 선택. 키마다 독립 난수라 문구가 늘어도 다른 선택이 안 흔들린다."""

    def __init__(self, seed: str):
        self.seed = str(seed)

    def rng(self, key: str) -> random.Random:
        h = hashlib.sha256((self.seed + "\x00" + key).encode("utf-8")).digest()
        return random.Random(int.from_bytes(h[:8], "big"))

    def pick(self, key: str, options: list):
        if not options:
            raise ValueError("선택지가 비었습니다")
        return self.rng(key).choice(options)

    def sample(self, key: str, options: list, k: int) -> list:
        k = min(k, len(options))
        return self.rng(key).sample(list(options), k)

    def number(self, key: str, lo: int, hi: int) -> int:
        return self.rng(key).randint(lo, hi)


_JOSA = re.compile(r"(\S)(['\"」』)]*)(을\(를\)|은\(는\)|이\(가\)|와\(과\))")
_JOSA_FORMS = {"을(를)": ("을", "를"), "은(는)": ("은", "는"), "이(가)": ("이", "가"), "와(과)": ("과", "와")}


def fix_josa(text: str) -> str:
    """'치킨을(를)' 같은 표기를 받침에 맞게 '치킨을'로 바꾼다. 한글이 아니면 그대로 둔다."""
    def sub(m: re.Match) -> str:
        ch, mid, tag = m.group(1), m.group(2), m.group(3)
        if not ("가" <= ch <= "힣"):
            return m.group(0)
        with_final, without = _JOSA_FORMS[tag]
        return ch + mid + (with_final if (ord(ch) - 0xAC00) % 28 else without)
    return _JOSA.sub(sub, text)


@dataclass
class Judgment:
    kind: str                      # night-snack / late / bet / toast
    title: str
    case_no: str
    date: str
    parties: list[tuple[str, str]] = field(default_factory=list)
    sections: list[tuple[str, list[str]]] = field(default_factory=list)
    ruling: str = ""               # 낭독용 한두 문장
    footer: str = DISCLAIMER
    llm_used: bool = False

    def __post_init__(self):
        self.parties = [(k, fix_josa(v)) for k, v in self.parties]
        self.sections = [(h, [fix_josa(x) for x in ls]) for h, ls in self.sections]
        self.ruling = fix_josa(self.ruling)

    def all_lines(self) -> list[str]:
        out = [self.title, self.case_no]
        out += [v for _, v in self.parties]
        for _, ls in self.sections:
            out += ls
        return out


# -- 텍스트 -------------------------------------------------------------------
def wrap(text: str, w: int, indent: str = "") -> list[str]:
    """표시 폭 기준 줄바꿈. 공백에서 먼저 끊고, 한 단어가 길면 글자 단위로 끊는다."""
    lines: list[str] = []
    cur = ""
    first = True

    def room() -> int:
        return w - width(indent if not first else "")

    for word in text.split(" "):
        cand = word if not cur else cur + " " + word
        if width(cand) <= room():
            cur = cand
            continue
        if cur:
            lines.append(cur)
            first = False
            cur = ""
        while width(word) > room():
            part = ""
            for ch in word:
                if width(part + ch) > room():
                    break
                part += ch
            lines.append(part)
            first = False
            word = word[len(part):]
        cur = word
    lines.append(cur)
    out = []
    for i, ln in enumerate(lines):
        out.append(ln if i == 0 else indent + ln)
    return out


def center(s: str, w: int) -> str:
    gap = max(0, w - width(s))
    left = gap // 2
    return " " * left + s + " " * (gap - left)


def render_text(j: Judgment, box_width: int = BOX_WIDTH) -> str:
    inner = box_width - 4
    body: list[str] = []
    body.append(center(j.title, inner))
    body.append(center(j.case_no, inner))
    body.append("-" * inner)
    if j.parties:
        lw = max(width(k) for k, _ in j.parties)
        for k, v in j.parties:
            for i, ln in enumerate(wrap(v, inner - lw - 2, " " * 0)):
                body.append(pad(k if i == 0 else "", lw) + "  " + ln)
        body.append("")
    for heading, lines in j.sections:
        body.append("[" + heading + "]")
        for ln in lines:
            if ln == "":
                body.append("")
                continue
            ind = "  " if ln.startswith("- ") else ""
            body.extend(wrap(ln, inner, ind))
        body.append("")
    body.append("-" * inner)
    body.extend(wrap(j.footer, inner))
    top = "+" + "-" * (box_width - 2) + "+"
    rows = ["| " + pad(ln, inner) + " |" for ln in body]
    return "\n".join([top, *rows, top]) + "\n"


# -- 마크다운 -------------------------------------------------------------------
def render_md(j: Judgment) -> str:
    out = [f"# {j.title}", "", f"**{j.case_no}** · {j.date}", ""]
    if j.parties:
        out += ["| 구분 | 내용 |", "|---|---|"]
        out += [f"| {k} | {v.replace('|', '/')} |" for k, v in j.parties]
        out.append("")
    for heading, lines in j.sections:
        out += [f"## {heading}", ""]
        prev_item = False
        for ln in lines:
            item = ln.startswith("- ")
            if prev_item and not item:
                out.append("")
            out.append(ln)
            if not item:
                out.append("")
            prev_item = item
        if prev_item:
            out.append("")
    out += ["---", "", f"*{j.footer}*", ""]
    return "\n".join(out)


# -- HTML (외부 자원 없음, 인쇄용) --------------------------------------------------
_CSS = """
:root{color-scheme:light}
body{margin:0;background:#efe9dc;font-family:"Noto Serif KR","Nanum Myeongjo","Batang","AppleMyungjo",serif;color:#1d1a16}
.paper{max-width:720px;margin:24px auto;background:#fffdf7;border:3px double #3a3328;padding:36px 44px;position:relative}
h1{text-align:center;letter-spacing:.3em;font-size:28px;margin:0 0 6px}
.case{text-align:center;color:#5a5144;margin-bottom:18px}
table{border-collapse:collapse;width:100%;margin:12px 0}
th,td{border:1px solid #8a8070;padding:6px 10px;text-align:left;vertical-align:top}
th{background:#f1ead9;width:7em;white-space:nowrap}
h2{font-size:18px;border-bottom:2px solid #3a3328;padding-bottom:3px;margin:22px 0 8px}
p{line-height:1.8;margin:6px 0}
ul{margin:6px 0;padding-left:1.4em;line-height:1.8}
.ruling{font-weight:700;font-size:19px;background:#f7efd2;padding:8px 12px;border-left:5px solid #a3271f}
.foot{margin-top:26px;border-top:1px solid #8a8070;padding-top:8px;font-size:13px;color:#5a5144}
.stamp{position:absolute;right:34px;top:30px;width:64px;height:64px;border:3px solid #a3271f;color:#a3271f;border-radius:50%;
display:flex;align-items:center;justify-content:center;font-weight:700;transform:rotate(-14deg);font-size:18px}
@media print{body{background:#fff}.paper{margin:0;border-width:2px;max-width:none;box-shadow:none}}
@media (max-width:600px){.paper{margin:0;padding:22px 16px}}
"""


def render_html(j: Judgment) -> str:
    e = html.escape
    parts = [f"<h1>{e(j.title)}</h1>", f'<div class="case">{e(j.case_no)} · {e(j.date)}</div>']
    if j.parties:
        parts.append("<table>")
        parts += [f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in j.parties]
        parts.append("</table>")
    for heading, lines in j.sections:
        parts.append(f"<h2>{e(heading)}</h2>")
        items = [ln[2:] for ln in lines if ln.startswith("- ")]
        if heading == "주문" and not items:
            parts += [f'<p class="ruling">{e(ln)}</p>' for ln in lines if ln]
            continue
        buf: list[str] = []
        for ln in lines:
            if ln.startswith("- "):
                buf.append(f"<li>{e(ln[2:])}</li>")
            else:
                if buf:
                    parts.append("<ul>" + "".join(buf) + "</ul>")
                    buf = []
                if ln:
                    parts.append(f"<p>{e(ln)}</p>")
        if buf:
            parts.append("<ul>" + "".join(buf) + "</ul>")
    parts.append(f'<div class="foot">{e(j.footer)}</div>')
    return ("<!doctype html>\n<html lang=\"ko\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{e(j.title)}</title><style>{_CSS}</style></head><body>"
            f'<div class="paper"><div class="stamp">선고</div>' + "\n".join(parts) + "</div></body></html>\n")
