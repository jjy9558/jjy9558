"""실패 편 부검 노트: 조회가 낮은 편의 원인 체크리스트를 누적하고 다음 기획에 넣는다.

- 편(영상 ID 또는 제목)마다 한 건. 같은 편을 다시 ``add``하면 그 건을 **덮어쓴다**(멱등: 같은 입력이면 파일이 같다).
- 원인은 아래 표준 항목 이름(예: ``hook_weak``)이나 직접 적은 문구. 항목별 확인 질문이 ``checklist``로 나온다.
- 메모와 제목은 개인정보(전화/이메일 등)를 마스킹해서 저장한다.
"""
import re
from datetime import date
from pathlib import Path

from ._jjkit import redact
from .store import read_json, write_json

# 이름: (한 줄 설명, 다음 편 기획 전 확인 질문)
CAUSES = {
    "hook_weak": ("첫 3초 훅이 약함", "첫 3초 안에 질문·반전·결과 중 하나를 보여 주는가?"),
    "too_long": ("길이가 너무 김", "핵심 전개가 끝나는 지점은 어디인가? 늘어지는 구간을 잘라냈는가?"),
    "title_mismatch": ("제목·썸네일과 내용 불일치", "제목이 약속한 장면·반전이 영상 안에 실제로 나오는가? (shorts-linter titles로 점검)"),
    "thumbnail_unclear": ("썸네일 가독성 부족", "작은 화면에서 3단어 이하로 읽히고 대비가 충분한가? 후보를 투표로 비교했는가?"),
    "topic_stale": ("주제·전개 반복", "최근 5편과 같은 소재·같은 전개가 아닌가? (shorts-linter similar로 점검)"),
    "pacing_slow": ("전개가 느림", "장면이 몇 초마다 바뀌는가? 같은 정보를 두 번 말하지 않는가?"),
    "audio_issue": ("음질·TTS 발음 문제", "고유명사·숫자 발음을 들어 보고 교정 사전에 반영했는가?"),
    "ending_weak": ("결말이 약함", "마지막 문장이 궁금증을 풀거나 다음 편으로 이어지는가?"),
    "timing": ("업로드 시간·요일", "시청자가 많은 시간대에 올렸는가? 직전 편과 간격이 너무 좁지 않은가?"),
    "policy": ("정책·공개 표시 문제", "AI 사용 공개 표시와 출처 기록을 확인했는가? 재활용 콘텐츠로 보일 요소는 없는가?"),
}
FILE = "autopsy.json"


def _norm_cause(c: str) -> str:
    c = re.sub(r"\s+", " ", c.strip())
    key = c.lower().replace("-", "_")
    return key if key in CAUSES else c[:40]


def parse_causes(text: str) -> list[str]:
    out = []
    for c in re.split(r"[,;\n]", text or ""):
        c = _norm_cause(c)
        if c and c not in out:
            out.append(c)
    return out


def _clean(s: str, limit: int) -> str:
    return redact.redact(re.sub(r"\s+", " ", s or "").strip())[:limit]


def load(data: Path) -> dict:
    d = read_json(Path(data) / FILE, {"entries": {}})
    d.setdefault("entries", {})
    return d


def add(data: Path, video: str, *, title: str = "", views: int | None = None, causes: str = "", note: str = "",
        when: str | None = None) -> tuple[str, dict]:
    """(``new`` 또는 ``updated``, 저장된 항목). 이미 있는 항목은 날짜를 유지한다(``when``을 주면 바꿈)."""
    video = video.strip()
    if not video:
        raise ValueError("영상 ID나 제목이 필요합니다")
    if views is not None and views < 0:
        raise ValueError("조회수는 0 이상이어야 합니다")
    if when:
        date.fromisoformat(when)
    d = load(data)
    key = _clean(video, 80)
    old = d["entries"].get(key)
    entry = {"video": key, "title": _clean(title, 100), "views": views, "causes": parse_causes(causes),
             "note": _clean(note, 300), "date": when or (old or {}).get("date") or date.today().isoformat()}
    d["entries"][key] = entry
    write_json(Path(data) / FILE, d)
    return ("updated" if old else "new"), entry


def entries(data: Path) -> list[dict]:
    return sorted(load(data)["entries"].values(), key=lambda e: (e["date"], e["video"]))


def patterns(data: Path, top: int = 3) -> dict:
    es = entries(data)
    counts: dict[str, int] = {}
    for e in es:
        for c in e["causes"]:
            counts[c] = counts.get(c, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"episodes": len(es), "top": [{"cause": c, "label": CAUSES.get(c, (c, ""))[0], "count": n,
                                          "question": CAUSES.get(c, (c, "이 항목을 다음 편에서 확인했는가?"))[1]}
                                         for c, n in ranked[:top]], "all": ranked}


def format_list(data: Path) -> str:
    from ._jjkit import textwidth
    es = entries(data)
    if not es:
        return "부검 노트가 없습니다. `autopsy add <영상> --views N --causes hook_weak,too_long`로 추가하세요"
    rows = [(e["date"], e["video"], "-" if e["views"] is None else e["views"],
             ",".join(e["causes"]) or "-", e["note"]) for e in es]
    return "\n".join(textwidth.table(rows, header=("날짜", "영상", "조회", "원인", "메모"), right_cols=(2,),
                                     max_width={1: 24, 4: 30}))


def format_patterns(data: Path, top: int = 3) -> str:
    p = patterns(data, top)
    if not p["episodes"]:
        return "부검 노트가 없습니다"
    if not p["top"]:
        return "부검 %d건, 기록된 원인이 없습니다" % p["episodes"]
    lines = ["부검 %d건 중 상위 원인 %d개" % (p["episodes"], len(p["top"]))]
    for i, t in enumerate(p["top"], 1):
        lines.append("%d. %s (%d/%d건)" % (i, t["label"], t["count"], p["episodes"]))
    return "\n".join(lines)


def format_checklist(data: Path, top: int = 3) -> str:
    """다음 편 기획 문서에 붙여 넣을 체크리스트."""
    p = patterns(data, top)
    if not p["top"]:
        return "(부검 기록이 쌓이면 여기에 다음 편 확인 항목이 나옵니다)"
    lines = ["## 다음 편 기획 전 확인 (부검 %d건 기반)" % p["episodes"]]
    for t in p["top"]:
        lines.append("- [ ] %s: %s (%d/%d건)" % (t["label"], t["question"], t["count"], p["episodes"]))
    return "\n".join(lines)


def format_causes() -> str:
    return "\n".join("%-18s %s" % (k, v[0]) for k, v in CAUSES.items())
