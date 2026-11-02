"""한 장짜리 종합 리포트(Markdown). 주어진 입력만 해당 절을 만들고, 없는 도구는 건너뛴다고 적는다."""
from datetime import date
from pathlib import Path

from . import autopsy, comments, csvreport, fatigue, linter, vote
from ._jjkit import textwidth


def _block(lines: list[str]) -> list[str]:
    return ["```"] + lines + ["```"]


def build_report(data: Path, *, poll: str | None = None, comments_file=None, csv_file=None, types_file=None,
                 meta_file=None, today: date | None = None, k: int | None = None, seed: int = 7,
                 llm_client=None, timeout: float = 60.0) -> str:
    out = ["# 쇼츠 피드백 리포트 (%s)" % (today or date.today()).isoformat(), ""]
    if poll is not None:
        out.append("## 썸네일 투표")
        try:
            p = vote.load_poll(data, None if poll in ("", "latest") else poll)
            res = vote.tally(p, vote.read_votes(vote.polls_dir(data) / p["id"] / "votes.jsonl"))
            out += _block(vote.format_result(res).splitlines())
        except ValueError as e:
            out.append("건너뜀: %s" % e)
        out.append("")
    if comments_file:
        out.append("## 댓글에서 뽑은 다음 사연 주제")
        try:
            res = comments.cluster_comments(comments.load_comments(comments_file), k=k, seed=seed)
            if llm_client is not None:
                comments.apply_names(res, comments.llm_names(llm_client, res["clusters"]))
            out += _block(comments.format_top(res).splitlines())
        except (OSError, ValueError) as e:
            out.append("건너뜀: %s" % e)
        out.append("")
    if csv_file:
        out.append("## 스튜디오 CSV 요약")
        try:
            out += _block(csvreport.format_text(csvreport.build(csv_file, types_file)).splitlines())
        except (OSError, ValueError) as e:
            out.append("건너뜀: %s" % e)
        out.append("")
    out.append("## 부검 노트에서 본 반복 원인")
    out += _block(autopsy.format_patterns(data).splitlines())
    out += ["", autopsy.format_checklist(data), ""]
    days = fatigue.load(data)
    out.append("## 제작 피로")
    out.append(fatigue.evaluate(days, today)["line"] if days else "제작 기록이 없습니다 (`fatigue add`로 기록)")
    out.append("")
    if meta_file:
        out.append("## shorts-linter 약속 일치(titles)")
        r = linter.run_promise(meta_file, timeout=timeout)
        out += _block(r["lines"]) if r["lines"] else []
        if r["note"]:
            out.append(r["note"])
        out.append("")
    return "\n".join(out).rstrip() + "\n"
