"""아침 보고서(md): 후보 2개 + 제외 사유 + 경고."""
from __future__ import annotations

from .scrub import REASON_LABELS

ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌", "skip": "⏭️"}


def _lint_lines(lr) -> list[str]:
    if not lr.available:
        return ["- 린트: ⏭️ 생략 (%s)" % (lr.note or "shorts-linter 없음")]
    out = ["- 약속 일치(promise): %s" % ICON.get(lr.promise_status, "")]
    for r in lr.promise:
        out.append("  - %s %s: %s" % (ICON.get(r["status"], ""), r["name"], r["message"]))
    if lr.title_scores:
        out.append("- 제목 점수(titles): " + " / ".join("%d점 %s" % (s, t) for s, t in lr.title_scores))
    if lr.similar_text:
        out.append("- 유사도(similar): %s %s" % ("⚠️" if lr.similar_risk else "✅", lr.similar_text))
    return out


def render(run: dict, cands: list, excluded: list, warnings: list, jarvis_msg: str = "") -> str:
    """run: {date, seed, cards, drafts, llm, linter}. cands: [(Draft, LintReport, Card)]."""
    L = ["# 사연 공장 아침 보고 (%s)" % run["date"], ""]
    L.append("- 시드 %s · 소재 카드 %d장 · 제외 %d장 · 초안 %d편 · 후보 %d개" % (
        run["seed"], run["cards"], len(excluded), run["drafts"], len(cands)))
    L.append("- 대본: %s · 린트: %s" % ("LLM 사연체 사용" if run["llm"] else "템플릿(LLM 없음/미사용)",
                                      "shorts-linter 사용" if run["linter"] else "⏭️ 생략(shorts-linter 없음)"))
    L.append("- 모든 소재는 가명화(실명·지명·연락처 마스킹) 후 사용했고, 원본 데이터는 읽기 전용으로만 열었습니다.")
    L.append("")
    if not cands:
        L += ["## 오늘 올릴 만한 후보가 없습니다", "",
              "소재 카드가 없거나 모두 민감 주제 필터에 걸렸습니다. 소스 경로(--life-nagger 등)를 확인해 주세요.", ""]
    for n, (d, lr, card) in enumerate(cands, 1):
        L += ["## 후보 %d. %s" % (n, d.title), "",
              "- 소재: %s / %s (카드 `%s`)" % (card.source, card.kind, card.id),
              "- 썸네일 문구: %s" % (d.thumbnail_text or "-"),
              "- 제목 후보: " + " | ".join(d.title_candidates),
              "- AI 사용: " + ", ".join("%s=%s" % (k, "예" if v else "아니오") for k, v in d.ai_used.items()),
              "- 파일: `%s.txt`, `%s.meta.json`" % (d.card_id, d.card_id)]
        L += _lint_lines(lr)
        L += ["", "대본:", ""] + ["> " + ln for ln in d.lines] + [""]
        if lr.available and lr.disclose:
            L += ["AI 공개 문구 초안(`disclose`):", ""] + ["    " + ln for ln in lr.disclose.splitlines()] + [""]
    if excluded:
        L += ["## 제외된 소재 (내용은 기록하지 않음)", "", "| 카드 | 종류 | 사유 |", "|---|---|---|"]
        for cid, kind, reasons in excluded:
            L.append("| `%s` | %s | %s |" % (cid, kind, ", ".join(REASON_LABELS.get(r, r) for r in reasons)))
        L.append("")
    notes = list(warnings) + ([jarvis_msg] if jarvis_msg else [])
    if notes:
        L += ["## 안내", ""] + ["- " + w for w in notes] + [""]
    L += ["---", "업로드 전에 영상 파일로 `shortslint check`를 한 번 더 돌리고, 사연이 실제 인물을 특정하지 않는지 직접 확인하세요."]
    return "\n".join(L) + "\n"
