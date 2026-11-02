"""GM 출력 파싱/검증: 서술 + 펜스 JSON(state_updates, roll)."""
import json
import logging
import re

log = logging.getLogger("gmtrpg")

KNOWN_KEYS = {"move_to", "add_location", "add_npc", "update_npc", "add_item", "remove_item",
              "quest_add", "quest_done", "add_fact", "hp_change", "hp", "roll", "state_updates"}
_FENCE = re.compile(r"```[ \t]*([a-zA-Z]*)[ \t]*\n?(.*?)```", re.S)
MAX_STR = 300


class Parsed:
    def __init__(self, narration="", updates=None, roll=None, errors=None):
        self.narration = narration
        self.updates = updates or {}
        self.roll = roll
        self.errors = errors or []


def _loads(s):
    s = s.strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    fixed = re.sub(r",\s*([}\]])", r"\1", s)  # 후행 쉼표
    fixed = fixed.replace("“", '"').replace("”", '"')
    try:
        return json.loads(fixed)
    except ValueError:
        return None


def _bare_json(text):
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, end = dec.raw_decode(text[i:])
            if isinstance(obj, dict) and KNOWN_KEYS & set(obj):
                return obj, i, i + end
        except ValueError:
            pass
        i = text.find("{", i + 1)
    return None


def extract(text):
    """(narration, json_dict|None, errors)"""
    errors = []
    found = None
    spans = []
    for m in _FENCE.finditer(text):
        body = m.group(2).strip()
        if not body.startswith("{"):
            continue
        spans.append(m.span())
        obj = _loads(body)
        if isinstance(obj, dict):
            found = obj
        else:
            errors.append("JSON 블록 파싱 실패")
    narration = text
    for a, b in reversed(spans):
        narration = narration[:a] + narration[b:]
    if found is None and not spans:
        r = _bare_json(text)
        if r:
            found = r[0]
            narration = text[:r[1]] + text[r[2]:]
    # 닫히지 않은 펜스(출력 잘림) 제거
    if "```" in narration:
        idx = narration.rfind("```")
        tail = narration[idx + 3:].lstrip()
        if re.match(r"^(json)?\s*\{", tail, re.I):
            narration = narration[:idx]
            errors.append("닫히지 않은 JSON 블록")
    return narration.strip(), found, errors


def _s(v, limit=MAX_STR):
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        return None
    v = str(v).strip()
    return v[:limit] if v else None


def _int(v, lo, hi):
    if isinstance(v, bool):
        return None
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return max(lo, min(hi, n))


def _as_list(v):
    return v if isinstance(v, list) else [v]


def validate_updates(raw):
    """잘못된 항목은 버리고 (clean, errors)를 돌려준다."""
    clean, errors = {}, []
    if not isinstance(raw, dict):
        return clean, ["state_updates가 객체가 아님"]

    def named(key, fields):
        out = []
        for e in _as_list(raw[key]):
            if isinstance(e, str):
                e = {"name": e}
            if not isinstance(e, dict) or not _s(e.get("name"), 60):
                errors.append("%s: 잘못된 항목 %r" % (key, e))
                continue
            d = {"name": _s(e["name"], 60)}
            for f in fields:
                if f == "qty":
                    d["qty"] = _int(e.get("qty", 1), 1, 999) or 1
                elif f == "links":
                    ls = e.get("links", [])
                    d["links"] = [x for x in (_s(l, 60) for l in _as_list(ls)) if x] if ls else []
                elif f in e and _s(e[f]) is not None:
                    d[f] = _s(e[f])
            out.append(d)
        return out

    if "move_to" in raw:
        v = _s(raw["move_to"], 60)
        if v:
            clean["move_to"] = v
        else:
            errors.append("move_to: 잘못된 값")
    for key, fields in (("add_location", ["description", "links"]),
                        ("add_npc", ["traits", "relation", "location"]),
                        ("update_npc", ["traits", "relation", "location"]),
                        ("add_item", ["qty", "description"]),
                        ("remove_item", ["qty"])):
        if key in raw and raw[key] not in (None, [], ""):
            lst = named(key, fields)
            if lst:
                clean[key] = lst
    if "quest_add" in raw and raw["quest_add"] not in (None, [], ""):
        out = []
        for e in _as_list(raw["quest_add"]):
            if isinstance(e, str):
                e = {"title": e}
            t = _s(e.get("title"), 80) if isinstance(e, dict) else None
            if not t:
                errors.append("quest_add: 잘못된 항목 %r" % (e,))
                continue
            out.append({"title": t, "detail": _s(e.get("detail")) or ""})
        if out:
            clean["quest_add"] = out
    for key in ("quest_done", "add_fact"):
        if key in raw and raw[key] not in (None, [], ""):
            out = []
            for e in _as_list(raw[key]):
                if isinstance(e, dict):
                    e = e.get("title") or e.get("text")
                s = _s(e)
                if s:
                    out.append(s)
                else:
                    errors.append("%s: 잘못된 항목 %r" % (key, e))
            if out:
                clean[key] = out
    for key in ("hp_change", "hp"):
        if key in raw:
            n = _int(raw[key], -999, 999)
            if n is None:
                errors.append("hp_change: 정수가 아님 %r" % (raw[key],))
            else:
                clean["hp_change"] = n
            break
    return clean, errors


def validate_roll(raw):
    if not isinstance(raw, dict):
        return None
    skill = _s(raw.get("skill"), 30) or "판정"
    dc = _int(raw.get("dc", 10), 1, 40)
    if dc is None:
        return None
    mode = raw.get("mode", "normal")
    if mode in ("adv", "유리"):
        mode = "advantage"
    elif mode in ("dis", "불리"):
        mode = "disadvantage"
    if mode not in ("normal", "advantage", "disadvantage"):
        mode = "normal"
    mod = _int(raw.get("modifier"), -10, 20) if raw.get("modifier") is not None else None
    return {"skill": skill, "dc": dc, "mode": mode, "modifier": mod}


def parse_gm_output(text):
    narration, obj, errors = extract(text or "")
    updates, roll = {}, None
    if obj is not None:
        raw_upd = obj.get("state_updates") if isinstance(obj.get("state_updates"), dict) else obj
        roll_raw = obj.get("roll", raw_upd.get("roll") if isinstance(raw_upd, dict) else None)
        if roll_raw is not None:
            roll = validate_roll(roll_raw)
            if roll is None:
                errors.append("roll: 잘못된 형식")
        updates, e2 = validate_updates({k: v for k, v in raw_upd.items() if k != "roll"})
        errors += e2
    for e in errors:
        log.warning("GM 출력 문제: %s", e)
    return Parsed(narration, updates, roll, errors)
