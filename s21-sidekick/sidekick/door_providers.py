"""현관 출발 provider 3개: 물건 기억(object-memory) / 문 앞 노크(sound-watch) / 반품 마감(paper-butler).

provider 시그니처는 butler와 같다: fn(options, now) -> str | None.
- 전부 선택 기능이며 기본 꺼짐(옵션 enabled가 True여야 동작).
- 실패하면 로그 한 줄만 남기고 None (출발 체크리스트의 나머지는 그대로 읽는다). 모든 I/O에 타임아웃.
- 다른 프로젝트의 데이터는 읽기만 한다: HTTP는 GET, sqlite는 mode=ro + query_only.
- 로그에는 토큰·질문 내용·판매자/물건 이름을 남기지 않는다(예외 종류만).
"""
import datetime as dt
import json
import logging
import os
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("sidekick.door")

OBJMEM_DEFAULT_URL = "http://127.0.0.1:8788"
MIN_MATCH = 0.7  # object-memory 일치도: 1.0 같은 이름, 0.7 이름 일부, 0.4 위치 설명에만 등장


def _warn(provider, e):
    log.warning("%s 건너뜀: %s", provider, type(e).__name__)


def _enabled(opts):
    return bool(opts.get("enabled", False))


def _clean(s, limit=30):
    return " ".join(str(s or "").split())[:limit]


# ------------------------------------------------------------------ objmem_items

def _http_get_json(url, headers, timeout):
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise ValueError("http/https 주소만 허용")
    req = urllib.request.Request(url, headers=dict(headers, **{"User-Agent": "s21-sidekick"}), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(1024 * 1024).decode("utf-8"))


def _when_text(ts, now):
    t = dt.datetime.fromtimestamp(ts)
    days = (now.date() - t.date()).days
    hm = "%02d:%02d" % (t.hour, t.minute)
    if days <= 0:
        return "오늘 " + hm
    if days == 1:
        return "어제 " + hm
    return "%d월 %d일 %s" % (t.month, t.day, hm)


def objmem_items(opts, now, fetch=None):
    """설정의 물건마다 object-memory `GET /api/ask`로 마지막 목격 시각·위치만 말한다.
    stale_hours(기본 24)보다 오래된 기록, 기록 없음, 애매한 일치는 '모름' (거짓 안심 방지)."""
    if not _enabled(opts):
        return None
    items = [_clean(i) for i in (opts.get("items") or []) if _clean(i)]
    if not items:
        return None
    token = opts.get("token") or os.environ.get(opts.get("token_env") or "OBJMEM_TOKEN", "")
    if not token:
        log.warning("objmem_items 건너뜀: 토큰 없음 (token 또는 token_env)")
        return None
    base = (opts.get("url") or OBJMEM_DEFAULT_URL).rstrip("/")
    timeout = opts.get("timeout", 3)
    stale_s = float(opts.get("stale_hours", 24)) * 3600
    get = fetch or _http_get_json
    now_ts = now.timestamp()
    parts = []
    for name in items:
        q = urllib.parse.urlencode({"q": name + " 어디 있어", "llm": "0"})
        try:
            data = get(base + "/api/ask?" + q, {"X-ObjMem-Token": token}, timeout)
            hits = data.get("sightings") or []
        except Exception as e:  # 서버가 죽었으면 '모름'이라고 말하지 않고 통째로 건너뛴다
            _warn("objmem_items", e)
            return None
        good = [h for h in hits if isinstance(h, dict) and (h.get("match") or 0) >= MIN_MATCH
                and isinstance(h.get("ts"), (int, float))]
        if not good:
            parts.append("%s: 모름, 본 기록이 없어요" % name)
            continue
        h = max(good, key=lambda x: x["ts"])
        age = now_ts - h["ts"]
        if age > stale_s:
            hours = int(float(opts.get("stale_hours", 24)))
            parts.append("%s: 모름, %d시간 넘게 못 봤어요" % (name, hours))
            continue
        where = _clean(h.get("where"))
        parts.append("%s: %s %s" % (name, _when_text(h["ts"], now), (where + "에서 봤어요") if where else "봤어요"))
    return "물건 기억 - " + ". ".join(parts) + "." if parts else None


# ------------------------------------------------------------------ sqlite (읽기 전용)

def open_ro(path, timeout=2):
    """남의 sqlite를 읽기 전용으로 연다. 없으면 FileNotFoundError (새로 만들지 않음)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError("db 없음")
    con = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, timeout=timeout)
    try:
        con.execute("PRAGMA query_only=1")
    except Exception:
        con.close()  # Windows 에서 열린 핸들이 파일 삭제를 막는다
        raise
    return con


# ------------------------------------------------------------------ soundwatch_door

def soundwatch_door(opts, now):
    """sound-watch 저장소(detections 표)에서 최근 N시간의 노크/초인종 감지를 센다."""
    if not _enabled(opts) or not opts.get("db_path"):
        return None
    hours = float(opts.get("hours", 6))
    kinds = list(opts.get("kinds") or ["knock"])
    names = [str(n) for n in (opts.get("names") or ["초인종", "doorbell"])]
    start, end = now.timestamp() - hours * 3600, now.timestamp()
    con = None
    try:
        con = open_ro(opts["db_path"], opts.get("timeout", 2))
        where = " OR ".join(["kind=?"] * len(kinds) + ["name LIKE ?"] * len(names))
        rows = con.execute("SELECT ts FROM detections WHERE ts>=? AND ts<=? AND (%s) ORDER BY ts" % where,
                           [start, end] + kinds + ["%" + n + "%" for n in names]).fetchall()
    except Exception as e:
        _warn("soundwatch_door", e)
        return None
    finally:
        if con is not None:
            con.close()
    if not rows:
        return None
    last = dt.datetime.fromtimestamp(rows[-1][0])
    label = ("%d시간" % hours) if hours == int(hours) else ("%g시간" % hours)
    return ("최근 %s 안에 노크·초인종 소리가 %d번 있었어요 (마지막 %02d:%02d). 문 앞 택배 있을 수도 있어요."
            % (label, len(rows), last.hour, last.minute))


# ------------------------------------------------------------------ paperbutler_returns

_DAY_WORDS = {0: "오늘", 1: "내일", 2: "모레"}


def paperbutler_returns(opts, now):
    """paper-butler(refunds 표)에서 `days`일 이내(기본 2, 오늘 포함) 반품·환불 마감을 말한다. 처리 완료 건 제외."""
    if not _enabled(opts) or not opts.get("db_path"):
        return None
    days = int(opts.get("days", 2))
    today = now.date()
    con = None
    try:
        con = open_ro(opts["db_path"], opts.get("timeout", 2))
        rows = con.execute(
            "SELECT merchant, deadline FROM refunds WHERE done=0 AND deadline BETWEEN ? AND ? ORDER BY deadline, id",
            (today.isoformat(), (today + dt.timedelta(days=days)).isoformat())).fetchall()
    except Exception as e:
        _warn("paperbutler_returns", e)
        return None
    finally:
        if con is not None:
            con.close()
    parts = []
    for merchant, deadline in rows:
        try:
            n = (dt.date.fromisoformat(str(deadline)[:10]) - today).days
        except ValueError:
            continue
        parts.append("%s %s" % (_clean(merchant, 20) or "상점", _DAY_WORDS.get(n, "%d일 뒤" % n)))
    if not parts:
        return None
    shown = parts[: int(opts.get("max_items", 3))]
    more = " 외 %d건" % (len(parts) - len(shown)) if len(parts) > len(shown) else ""
    return "반품 마감: %s%s." % (", ".join(shown), more)


PROVIDERS = {"objmem_items": objmem_items, "soundwatch_door": soundwatch_door,
             "paperbutler_returns": paperbutler_returns}
