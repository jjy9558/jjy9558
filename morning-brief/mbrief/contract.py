"""출력 계약 검사. 폴더 사이 결합은 이 두 파일뿐이다.

``out/brief-latest.json`` (읽는 쪽: s21-sidekick morning_gate)::

    {"version": 1, "date": "YYYY-MM-DD", "speak_text": "<=300자", "reject_text": "한 줄",
     "sections": [{"id", "title", "items": [{"source", "kind", "text", "detail", "priority", "due", "spoken"}]}],
     "sources": {"<어댑터>": {"status": "ok|disabled|skipped|missing|error", "ok": bool, "items": int, "note": str}},
     "quiet_day": bool, "speak_budget_chars": int, "dropped": int}

``out/cards-latest.json`` (읽는 쪽: phone-home 사장님 책상)::

    [{"kind": str, "title": str, "detail": str, "due": "YYYY-MM-DD" | "YYYY-MM-DDTHH:MM+09:00" | null,
      "actions": [str]}]      # due 오름차순, due 없는 카드는 맨 뒤

읽는 쪽은 모르는 키를 무시해야 하고, ``date`` 가 오늘이 아니면 오래된 파일로 본다.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

MAX_SPEAK = 300
STATUSES = ("ok", "disabled", "skipped", "missing", "error")
CARD_KEYS = ("kind", "title", "detail", "due", "actions")


def _valid_due(v) -> bool:
    if v is None:
        return True
    if not isinstance(v, str):
        return False
    try:
        if "T" in v:
            dt.datetime.fromisoformat(v)
        else:
            dt.date.fromisoformat(v)
    except ValueError:
        return False
    return True


def validate_brief(obj) -> list[str]:
    errs: list[str] = []
    if not isinstance(obj, dict):
        return ["최상위가 객체가 아님"]
    try:
        dt.date.fromisoformat(str(obj.get("date")))
    except ValueError:
        errs.append("date 가 YYYY-MM-DD 가 아님")
    speak = obj.get("speak_text")
    if not isinstance(speak, str) or not speak.strip():
        errs.append("speak_text 가 비어 있음")
    elif len(speak) > MAX_SPEAK:
        errs.append("speak_text 가 %d자 (상한 %d)" % (len(speak), MAX_SPEAK))
    rej = obj.get("reject_text")
    if not isinstance(rej, str) or not rej.strip() or "\n" in rej:
        errs.append("reject_text 는 한 줄 문자열이어야 함")
    secs = obj.get("sections")
    if not isinstance(secs, list):
        errs.append("sections 가 배열이 아님")
    else:
        for i, s in enumerate(secs):
            if not isinstance(s, dict) or not isinstance(s.get("title"), str) or not isinstance(s.get("items"), list):
                errs.append("sections[%d] 형식 오류" % i)
                continue
            for j, it in enumerate(s["items"]):
                if not isinstance(it, dict) or not isinstance(it.get("text"), str) \
                        or not isinstance(it.get("priority"), (int, float)) or not isinstance(it.get("spoken"), bool):
                    errs.append("sections[%d].items[%d] 형식 오류" % (i, j))
    src = obj.get("sources")
    if not isinstance(src, dict):
        errs.append("sources 가 객체가 아님")
    else:
        for name, st in src.items():
            if not isinstance(st, dict) or st.get("status") not in STATUSES or not isinstance(st.get("ok"), bool):
                errs.append("sources.%s 형식 오류" % name)
    return errs


def validate_cards(obj) -> list[str]:
    if not isinstance(obj, list):
        return ["최상위가 배열이 아님"]
    errs: list[str] = []
    for i, c in enumerate(obj):
        if not isinstance(c, dict) or set(c) != set(CARD_KEYS):
            errs.append("cards[%d] 키가 %s 와 다름" % (i, "/".join(CARD_KEYS)))
            continue
        if not isinstance(c["kind"], str) or not c["kind"] or not isinstance(c["title"], str) or not c["title"]:
            errs.append("cards[%d] kind/title 이 비었음" % i)
        if not isinstance(c["detail"], str):
            errs.append("cards[%d] detail 이 문자열이 아님" % i)
        if not _valid_due(c["due"]):
            errs.append("cards[%d] due 형식 오류: %r" % (i, c["due"]))
        if not isinstance(c["actions"], list) or not all(isinstance(a, str) for a in c["actions"]):
            errs.append("cards[%d] actions 가 문자열 배열이 아님" % i)
    if not errs:
        keys = [(c["due"] is None, (c["due"] or "")[:16] if c["due"] and "T" in c["due"] else (c["due"] or "")[:10] + "T00:00")
                for c in obj]
        if keys != sorted(keys):
            errs.append("cards 가 due 오름차순이 아님")
    return errs


def check_files(out_dir: str | Path) -> dict[str, list[str]]:
    """out_dir 의 두 계약 파일을 읽어 {파일: 오류 목록}. 파일이 없으면 오류 한 줄."""
    out = {}
    for name, fn in (("brief-latest.json", validate_brief), ("cards-latest.json", validate_cards)):
        p = Path(out_dir) / name
        try:
            out[name] = fn(json.loads(p.read_text(encoding="utf-8")))
        except OSError:
            out[name] = ["파일이 없어요"]
        except ValueError as e:
            out[name] = ["JSON 이 아님: %s" % e]
    return out
