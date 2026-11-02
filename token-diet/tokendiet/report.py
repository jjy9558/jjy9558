"""리포트 모델: 같은 구조에서 마크다운과 자기완결 HTML을 만든다. 출력 직전에 비밀값/사용자명을 마스킹한다."""
import html
from dataclasses import dataclass, field

from ._jjkit import redact as _redact
from ._jjkit import textwidth

PROMPT_LIMIT = 120


def clip(text: str, limit: int = PROMPT_LIMIT) -> str:
    """원문 프롬프트는 한 줄로 줄여 limit자까지만."""
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def est_tokens(chars: int) -> int:
    """대략적 토큰 추정: 문자 수 / 4 (추정치)."""
    return (max(chars, 0) + 3) // 4


@dataclass
class Section:
    title: str
    notes: list = field(default_factory=list)
    header: list = field(default_factory=list)
    rows: list = field(default_factory=list)


@dataclass
class Report:
    title: str
    intro: list = field(default_factory=list)
    sections: list = field(default_factory=list)

    def add(self, title, notes=(), header=(), rows=()):
        s = Section(title, list(notes), list(header), [list(r) for r in rows])
        self.sections.append(s)
        return s


def _mask(text: str) -> str:
    return _redact.redact(text)


def to_markdown(rep: Report) -> str:
    out = [f"# {rep.title}", ""]
    out += [f"> {n}" for n in rep.intro] + ([""] if rep.intro else [])
    for s in rep.sections:
        out += [f"## {s.title}", ""]
        out += [f"- {n}" for n in s.notes]
        if s.notes:
            out.append("")
        if s.rows:
            out += textwidth.md_table(s.rows, s.header)
            out.append("")
    return _mask("\n".join(out).rstrip() + "\n")


_CSS = """:root{--bg:#fff;--fg:#1d2330;--mut:#5b6475;--line:#d9dde6;--acc:#2456c7;--card:#f5f7fb}
@media (prefers-color-scheme:dark){:root{--bg:#14171f;--fg:#e6e9f0;--mut:#9aa3b5;--line:#2b3140;--acc:#7ea6ff;--card:#1c2130}}
body{background:var(--bg);color:var(--fg);font:15px/1.6 system-ui,'Malgun Gothic',sans-serif;max-width:980px;margin:0 auto;padding:24px 16px}
h1{font-size:24px}h2{font-size:18px;margin-top:32px;border-bottom:1px solid var(--line);padding-bottom:4px;color:var(--acc)}
.note{color:var(--mut);margin:4px 0}.intro{background:var(--card);padding:10px 14px;border-radius:8px;color:var(--mut)}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13.5px}
th,td{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}th{background:var(--card)}"""


def to_html(rep: Report) -> str:
    e = html.escape
    p = [f"<h1>{e(rep.title)}</h1>"]
    p += [f'<div class="intro">{e(n)}</div>' for n in rep.intro]
    for s in rep.sections:
        p.append(f"<h2>{e(s.title)}</h2>")
        p += [f'<p class="note">{e(n)}</p>' for n in s.notes]
        if s.rows:
            p.append('<div class="wrap"><table><tr>' + "".join(f"<th>{e(str(h))}</th>" for h in s.header) + "</tr>")
            for r in s.rows:
                p.append("<tr>" + "".join(f"<td>{e(str(c))}</td>" for c in r) + "</tr>")
            p.append("</table></div>")
    doc = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           f"<title>{e(rep.title)}</title><style>{_CSS}</style></head><body>" + "\n".join(p) + "</body></html>\n")
    return _mask(doc)


def fmt_tok(n) -> str:
    n = int(n)
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)
