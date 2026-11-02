"""판매글 린트: 금칙어·과장 표현·전화번호/계좌 등 개인정보·주소/송장 노출·제목 길이.

개인정보 검출은 jjkit.redact 를 쓴다. 출력에는 원문 대신 가려진 문장을 보여 준다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from html.parser import HTMLParser

from ._jjkit import redact as rd

SIDO = r"(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)"
ADDR_ROAD = re.compile(r"(?:" + SIDO + r"[가-힣]*\s*)?[가-힣]{1,8}[시군구]\s*[가-힣0-9]{1,12}[로길]\s*\d+(?:-\d+)?")
ADDR_UNIT = re.compile(r"\d{1,4}\s*동\s*\d{1,4}\s*호")
INVOICE = re.compile(r"(?:운송장|송장|택배\s*번호|배송\s*조회)[^\n\d]{0,12}(\d{10,14})(?!\d)")
REPEAT_PUNCT = re.compile(r"[!?]{3,}")


@dataclass
class Finding:
    severity: str   # error | warn
    rule: str
    message: str
    where: str = ""
    snippet: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _snippet(line: str, start: int, end: int, width: int = 40) -> str:
    a = max(0, start - 12)
    return line[a:min(len(line), end + width - 12)].strip()


def lint_text(text: str, rules: dict, where: str = "") -> list[Finding]:
    out: list[Finding] = []
    lines = (text or "").splitlines() or [""]
    pii = set(rules.get("pii_rules", []))
    disable = set(rd.RULE_LABELS) - pii
    red = rd.Redactor(use_os_user=False, disable=disable)

    for kind, sev in (("banned", "error"), ("exaggeration", "warn")):
        for word, why in rules.get(kind, {}).items():
            pat = re.compile(re.escape(word), re.I)
            for ln, line in enumerate(lines, 1):
                for m in pat.finditer(line):
                    out.append(Finding(sev, "금칙어" if kind == "banned" else "과장 표현",
                                       "'%s': %s" % (word, why), "%s:%d" % (where, ln) if where else str(ln),
                                       _snippet(line, m.start(), m.end())))

    for ln, line in enumerate(lines, 1):
        loc = "%s:%d" % (where, ln) if where else str(ln)
        red.counts.clear()
        masked = red.redact(line)
        for rule, n in sorted(red.counts.items()):
            out.append(Finding("error", "개인정보", "%s %d건 (%s)" % (rd.RULE_LABELS.get(rule, rule), n, rule),
                               loc, masked.strip()[:60]))
        for pat, label in ((ADDR_ROAD, "도로명 주소로 보이는 표현"), (ADDR_UNIT, "동/호수로 보이는 표현")):
            m = pat.search(line)
            if m:
                out.append(Finding("error", "주소", label + ": 동네 이름 정도만 적으세요", loc,
                                   re.sub(r"\d", "*", _snippet(line, m.start(), m.end()))))
        m = INVOICE.search(line)
        if m:
            out.append(Finding("error", "송장", "송장/운송장 번호로 보이는 숫자", loc,
                               line[:m.start(1)].strip()[-20:] + "*" * len(m.group(1))))
        if REPEAT_PUNCT.search(line):
            out.append(Finding("warn", "과장 표현", "느낌표/물음표 반복", loc, line.strip()[:40]))
    return out


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip += 1
        for k, v in attrs:
            if k in ("alt", "title") and v:
                self.parts.append(v)

    def handle_endtag(self, tag):
        if tag in ("style", "script") and self._skip:
            self._skip -= 1
        if tag in ("p", "li", "tr", "h1", "h2", "h3", "div", "section", "figcaption"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    p.close()
    return re.sub(r"\n{2,}", "\n", "".join(p.parts))


def lint_draft(draft: dict, rules: dict) -> list[Finding]:
    where = draft["market"]
    out = lint_text(draft["title"] + "\n" + draft["description"], rules, where)
    if len(draft["title"]) > draft["title_max"]:
        out.append(Finding("error", "제목 길이", "제목 %d자 > 제한 %d자(근사치)" % (len(draft["title"]), draft["title_max"]),
                           where, draft["title"]))
    todo = draft.get("todo") or []
    if todo:
        out.append(Finding("warn", "미완성", "직접 채울 곳 %d개: %s" % (len(todo), ", ".join(todo[:4])), where))
    return out


def lint_item(root, data: dict, rules: dict) -> list[Finding]:
    from pathlib import Path
    out: list[Finding] = []
    drafts = data.get("drafts") or {}
    for market in sorted(drafts):
        out += lint_draft(drafts[market], rules)
    page = Path(root) / "detail.html"
    if page.is_file():
        out += lint_text(html_to_text(page.read_text(encoding="utf-8")), rules, "detail.html")
    # 사진 캡션과 사용자 입력도 같이 본다 (상세페이지/초안에 들어갈 문장)
    parts = [data.get("name", ""), data.get("spec", ""), data.get("condition", ""), data.get("notes", ""),
             data.get("trade", "")] + list(data.get("defects", [])) + list(data.get("included", []))
    parts += [p.get("caption", "") for p in data.get("photos", [])]
    out += lint_text("\n".join(x for x in parts if x), rules, "item")
    return out


def render_findings(findings: list[Finding]) -> list[str]:
    if not findings:
        return ["린트: 문제를 찾지 못했습니다 (모든 문제를 잡는다는 뜻은 아닙니다)"]
    lines = ["린트: 오류 %d, 주의 %d" % (sum(f.severity == "error" for f in findings),
                                       sum(f.severity == "warn" for f in findings))]
    for f in findings:
        tag = "[오류]" if f.severity == "error" else "[주의]"
        s = " ".join(x for x in (tag, f.where, f.rule) if x) + " - " + f.message
        if f.snippet:
            s += "  ->  \"%s\"" % f.snippet
        lines.append(s)
    return lines
