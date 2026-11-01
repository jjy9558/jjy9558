"""분류 + 날짜 추출 결과로 Entry의 일정을 결정."""
import datetime as dt

from . import classify as C
from .dates import KST, UTC, extract_datetimes
from .markup import strip_markup
from .models import Entry

MAINT_DEFAULT = dt.timedelta(hours=2)
EVENT_DEFAULT = dt.timedelta(hours=1)


def _choose(category, cands):
    """-> (start, end, all_day) ; start/end는 all_day면 date, 아니면 UTC datetime."""
    first = cands[0]
    if category == C.SHUTDOWN:
        best = min(cands, key=lambda f: (f.date, f.start or dt.datetime.min.replace(tzinfo=UTC)))
        return best.date, None, True
    if category == C.MAINT:
        if first.has_time:
            return first.start, first.end or first.start + MAINT_DEFAULT, False
        hi = max(cands, key=lambda f: f.date)
        end = hi.date if hi.date > first.date and (hi.date - first.date).days <= 14 else None
        return first.date, end, True
    # 이벤트
    lo = min(cands, key=lambda f: f.date)
    hi = max(cands, key=lambda f: f.date)
    if hi.date > lo.date and (hi.date - lo.date).days <= 90:
        if lo.has_time and hi.has_time:
            return lo.start, hi.start, False
        return lo.date, hi.date, True
    if lo.has_time:
        return lo.start, lo.end or lo.start + EVENT_DEFAULT, False
    return lo.date, None, True


def build_entry(appid, game, item, summarizer=None, default_tz="UTC"):
    title = strip_markup(item.get("title", "")) or "(제목 없음)"
    body = strip_markup(item.get("contents", ""))
    post_ts = int(item.get("date", 0))
    post_dt = dt.datetime.fromtimestamp(post_ts, UTC)
    post_date = post_dt.astimezone(KST).date()
    category = C.classify(title, body)
    e = Entry(gid=str(item.get("gid")), appid=int(appid), game=game, title=title,
              url=item.get("url", ""), category=category, post_ts=post_ts,
              start=post_date, end=None, all_day=True, is_notice=True)
    if category in (C.MAINT, C.EVENT, C.SHUTDOWN):
        founds = extract_datetimes(title + "\n" + body[:4000], post_date, default_tz)
        cands = [f for f in founds if f.date >= post_date - dt.timedelta(days=1)]
        if cands:
            e.start, e.end, e.all_day = _choose(category, cands)
            e.is_notice = False
    if summarizer is not None:
        if category == C.PATCH:
            e.summary, e.summary_mode = summarizer.summarize(title, body)
        else:
            e.summary, e.summary_mode = summarizer.extractive(body, 2), "extract"
    return e
