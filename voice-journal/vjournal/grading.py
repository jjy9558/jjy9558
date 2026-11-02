# -*- coding: utf-8 -*-
"""목표 채점과 한 줄 평 (순수 함수)."""
import zlib
from datetime import timedelta

GRADE_ORDER = "ABCDF"


def ratio_to_grade(r):
    if r >= 0.9:
        return "A"
    if r >= 0.75:
        return "B"
    if r >= 0.55:
        return "C"
    if r >= 0.3:
        return "D"
    return "F"


COMMENTS = {
    "A": [
        "선언한 대로 해냈다. 이 정도면 말이 아니라 예언이다.",
        "입으로만 하는 사람이 아니었군. 인정.",
        "깔끔하다. 과거의 나에게 보내는 완벽한 택배.",
    ],
    "B": [
        "거의 다 왔다. 마지막 10%가 늘 비싸다.",
        "합격점. 다만 아직 여유가 남아 보인다.",
        "선언의 90%는 지켰다. 나머지는 '개성'으로 하자.",
    ],
    "C": [
        "절반의 성공. 절반은 의지, 절반은 핑계.",
        "나쁘진 않은데 자랑하기엔 민망한 숫자.",
        "하긴 했다. 하긴.",
    ],
    "D": [
        "선언문이 좀 민망해하고 있다.",
        "시작이 반이라더니, 딱 그 반만 했다.",
        "의지는 있었다. 증거가 부족할 뿐.",
    ],
    "F": [
        "선언은 화려했고, 결과는 조용했다.",
        "이 목표는 지금 서랍 속에서 쉬고 있다.",
        "다음 주의 나에게 이 숙제를 정중히 토스한다.",
    ],
    "PENDING": [
        "아직 시간이 있다. 시간이 있다는 말이 제일 위험하다.",
        "마감이 다가온다. 저 멀리서 발소리가 들린다.",
    ],
    "DROPPED": [
        "접었다. 사유는 '왜 접었지?'에서 확인하자.",
        "깔끔하게 접었다. 접는 것도 결정이라고 해두자.",
    ],
}

OVERALL = {
    "A": "이번 주는 선언 지킴이. 말과 행동의 오차 0%에 가깝다.",
    "B": "꽤 믿음직한 한 주. 약속 대부분이 현실이 됐다.",
    "C": "평범하게 흔들린 한 주. 선언을 절반만 줄여도 점수가 오른다.",
    "D": "선언 인플레이션 주의. 목표를 줄이거나 의지를 늘리자.",
    "F": "이번 주 선언은 전부 BGM이었다. 다음 주엔 하나만 정해서 끝내보자.",
    None: "이번 주엔 선언이 없었다. 말하지 않으면 채점도 없다.",
}


def pick_comment(kind, seed):
    pool = COMMENTS[kind]
    return pool[zlib.crc32(str(seed).encode("utf-8")) % len(pool)]


def _d(s):
    from datetime import datetime
    return datetime.strptime(s, "%Y-%m-%d").date() if isinstance(s, str) else s


def grade_goal(goal, events, week_start, week_end, today):
    """goal: dict, events: [(date, amount)]. 반환 dict(ratio, grade, final, progress, target, note, comment)."""
    week_start, week_end, today = _d(week_start), _d(week_end), _d(today)
    period = goal["period"]
    target = goal.get("target")
    need = target if target else 1
    deadline = _d(goal["deadline"]) if goal.get("deadline") else None
    start = _d(goal["start_day"]) if goal.get("start_day") else _d(goal["created_day"])
    ev = [(_d(d), a) for d, a in events]
    res = {"goal_id": goal["id"], "title": goal["title"], "period": period,
           "target": target, "unit": goal.get("unit"), "deadline": deadline}

    def finish(ratio, final, progress, note, kind=None):
        res.update(ratio=ratio, final=final, progress=progress, note=note)
        res["grade"] = ratio_to_grade(ratio)
        res["comment"] = pick_comment(kind or res["grade"], "%s-%s" % (goal["id"], week_start))
        return res

    if goal["status"] == "dropped":
        dd = _d(goal["dropped_day"]) if goal.get("dropped_day") else week_start
        part = sum(a for d, a in ev if week_start <= d <= week_end)
        return finish(0.0, True, part, "접음 (%s)" % dd.isoformat(), "DROPPED")

    if period == "day":
        s = max(start, week_start)
        e = min(week_end, deadline or week_end, today)
        if e == today and today <= week_end and e > s:
            day_sum = sum(a for d, a in ev if d == today)
            if day_sum < need:
                e = today - timedelta(days=1)   # 오늘은 아직 끝나지 않았으니 봐준다
        days = (e - s).days + 1
        if days <= 0:
            res.update(progress=0, note="아직 시작 전")
            return _pending(res)
        met = 0
        d = s
        while d <= e:
            if sum(a for dd, a in ev if dd == d) >= need:
                met += 1
            d += timedelta(days=1)
        final = today > week_end
        return finish(met / float(days), final, met, "%d/%d일 달성" % (met, days))

    if period == "week":
        got = sum(a for d, a in ev if week_start <= d <= week_end)
        final = today > week_end
        return finish(min(1.0, got / float(need)), final, got, "이번 주 %s/%s" % (_fmt(got), _fmt(need)))

    # once
    total = sum(a for d, a in ev)
    if goal["status"] == "done" and goal.get("done_day"):
        dd = _d(goal["done_day"])
        if deadline is None or dd <= deadline:
            return finish(1.0, True, total, "기한 내 완료 (%s)" % dd.isoformat())
        late = (dd - deadline).days
        return finish(max(0.3, 0.7 - 0.1 * (late - 1)), True, total, "%d일 늦게 완료" % late)
    frac = min(1.0, total / float(need))
    if deadline is not None and deadline < today:
        return finish(frac * 0.8, True, total, "기한 지남 (%s/%s)" % (_fmt(total), _fmt(need)))
    return finish(frac, False, total, "진행 중 (%s/%s)" % (_fmt(total), _fmt(need)),
                  "PENDING" if frac < 0.9 else None)


def _pending(res):
    res.update(grade=None, ratio=None, final=False, comment=pick_comment("PENDING", res["goal_id"]))
    return res


def _fmt(x):
    return str(int(x)) if float(x) == int(x) else "%.1f" % x


def overall_grade(results):
    rs = [r["ratio"] for r in results if r.get("ratio") is not None]
    if not rs:
        return None, None
    avg = sum(rs) / len(rs)
    return ratio_to_grade(avg), avg
