"""귀가 집사: 작은 provider 함수들의 결과를 모아 한 번에 읽어준다.

provider 시그니처: fn(options: dict, now: datetime) -> str | None
None/빈 문자열이면 건너뛴다. 새 provider는 PROVIDERS에 등록하면 끝.
"""
import datetime as dt
import os

from .triggers import Cooldown


def greeting(opts, now):
    h = now.hour
    base = "좋은 아침이에요" if h < 11 else ("어서 와요" if h < 18 else "수고했어요, 어서 와요")
    return opts.get("text") or base + "."


def _read_text(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().strip() or None


def _unfold(text):
    out = []
    for line in text.splitlines():
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _parse_ics_dt(value):
    v = value.strip()
    if len(v) == 8:
        return dt.datetime.strptime(v, "%Y%m%d"), True
    if v.endswith("Z"):
        d = dt.datetime.strptime(v[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc)
        return d.astimezone().replace(tzinfo=None), False
    return dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S"), False


def parse_ics_events(text):
    """아주 단순한 .ics 파서: VEVENT의 DTSTART/SUMMARY만. 반복 일정(RRULE)은 미지원."""
    events, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            if "start" in cur:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            key, _, val = line.partition(":")
            name = key.split(";")[0].upper()
            if name == "DTSTART":
                try:
                    cur["start"], cur["all_day"] = _parse_ics_dt(val)
                except ValueError:
                    pass
            elif name == "SUMMARY":
                cur["summary"] = val.replace("\\,", ",").replace("\\n", " ").strip()
    return events


def calendar_ics(opts, now):
    text = _read_text(opts.get("path"))
    if not text:
        return None
    today = [e for e in parse_ics_events(text) if e["start"].date() == now.date()]
    if not today:
        return "오늘 남은 일정은 없어요."
    today.sort(key=lambda e: e["start"])
    parts = []
    for e in today[:5]:
        when = "종일" if e["all_day"] else e["start"].strftime("%H시 %M분").replace(" 00분", "")
        parts.append("%s %s" % (when, e.get("summary", "제목 없음")))
    return "오늘 일정 %d개: %s." % (len(today), ", ".join(parts))


def claude_status(opts, now):
    """자리표시자: Claude Code 훅 등이 써 둔 상태 파일을 읽는다 (예: '빌드 통과, PR 1개 리뷰 대기')."""
    text = _read_text(opts.get("path"))
    return ("Claude 상태: " + text.splitlines()[0]) if text else None


def weather(opts, now):
    """자리표시자: 아직 실제 API 연동 없음. opts.text가 있으면 그대로."""
    return opts.get("text") or None


def delivery(opts, now):
    """자리표시자: 택배 조회 미구현."""
    return opts.get("text") or None


PROVIDERS = {"greeting": greeting, "calendar_ics": calendar_ics,
             "claude_status": claude_status, "weather": weather, "delivery": delivery}


def assemble(provider_cfgs, now, registry=None):
    reg = registry or PROVIDERS
    parts = []
    for p in provider_cfgs:
        fn = reg.get(p.get("name"))
        if not fn:
            continue
        try:
            text = fn(p, now)
        except Exception:  # provider 하나가 죽어도 나머지는 읽는다
            text = None
        if text and text.strip():
            parts.append(text.strip())
    return " ".join(parts)


class Butler:
    def __init__(self, cfg, clock=None, monotonic=None, registry=None):
        self.cfg = cfg
        self.clock = clock or dt.datetime.now
        self.registry = registry
        self.cooldown = Cooldown(cfg["cooldown_seconds"], **({"clock": monotonic} if monotonic else {}))

    def arrive(self, source="button", force=False):
        if not self.cfg.get("enabled", True):
            return {"ok": False, "say": ""}
        manual = source == "button" or force
        if manual:
            self.cooldown.touch("arrival")  # 버튼은 쿨다운 무시, 대신 직후 모션 재트리거를 막는다
        elif not self.cooldown.try_acquire("arrival"):
            return {"ok": False, "cooldown": True, "say": ""}
        return {"ok": True, "say": assemble(self.cfg["providers"], self.clock(), self.registry)}
