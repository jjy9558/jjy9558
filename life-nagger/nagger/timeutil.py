from datetime import datetime, timedelta

WEEKDAYS_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


def parse_hhmm(s):
    try:
        h, m = str(s).split(":")
        h, m = int(h), int(m)
    except (ValueError, TypeError):
        raise ValueError("시각은 HH:MM 형식이어야 합니다: %r" % (s,))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("시각 범위 오류: %r" % (s,))
    return h * 60 + m


def in_window(now, start, end):
    """now(datetime)가 [start, end) 안인가. 자정을 넘는 구간(23:00~05:00)도 지원."""
    t = now.hour * 60 + now.minute
    a, b = parse_hhmm(start), parse_hhmm(end)
    if a == b:
        return False
    if a < b:
        return a <= t < b
    return t >= a or t < b


def fmt_duration(sec):
    """3600*112 -> '112시간', 90분 -> '1시간 30분', 45분 -> '45분'."""
    total = int(round(max(0, sec) / 60.0))
    h, m = divmod(total, 60)
    if h and m:
        return "%d시간 %d분" % (h, m)
    if h:
        return "%d시간" % h
    return "%d분" % m


def ko_hour(h):
    if h == 0:
        return "오전 12시"
    if h < 12:
        return "오전 %d시" % h
    if h == 12:
        return "오후 12시"
    return "오후 %d시" % (h - 12)


def next_midnight(dt):
    return datetime(dt.year, dt.month, dt.day) + timedelta(days=1)
