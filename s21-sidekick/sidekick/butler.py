"""귀가 집사: 작은 provider 함수들의 결과를 모아 한 번에 읽어준다.

provider 시그니처: fn(options: dict, now: datetime) -> str | None
None/빈 문자열이면 건너뛴다. 새 provider는 PROVIDERS에 등록하면 끝.
"""
import datetime as dt
import json
import os
import urllib.parse
import urllib.request

from . import door_providers
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


def _fmt_time(d):
    return d.strftime("%H시 %M분").replace(" 00분", "")


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
        when = "종일" if e["all_day"] else _fmt_time(e["start"])
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


def calendar_next(opts, now):
    """출발용: 오늘 아직 시작 안 한 첫 일정 (+ 종일 일정). 없으면 아무 말 안 함."""
    text = _read_text(opts.get("path"))
    if not text:
        return None
    today = [e for e in parse_ics_events(text) if e["start"].date() == now.date()]
    grace = dt.timedelta(minutes=opts.get("grace_minutes", 10))
    timed = sorted((e for e in today if not e["all_day"] and e["start"] >= now - grace),
                   key=lambda e: e["start"])
    parts = []
    if timed:
        parts.append("첫 일정은 %s %s." % (_fmt_time(timed[0]["start"]), timed[0].get("summary", "제목 없음")))
    all_day = [e.get("summary", "제목 없음") for e in today if e["all_day"]]
    if all_day:
        parts.append("종일 일정: %s." % ", ".join(all_day[:3]))
    return " ".join(parts) or None


def reminders(opts, now):
    """고정 체크 항목. 항목은 "지갑" 또는 {"text": "분리수거", "weekdays": [0, 3]} (0=월요일)."""
    items = []
    for it in opts.get("items") or []:
        if isinstance(it, dict):
            days = it.get("weekdays")
            if days is not None and now.weekday() not in days:
                continue
            it = it.get("text")
        if it and str(it).strip():
            items.append(str(it).strip())
    if not items:
        return None
    return "%s %s." % (opts.get("prefix") or "챙기세요:", ", ".join(items))


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def _fetch_json(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "s21-sidekick"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def open_meteo(opts, now, fetch=None):
    """Open-Meteo (API 키 불필요). 기본 꺼짐: enabled true + latitude/longitude 필요. 실패하면 조용히 None."""
    if not opts.get("enabled", False):
        return None
    lat, lon = opts.get("latitude"), opts.get("longitude")
    if lat is None or lon is None:
        return None
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "current": "temperature_2m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto", "forecast_days": 1})
    try:
        data = (fetch or _fetch_json)(opts.get("url", OPEN_METEO_URL) + "?" + q, opts.get("timeout", 4))
        daily = data.get("daily") or {}
        cur = (data.get("current") or {}).get("temperature_2m")

        def first(key):
            v = daily.get(key) or [None]
            return v[0]
        hi, lo, rain = first("temperature_2m_max"), first("temperature_2m_min"), first("precipitation_probability_max")
    except Exception:
        return None
    parts = []
    if cur is not None:
        parts.append("지금 %d도" % round(cur))
    if hi is not None and lo is not None:
        parts.append("오늘 최고 %d도, 최저 %d도" % (round(hi), round(lo)))
    if not parts and rain is None:
        return None
    text = ", ".join(parts) + "." if parts else ""
    if rain is not None:
        if rain >= opts.get("umbrella_threshold", 50):
            text += " 강수확률 %d%%, 우산 챙기세요." % round(rain)
        else:
            text += " 강수확률 %d%%." % round(rain)
    return text.strip()


GPU_QUERY = ["--query-gpu=name,utilization.gpu", "--format=csv,noheader,nounits"]


def gpu_busy(opts, now, runner=None):
    """선택: nvidia-smi로 GPU 사용률 확인. 기준 이상이면 '아직 일하는 중' 알림. 인자 리스트, shell=False."""
    if not opts.get("enabled", False):
        return None
    if runner is None:
        from .debugger import default_runner as runner
    argv = [opts.get("nvidia_smi") or "nvidia-smi"] + GPU_QUERY
    code, out = runner(argv, cwd=None, timeout=opts.get("timeout", 5))
    if code != 0:
        return None
    threshold = opts.get("threshold", 30)
    busy = []
    for line in (out or "").splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 2:
            continue
        try:
            util = float(cols[1])
        except ValueError:
            continue
        if util >= threshold:
            busy.append((util, cols[0]))
    if not busy:
        return None
    util, name = max(busy)
    return "PC가 아직 일하는 중이에요: %s 사용률 %d%%. 켜 두고 갈 건지 확인하세요." % (name, round(util))


PROVIDERS = {"greeting": greeting, "calendar_ics": calendar_ics,
             "claude_status": claude_status, "weather": weather, "delivery": delivery,
             "calendar_next": calendar_next, "reminders": reminders,
             "open_meteo": open_meteo, "gpu_busy": gpu_busy}
PROVIDERS.update(door_providers.PROVIDERS)  # 선택 기능(기본 꺼짐): objmem_items 등


def assemble(provider_cfgs, now, registry=None):
    return " ".join(assemble_parts(provider_cfgs, now, registry))


def assemble_parts(provider_cfgs, now, registry=None):
    reg = registry or PROVIDERS
    parts = []
    for p in provider_cfgs:
        fn = reg.get(p.get("name"))
        if not fn or p.get("enabled") is False:
            continue
        try:
            text = fn(p, now)
        except Exception:  # provider 하나가 죽어도 나머지는 읽는다
            text = None
        if text and text.strip():
            parts.append(text.strip())
    return parts


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
