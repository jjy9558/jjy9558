"""환불 기한 계산, 얼리액세스 방치 위험 점수, 시간당 만족도 같은 순수 계산만 모아 둔다."""

import math
from datetime import datetime, timedelta

REFUND_DAYS = 14
REFUND_MINUTES = 120
URGENT_DAYS = 2
URGENT_MINUTES = 30


def parse_dt(s):
    return datetime.fromisoformat(s)


def refund_status(game, now):
    """Steam 환불 조건(구매 후 14일 이내, 플레이 2시간 미만) 기준으로 남은 여유를 계산한다."""
    bought = parse_dt(game["purchased_at"])
    deadline = bought + timedelta(days=REFUND_DAYS)
    time_left = deadline - now
    minutes = game.get("minutes", 0)
    minutes_left = REFUND_MINUTES - minutes
    refundable = time_left.total_seconds() > 0 and minutes_left > 0
    if game.get("kept"):
        state = "보유확정"
    elif not refundable:
        state = "기한지남"
    elif time_left <= timedelta(days=URGENT_DAYS) or minutes_left <= URGENT_MINUTES:
        state = "급함"
    else:
        state = "여유"
    return {
        "deadline": deadline,
        "time_left": time_left,
        "minutes": minutes,
        "minutes_left": minutes_left,
        "refundable": refundable,
        "state": state,
    }


def fmt_timedelta(td):
    secs = int(td.total_seconds())
    if secs <= 0:
        return "지남"
    days, rem = divmod(secs, 86400)
    hours = rem // 3600
    if days:
        return f"{days}일 {hours}시간"
    return f"{hours}시간 {(rem % 3600) // 60}분"


def fmt_minutes(m):
    if m <= 0:
        return "0분"
    h, mm = divmod(int(m), 60)
    return f"{h}시간 {mm}분" if h else f"{mm}분"


def fmt_won(n):
    return f"₩{int(n):,}"


_DATE_FORMATS = ("%d %b, %Y", "%b %d, %Y", "%d %B, %Y", "%B %d, %Y", "%Y-%m-%d")


def parse_release_date(s):
    s = (s or "").strip()
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def early_access_risk(details, reviews, last_update, now):
    """얼리액세스 게임이 방치될 위험을 0~100점으로 매긴다. 얼리액세스가 아니면 None.

    점수 근거를 reasons에 같이 돌려줘서 왜 그 점수인지 볼 수 있게 한다.
    """
    if not details.get("early_access"):
        return None
    score, reasons = 0, []

    if last_update and last_update.get("date"):
        age = (now - datetime.fromtimestamp(last_update["date"])).days
        if age > 180:
            score += 45
            reasons.append(f"마지막 개발사 공지가 {age}일 전 (반년 넘음)")
        elif age > 90:
            score += 30
            reasons.append(f"마지막 개발사 공지가 {age}일 전 (3개월 넘음)")
        elif age > 30:
            score += 10
            reasons.append(f"마지막 개발사 공지가 {age}일 전")
        else:
            reasons.append(f"최근 {age}일 안에 개발사 공지 있음")
    else:
        score += 35
        reasons.append("개발사 공지(업데이트 노트)를 찾지 못함")

    released = parse_release_date(details.get("release_date"))
    if released:
        years = (now - released).days / 365
        if years > 3:
            score += 25
            reasons.append(f"얼리액세스 {years:.1f}년째")
        elif years > 2:
            score += 15
            reasons.append(f"얼리액세스 {years:.1f}년째")
        elif years > 1:
            score += 5
            reasons.append(f"얼리액세스 {years:.1f}년째")

    ratio = reviews.get("ratio") if reviews else None
    if ratio is not None:
        if ratio < 0.6:
            score += 20
            reasons.append(f"긍정 리뷰 {ratio:.0%}로 낮음")
        elif ratio < 0.75:
            score += 10
            reasons.append(f"긍정 리뷰 {ratio:.0%}")
    if reviews and reviews.get("total", 0) < 50:
        score += 10
        reasons.append(f"리뷰가 {reviews.get('total', 0)}개뿐이라 판단 근거가 적음")

    score = min(score, 100)
    level = "높음" if score >= 60 else "보통" if score >= 30 else "낮음"
    return {"score": score, "level": level, "reasons": reasons}


def value_row(game):
    """산 뒤 돌아보기: 낸 돈, 플레이 시간, 시간당 비용, 만족도."""
    hours = game.get("minutes", 0) / 60
    price = game.get("price_paid", 0)
    per_hour = price / hours if hours >= 0.1 else None
    return {
        "name": game["name"],
        "price": price,
        "hours": hours,
        "per_hour": per_hour,
        "rating": game.get("rating"),
        "note": game.get("note", ""),
    }


def wilson_lower_bound(positive, total, z=1.96):
    """긍정 비율의 윌슨 신뢰구간 하한(기본 95%). 표본이 적을수록 보수적으로 깎인다."""
    if total <= 0:
        return 0.0
    p = positive / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / denom)


def gem_score(positive, total):
    """숨은 명작 점수(0~1): 윌슨 하한 70% + 단순 긍정 비율 30%. 30/30이 950/1000을 이기지 못하게 한다."""
    if total <= 0:
        return 0.0
    return 0.7 * wilson_lower_bound(positive, total) + 0.3 * (positive / total)


def bundle_value(items, bundle_price):
    """번들 실가치. items: [{'price': 원, 'owned': bool, 'skipped': bool}, ...].

    이미 가진 게임과 관심 없다고 뺀 게임은 실가치에서 제외한다.
    """
    counted = [i for i in items if not i.get("owned") and not i.get("skipped")]
    full_sum = sum(i["price"] for i in items if not i.get("skipped"))
    real = sum(i["price"] for i in counted)
    n = len(counted)
    per_game = bundle_price / n if n else None
    gain = real - bundle_price
    if n == 0:
        verdict = "살 만한 새 게임이 하나도 없어요. 번들은 필요 없어요."
    elif gain > 0:
        verdict = f"번들이 {fmt_won(gain)} 이득이에요."
    elif full_sum > bundle_price:
        verdict = "이미 가진 게임 빼면 개별 구매가 더 싸요."
    else:
        verdict = f"개별 구매가 {fmt_won(-gain)} 더 싸요."
    return {"full_sum": full_sum, "real": real, "new_count": n, "per_game": per_game,
            "gain": gain, "verdict": verdict}
