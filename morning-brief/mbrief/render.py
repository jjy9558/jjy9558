"""사람이 읽는 출력: md 문서와 어댑터 상태표."""
from __future__ import annotations

import datetime as dt

from ._jjkit import textwidth
from .speech import WEEKDAYS

MARK = {"ok": "✅", "disabled": "➖", "skipped": "⏸", "missing": "⚠", "error": "❌"}
LABEL = {"ok": "정상", "disabled": "꺼짐", "skipped": "건너뜀", "missing": "연결 안 됨", "error": "오류"}


def source_rows(sources: dict) -> list[list]:
    return [[name, MARK[st["status"]] + " " + LABEL[st["status"]], st["items"], st["note"]]
            for name, st in sources.items()]


def sources_table(sources: dict) -> list[str]:
    return textwidth.table(source_rows(sources), header=["어댑터", "상태", "항목", "비고"],
                           max_width={3: 48})


def render_md(brief: dict) -> str:
    d = dt.date.fromisoformat(brief["date"])
    L = ["# 아침 브리핑 %s (%s)" % (brief["date"], WEEKDAYS[d.weekday()]), ""]
    if brief.get("quiet_day"):
        L += ["_조용한 날: 게임·커뮤니티 위주로 가중치를 바꿨어요._", ""]
    L += ["> " + brief["speak_text"], "",
          "낭독 %d자 / 예산 %d자 · 생략 %d건 · 반려 문구: %s" % (len(brief["speak_text"]), brief["speak_budget_chars"],
                                                         brief["dropped"], brief["reject_text"]), ""]
    if not brief["sections"]:
        L += ["_오늘 챙길 항목이 없어요._", ""]
    for sec in brief["sections"]:
        L += ["## " + sec["title"], ""]
        for it in sec["items"]:
            tag = "낭독" if it["spoken"] else "화면"
            due = " · 기한 " + it["due"] if it["due"] else ""
            L.append("- **%s** `우선 %g · %s%s`" % (it["text"], it["priority"], tag, due))
            for ln in (it["detail"] or "").splitlines():
                if ln.strip():
                    L.append("  " + ln)
        L.append("")
    L += ["## 어댑터 상태", ""]
    L += ["| 어댑터 | 상태 | 항목 | 비고 |", "|---|---|---|---|"]
    for r in source_rows(brief["sources"]):
        L.append("| %s | %s | %s | %s |" % (r[0], r[1], r[2], str(r[3]).replace("|", "/")))
    return "\n".join(L).rstrip() + "\n"
