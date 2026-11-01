import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional

from .dates import KST, UTC


def _ser(v):
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return v.isoformat()


def _de(s):
    if s is None:
        return None
    if "T" in s:
        return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return dt.date.fromisoformat(s)


@dataclass
class Entry:
    gid: str
    appid: int
    game: str
    title: str
    url: str
    category: str
    post_ts: int
    start: object = None        # all_day: date, else UTC datetime
    end: object = None          # all_day: 포함(inclusive) 날짜, else UTC datetime
    all_day: bool = True
    summary: List[str] = field(default_factory=list)
    summary_mode: str = ""      # "llm" | "extract" | ""
    is_notice: bool = True      # True: 일정 추출 실패 -> 게시일 기준 '공지'

    def to_dict(self):
        return {"gid": self.gid, "appid": self.appid, "game": self.game, "title": self.title,
                "url": self.url, "category": self.category, "post_ts": self.post_ts,
                "start": _ser(self.start), "end": _ser(self.end), "all_day": self.all_day,
                "summary": self.summary, "summary_mode": self.summary_mode,
                "is_notice": self.is_notice}

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["start"] = _de(d.get("start"))
        d["end"] = _de(d.get("end"))
        return cls(**d)

    def start_date_kst(self):
        if self.all_day:
            return self.start
        return self.start.astimezone(KST).date()

    def when_text(self):
        if self.all_day:
            s = self.start.isoformat()
            if self.end and self.end != self.start:
                s += " ~ " + self.end.isoformat()
            return s + " (종일)"
        s = self.start.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
        if self.end:
            s += " ~ " + self.end.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
        return s
