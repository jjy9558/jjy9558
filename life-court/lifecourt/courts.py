"""네 개의 재판소: 야식, 지각, 내기, 건배사. 모두 입력만으로 결정되는 템플릿 판결문."""
from __future__ import annotations

import hashlib
from datetime import date as Date
from datetime import timedelta

from .engine import Judgment, Picker


class CourtError(ValueError):
    """입력 오류. 메시지는 사용자에게 그대로 보인다."""


def _need(value: str | None, label: str) -> str:
    v = (value or "").strip()
    if not v:
        raise CourtError("%s이(가) 비었습니다. 입력해 주세요." % label)
    return v


def parse_date(s: str | None, today: Date) -> Date:
    if not s:
        return today
    try:
        return Date.fromisoformat(s.strip())
    except ValueError:
        raise CourtError("날짜는 YYYY-MM-DD 형식이어야 합니다: %s" % s) from None


def parse_time(s: str) -> tuple[int, int]:
    try:
        h, m = s.strip().split(":")
        h, m = int(h), int(m)
    except ValueError:
        raise CourtError("시각은 HH:MM 형식이어야 합니다: %s" % s) from None
    if not (0 <= h < 24 and 0 <= m < 60):
        raise CourtError("시각 범위가 이상합니다: %s" % s)
    return h, m


def _case_no(seed: str, prefix: str, d: Date) -> str:
    n = int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:2], "big") % 9000 + 1000
    return "%d%s %d호" % (d.year, prefix, n)


def _seed(explicit: str | None, *parts) -> str:
    return explicit if explicit else "|".join(str(p) for p in parts)


# ===================================================================== 야식
CRIMES = [
    (("치킨", "닭", "후라이드", "양념"), "야간 탐닉(치킨 가중)"),
    (("라면", "라멘", "짜파", "신라면"), "야간 국물 탐닉"),
    (("피자",), "야간 치즈 약취"),
    (("떡볶이", "분식", "순대"), "야간 매운맛 폭행"),
    (("족발", "보쌈"), "야간 돼지 점유"),
    (("아이스크림", "빙수", "케이크", "쿠키", "과자", "초콜릿"), "야간 당분 밀수"),
    (("맥주", "소주", "술", "와인"), "야간 음주 병합"),
    (("햄버거", "버거"), "야간 번 사이 은닉"),
]


def crime_name(food: str) -> str:
    for keys, name in CRIMES:
        if any(k in food for k in keys):
            return name
    return "야간 취식"


def hour_points(h: int) -> int:
    if h < 5:
        return 4 if h >= 2 else 3
    if h >= 23:
        return 2
    if h >= 22:
        return 1
    return 0


def night_score(h: int, kcal: int, today_count: int, priors: int) -> dict:
    parts = {
        "time": hour_points(h),
        "kcal": min(4, kcal // 300) if kcal > 0 else 0,
        "repeat_today": min(3, max(0, today_count - 1)),
        "recidivism": min(6, priors),
    }
    parts["total"] = sum(parts.values())
    return parts


def night_priors(entries: list[dict], d: Date, me_key: tuple) -> int:
    n = 0
    for e in entries:
        try:
            ed = Date.fromisoformat(str(e.get("date")))
        except ValueError:
            continue
        if (e.get("date"), e.get("time"), e.get("food")) == me_key:
            continue
        if timedelta(0) <= d - ed <= timedelta(days=7):
            n += 1
    return n


def judge_night(food: str, time: str, d: Date, *, kcal: int = 0, today_count: int = 1,
                priors: int = 0, defendant: str = "나", seed: str | None = None) -> Judgment:
    food = _need(food, "음식 이름")
    if kcal < 0 or today_count < 1:
        raise CourtError("칼로리는 0 이상, 오늘 횟수는 1 이상이어야 합니다.")
    h, m = parse_time(time)
    sc = night_score(h, kcal, today_count, priors)
    s = sc["total"]
    p = Picker(_seed(seed, "night", food, time, d, kcal, today_count, priors, defendant))
    crime = crime_name(food)
    days = max(1, s - 1)
    if s <= 1:
        tier, order = "선고유예", "피고인에게 선고를 유예한다. 다음에는 유예가 없다."
    elif s <= 3:
        tier, order = "벌금형", "피고인은 내일 샐러드 %d일에 처한다." % days
    elif s <= 6:
        tier, order = "징역형 집행유예", "피고인을 내일 샐러드 %d일에 처하되, 다음 야식 시 즉시 집행한다." % days
    else:
        tier, order = "실형", "피고인을 샐러드 %d일과 야식 앱 삭제 1일에 처한다. 법정 구속은 침대 위에서 집행한다." % days
    reasons = [
        "피고인은 %s %02d시 %02d분경 '%s'을(를) 취식하였고, 이는 %s에 해당한다." % (
            Date.isoformat(d), h, m, food, crime),
    ]
    if sc["time"]:
        reasons.append("- 심야 시간대 가중: +%d. %s" % (sc["time"], p.pick("time", [
            "위장도 퇴근한 시간이다.", "이 시각에 깨어 있는 것 자체가 정상참작 불가 사유이다.",
            "내일 아침 얼굴이 이미 증인으로 채택되었다."])))
    if sc["kcal"]:
        reasons.append("- 열량 약 %d kcal: +%d. %s" % (kcal, sc["kcal"], p.pick("kcal", [
            "계산은 대략이나 위장은 정확히 기억한다.", "저울은 거짓말을 하지 않는다.",
            "숨은 열량은 숨기려 해도 숨겨지지 않는다."])))
    if sc["repeat_today"]:
        reasons.append("- 오늘만 %d번째 취식: +%d. %s" % (today_count, sc["repeat_today"], p.pick("rep", [
            "하루에 두 번은 습관, 세 번은 생활양식이다.", "'한 번만 더'는 법정에서 인정되지 않는다."])))
    if sc["recidivism"]:
        reasons.append("- 최근 7일 내 전과 %d건: +%d. %s" % (priors, sc["recidivism"], p.pick("rec", [
            "상습성이 인정된다.", "반성문이 이미 재활용되었음이 의심된다.", "재범은 가중처벌한다."])))
    else:
        reasons.append("- 최근 7일 내 전과 없음. 초범으로서 참작한다.")
    plea = p.pick("plea", ["배가 고팠습니다", "냉장고가 먼저 쳐다봤습니다", "내일부터 하려고 마지막으로 먹었습니다",
                           "이것은 야식이 아니라 늦은 저녁입니다", "친구가 시켰습니다(친구 없음)"])
    sections = [
        ("주문", [order]),
        ("이유", reasons),
        ("피고인의 변소", ["피고인은 \"%s\"라고 주장하나, 이는 증거와 위장의 소리에 비추어 받아들이지 않는다." % plea]),
        ("양형 점수", ["- 시각 %d / 열량 %d / 반복 %d / 재범 %d = 합계 %d점" % (
            sc["time"], sc["kcal"], sc["repeat_today"], sc["recidivism"], s)]),
        ("항소", [p.pick("appeal", ["항소는 위장이 아니라 체중계 앞에서 하시오. 기각한다.",
                                    "항소기간은 내일 아침 체중 측정 시까지이며, 그때 결과가 나온다.",
                                    "항소 기각. 단 야식 대신 물 한 잔은 허용한다."])]),
    ]
    return Judgment("night-snack", "야 식 재 판 소  판 결 문", _case_no(p.seed, "야식", d), d.isoformat(),
                    [("피고인", defendant), ("죄명", crime), ("선고", "%s (점수 %d)" % (tier, s))],
                    sections, ruling="주문. %s 죄명은 %s. %s" % (defendant, crime, order))


# ===================================================================== 지각
EXCUSES = [
    (("버스", "지하철", "교통", "차가", "차 막", "도로"), "교통 탓",
     "대중교통은 피고인만 기다려 주지 않는다. 그러나 출발 시각은 피고인이 정한다."),
    (("알람", "늦잠", "자느라", "잠"), "수면 관련",
     "알람은 울렸고, 피고인이 껐다. 알람은 무죄이다."),
    (("길", "내비", "지도", "주차"), "길 찾기 관련",
     "지도 앱은 도착 시각을 미리 알려 주었다. 이는 예고된 범죄이다."),
    (("옷", "준비", "씻", "화장"), "준비 지연",
     "준비 시간은 늘 실제보다 짧게 추정된다. 피고인만의 일이 아니나 피고인의 책임이다."),
]


def classify_excuse(text: str) -> tuple[str, str]:
    for keys, label, comment in EXCUSES:
        if any(k in text for k in keys):
            return label, comment
    return "기타 변명", "변명의 독창성은 인정하나 시계는 이를 인정하지 않는다."


def late_priors(entries: list[dict], defendant: str, d: Date) -> int:
    n = 0
    for e in entries:
        if e.get("defendant") != defendant:
            continue
        try:
            ed = Date.fromisoformat(str(e.get("date")))
        except ValueError:
            continue
        if timedelta(0) <= d - ed <= timedelta(days=30) and ed != d:
            n += 1
    return n


def judge_late(defendant: str, minutes: int, meeting: str, d: Date, *, excuse: str = "",
               priors: int = 0, is_self: bool = True, seed: str | None = None) -> Judgment:
    defendant = _need(defendant, "피고인 이름")
    meeting = _need(meeting, "약속(모임) 이름")
    if not 1 <= minutes <= 1440:
        raise CourtError("지각 분은 1~1440 사이여야 합니다.")
    p = Picker(_seed(seed, "late", defendant, minutes, meeting, d, excuse, priors))
    base = 0 if minutes < 5 else 1 if minutes < 15 else 2 if minutes < 30 else 3 if minutes < 60 else 4
    cups = max(1, base + min(priors, 4))
    if minutes < 5:
        tier = "훈계"
        order = "피고인에게 훈계 방면한다. 5분 이내는 '인간적 오차'로 본다."
        cups = 0
    elif cups <= 2:
        tier, order = "벌금형", "피고인은 커피 %d잔을 모임에 납부하라." % cups
    elif cups <= 4:
        tier, order = "벌금형 가중", "피고인은 커피 %d잔과 다음 모임 선도착을 명한다." % cups
    else:
        tier, order = "중벌", "피고인은 커피 %d잔과 간식 1인분, 그리고 다음 약속 30분 전 도착을 명한다." % cups
    reasons = ["피고인은 '%s'에 %d분 늦게 도착하였다. 그 시간에 다른 참석자들은 메뉴판을 세 번 읽었다." % (meeting, minutes)]
    reasons.append("- 지각 정도: %d단계" % base)
    if priors:
        reasons.append("- 최근 30일 내 지각 전력 %d건. %s" % (priors, p.pick("prior", [
            "상습성이 인정되므로 가중한다.", "'이번이 마지막'이라는 말은 이미 여러 번 기록되었다."])))
    else:
        reasons.append("- 최근 30일 내 지각 전력 없음. 초범으로 참작한다.")
    sections = [("주문", [order]), ("이유", reasons)]
    if excuse.strip():
        label, comment = classify_excuse(excuse)
        sections.append(("피고인의 변명", ["\"%s\"" % excuse.strip(), "분류: %s. %s" % (label, comment)]))
    sections.append(("자진 출석", ["본 재판은 피고인 본인의 신청으로 열렸다. 자진 출석 사실은 양형에 참작되었다."]
                     if is_self else ["본 재판은 피고인의 동의를 받았음을 전제로 열렸다."]))
    sections.append(("항소", [p.pick("appeal", ["항소는 다음 약속에 10분 일찍 오는 방식으로만 가능하다. 기각한다.",
                                                "'차가 막혔다'는 항소 이유는 이미 판결 이유에 포함되어 있다. 기각한다.",
                                                "항소 기각. 단 커피 사이즈는 피고인이 고를 수 있다."])]))
    return Judgment("late", "지 각 재 판 소  판 결 문", _case_no(p.seed, "지각", d), d.isoformat(),
                    [("피고인", defendant), ("사건", "%s %d분 지각" % (meeting, minutes)), ("선고", tier)],
                    sections, ruling="%s 지각 재판 주문. %s" % (defendant, order))


# ===================================================================== 내기
def judge_bet(a: str, b: str, condition: str, winner: str, d: Date, *, stake: str = "치킨 한 마리",
              complaint: str = "", seed: str | None = None) -> Judgment:
    a, b = _need(a, "첫 번째 이름"), _need(b, "두 번째 이름")
    condition = _need(condition, "내기 조건")
    winner = _need(winner, "결과(승자 이름 또는 무승부)")
    stake = stake.strip() or "치킨 한 마리"
    if a == b:
        raise CourtError("두 사람의 이름이 같습니다. 혼자 한 내기는 독백입니다.")
    draw = winner in ("무승부", "draw", "무")
    if not draw and winner not in (a, b):
        raise CourtError("승자는 '%s' 또는 '%s' 또는 '무승부'여야 합니다." % (a, b))
    p = Picker(_seed(seed, "bet", a, b, condition, winner, stake, complaint, d))
    if draw:
        order = ["원고 %s와 피고 %s의 청구를 모두 기각한다." % (a, b), "%s은(는) 쌍방이 반씩 부담한다." % stake]
        loser = None
        ruling = "주문. %s와 %s의 내기는 무승부. %s은 반반 부담." % (a, b, stake)
    else:
        loser = b if winner == a else a
        order = ["%s의 승소를 선언한다." % winner, "%s은(는) %s에게 %s을(를) 지급하라." % (loser, winner, stake),
                 "소송비용은 패소자가 부담한다(치킨은 포함, 콜라는 별도)."]
        ruling = "주문. %s 승소. %s은 %s에게 %s을 지급하라." % (winner, loser, winner, stake)
    reasons = ["원고와 피고는 '%s'을(를) 걸고 %s을(를) 약정하였다. 구두 약정이나 사진 증거가 없으면 기억은 승자 편이다." % (
        condition, stake), p.pick("why", [
            "증거를 종합하면 결과에 다툼의 여지가 없다.", "양측 진술은 엇갈리나 스코어는 거짓말을 하지 않는다.",
            "법원은 내기의 정신이 '말보다 결과'에 있음을 확인한다."])]
    sections = [("주문", order), ("이유", reasons)]
    if complaint.strip() and loser:
        sections.append(("%s의 항소 이유" % loser, ["\"%s\"" % complaint.strip(),
                                               "위 주장은 이유 없다. %s" % p.pick("c", [
                                                   "억울함은 증거가 아니다.", "다음 내기에서 사실로 입증하시오."])]))
    sections.append(("항소", [p.pick("appeal", [
        "항소를 기각한다. 재항소와 상고도 미리 기각한다.", "항소 기각. 항소비용으로 음료 한 잔을 추가한다.",
        "항소는 이유 없어 기각한다. 재심은 다음 판에서 청구하시오."])]))
    sections.append(("복사용", ["이 판결문은 직접 복사해 보내세요. 자동 전송 기능은 없습니다."]))
    return Judgment("bet", "내 기 판 결 문", _case_no(p.seed, "내기", d), d.isoformat(),
                    [("원고", a), ("피고", b), ("사건", "내기 이행(%s)" % condition)], sections, ruling=ruling)


# ===================================================================== 건배사
GENERAL = [
    "오늘 이 자리를 위하여, 내일 숙취는 내일의 우리가 알아서 하는 것으로. 제가 '건강을' 하면 '위하여'!",
    "마신 잔은 추억이 되고, 남긴 안주는 눈치가 됩니다. 다 같이 '위하여'!",
    "우리의 건배는 짧게, 우정은 길게! 제가 '짧게' 하면 '길게'!",
    "지갑은 가볍게, 마음은 무겁게, 잔은 가득하게! 건배!",
    "오늘 웃은 만큼 내일 주름이 생기겠지만 괜찮습니다. 웃음 주름은 훈장입니다. 위하여!",
    "집에 갈 때는 모두 같이, 계산할 때는 각자 알아서! 건배!",
    "'다음에 한 번 보자'를 '다음 주에 보자'로 바꾸는 우리가 되기를! 위하여!",
]
NAMED = [
    "{name}님의 건강과 월급을 위하여! 월급은 통장을 스쳐 가는 바람이어도 좋습니다. 위하여!",
    "{name}님이 오늘 이 자리에 와 주신 것만으로 이미 승리입니다. 제가 '{name}'을 외치면 '고마워요'!",
    "{name}님의 다음 한 해가 지금 잔처럼 가득하고, 숙취는 이 잔처럼 가볍기를! 건배!",
    "{name}님을 위해, 그리고 {name}님이 쏘게 될 다음 라운드를 위해! 위하여!",
]
JOBY = [
    "{name}님은 {job}으로서 오늘도 고생하셨습니다. 일은 내일 하는 것이고 오늘은 마시는 것입니다. 위하여!",
    "{job} {name}님의 전문성이 이 술자리에서만은 안주 고르기에 쓰이길! 건배!",
    "세상에서 제일 바쁜 {job}, {name}님! 오늘만큼은 알림을 끄고 건배!",
]
OCCASION = "{occ}을(를) 맞아 한 말씀 올립니다. {name}님을 포함한 모두에게 복이 오기를! 위하여!"


def parse_guest(s: str) -> tuple[str, str]:
    name, _, job = s.partition(":")
    return name.strip(), job.strip()


def make_toasts(guests: list[str], d: Date, *, occasion: str = "", count: int = 5,
                seed: str | None = None) -> Judgment:
    parsed = [parse_guest(g) for g in guests]
    parsed = [(n, j) for n, j in parsed if n]
    if not parsed:
        raise CourtError("이름이 비었습니다. 이름을 한 명 이상 입력해 주세요. (예: 민수:개발자)")
    p = Picker(_seed(seed, "toast", [f"{n}:{j}" for n, j in parsed], occasion, d, count))
    pool: list[str] = []
    order = p.sample("order", list(range(len(parsed))), len(parsed))
    for i in p.sample("gen", list(range(len(GENERAL))), len(GENERAL)):
        pool.append(GENERAL[i])
    named = []
    for i, tmpl in enumerate(p.sample("named", NAMED, len(NAMED))):
        n, j = parsed[order[i % len(order)]]
        named.append(tmpl.format(name=n))
    jobbed = []
    for n, j in (x for x in parsed if x[1]):
        jobbed.append(p.pick("job" + n, JOBY).format(name=n, job=j))
    occ = []
    if occasion.strip():
        n = parsed[order[0]][0]
        occ.append(OCCASION.format(occ=occasion.strip(), name=n))
    chosen: list[str] = occ + jobbed[:2] + named[:1]
    rest = named[1:] + pool
    chosen += rest[: max(0, count - len(chosen))]
    chosen = chosen[:count]
    lines = ["- %s" % t for t in chosen]
    who = ", ".join(n for n, _ in parsed)
    sections = [("건배사 %d선" % len(chosen), lines),
                ("낭독 요령", ["잔을 들고, 마지막 '위하여'만 큰 소리로. 나머지는 천천히."]),
                ("복사용", ["이 문구는 직접 복사해 쓰세요. 자동 전송 기능은 없습니다."])]
    return Judgment("toast", "건 배 사  제 안 서", _case_no(p.seed, "건배", d), d.isoformat(),
                    [("참석자", who), ("자리", occasion.strip() or "일반 술자리")], sections,
                    ruling=chosen[0] if chosen else "")
