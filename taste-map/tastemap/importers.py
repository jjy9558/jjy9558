"""세 가지 입력을 읽어 Store 에 반영한다. 네트워크는 쓰지 않는다."""
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ._jjkit import redact


class ImportFailed(ValueError):
    """입력 파일 형식이 맞지 않을 때."""


def _num(v, default=0):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


# ----------------------------------------------------------------- steam-buddy
def import_steam(store, path, compat_path=None) -> dict:
    """steam-buddy data.json 을 읽는다. 기존 필드만 필수로 보고 새 필드는 있으면 쓴다.

    환불한 게임은 제외. 이름이 없는 항목은 건너뛰고 skipped 로 센다."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ImportFailed(f"steam-buddy data.json 을 읽지 못했어요: {e}")
    games = raw.get("games") if isinstance(raw, dict) else None
    if not isinstance(games, dict):
        raise ImportFailed("steam-buddy data.json 형식이 아니에요. ('games' 항목이 없어요.)")
    compat = _load_compat(Path(compat_path) if compat_path else p.with_name("compat.json"))
    n = skipped = refunded = 0
    for key, g in games.items():
        if not isinstance(g, dict) or not g.get("name"):
            skipped += 1
            continue
        if g.get("refunded"):
            refunded += 1
            continue
        appid = g.get("appid", key)
        try:
            appid = int(appid)
        except (TypeError, ValueError):
            skipped += 1
            continue
        rec = {
            "appid": appid,
            "name": str(g["name"])[:120],
            "purchased_at": g.get("purchased_at") if isinstance(g.get("purchased_at"), str) else None,
            "price_paid": _num(g.get("price_paid")),
            "minutes": _num(g.get("minutes")),
            "rating": g.get("rating") if isinstance(g.get("rating"), int) and 1 <= g["rating"] <= 5 else None,
        }
        # 있으면 쓰는 선택 필드
        extra_tags = g.get("genres") or g.get("tags")
        if isinstance(extra_tags, dict):
            extra_tags = list(extra_tags)
        if isinstance(extra_tags, list):
            rec["genres"] = [str(t)[:40] for t in extra_tags[:20] if isinstance(t, (str, int))]
        c = compat.get(str(appid)) or {}
        deck = g.get("deck") or c.get("deck")
        if isinstance(deck, str):
            rec["deck"] = deck
        ac = g.get("anticheat") or c.get("anticheat")
        if isinstance(ac, dict) and ac.get("kind"):
            rec["anticheat"] = str(ac["kind"])[:40]
        store.steam[str(appid)] = rec
        n += 1
    # 호환 정보는 후보 순위(--handheld)에도 쓰려고 보유 여부와 상관없이 덱 등급만 따로 둔다
    store.data["compat"] = {k: {"deck": v["deck"]} for k, v in compat.items() if isinstance(v.get("deck"), str)}
    return {"imported": n, "skipped": skipped, "refunded": refunded, "compat": bool(compat)}


def _load_compat(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    g = d.get("games") if isinstance(d, dict) else None
    # 예시 항목(example)은 실제 정보가 아니라서 버린다
    return {k: v for k, v in (g or {}).items() if isinstance(v, dict) and not v.get("example")}


# ----------------------------------------------------------------- game-diary
def import_diary(store, db_path) -> dict:
    """game-diary sqlite 를 읽기 전용으로 열어 게임별로 집계한다. 캡션 원문은 가져오지 않는다."""
    p = Path(db_path)
    if not p.exists():
        raise ImportFailed(f"파일이 없어요: {p}")
    uri = "file:" + p.resolve().as_posix() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute(
            "SELECT f.game, f.appid, f.ts, c.tags, c.mood, c.notable FROM files f "
            "LEFT JOIN captions c ON c.hash = f.hash WHERE f.skip_reason IS NULL").fetchall()
    except sqlite3.DatabaseError as e:
        raise ImportFailed(f"game-diary DB 형식이 아니에요: {e}")
    finally:
        con.close()
    out = {}
    for game, appid, ts, tags_json, mood, notable in rows:
        name = (game or "").strip()
        appid_s = str(appid).strip() if appid not in (None, "") else ""
        if not name and not appid_s:
            continue
        key = appid_s if appid_s.isdigit() else "n:" + norm_name(name)
        e = out.setdefault(key, {"game": name or appid_s, "appid": int(appid_s) if appid_s.isdigit() else None,
                                 "shots": 0, "captioned": 0, "notable": 0, "tags": {}, "moods": {},
                                 "first": None, "last": None})
        e["shots"] += 1
        if notable:
            e["notable"] += 1
        if ts:
            e["first"] = ts if e["first"] is None or ts < e["first"] else e["first"]
            e["last"] = ts if e["last"] is None or ts > e["last"] else e["last"]
        if tags_json is not None or mood:
            e["captioned"] += 1
        try:
            tags = json.loads(tags_json) if tags_json else []
        except ValueError:
            tags = []
        for t in tags if isinstance(tags, list) else []:
            if isinstance(t, str) and t.strip():
                t = t.strip().lower()[:40]
                e["tags"][t] = e["tags"].get(t, 0) + 1
        if mood and mood.strip():
            m = mood.strip().lower()[:40]
            e["moods"][m] = e["moods"].get(m, 0) + 1
    store.data["diary"] = out  # 다이어리는 DB 에서 다시 계산한 스냅샷이라 통째로 바꾼다
    return {"games": len(out), "shots": sum(e["shots"] for e in out.values())}


def norm_name(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", (s or "").lower())


# ----------------------------------------------------------------- youtube
_PREFIX = re.compile(r"^(?:Watched|Viewed|시청한 동영상|시청 기록|시청함|시청)\s*[:：]?\s+", re.I)


def _video_id(url: str) -> str:
    try:
        u = urlparse(url)
    except ValueError:
        return ""
    if u.netloc.endswith("youtu.be"):
        return u.path.strip("/")
    return (parse_qs(u.query).get("v") or [""])[0]


def import_youtube(store, path, since: str | None = None) -> dict:
    """Google 테이크아웃 watch-history.json 을 가져온다. 제목과 시각만 남기고 URL은 버린다.

    같은 영상(영상 id, 없으면 제목)을 여러 번 봤으면 한 항목에 시각을 모아 횟수로 합산한다.
    같은 파일을 다시 가져와도 시각이 같으면 두 번 세지 않는다."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ImportFailed(f"watch-history.json 을 읽지 못했어요: {e}")
    if not isinstance(raw, list):
        raise ImportFailed("테이크아웃 watch-history.json 형식(목록)이 아니에요.")
    entries = skipped = 0
    for it in raw:
        if not isinstance(it, dict):
            skipped += 1
            continue
        if it.get("details"):  # 광고 시청 등
            skipped += 1
            continue
        title = str(it.get("title") or "").strip()
        url = str(it.get("titleUrl") or "")
        title = _PREFIX.sub("", title, count=1).strip()
        if not title or title.lower().startswith("http") or not url:
            skipped += 1  # 삭제된 영상: 제목 대신 URL 이 들어 있다
            continue
        t = str(it.get("time") or "")
        if since and t[:10] and t[:10] < since:
            skipped += 1
            continue
        vid = _video_id(url)
        key = hashlib.sha1((vid or title).encode("utf-8")).hexdigest()[:12]
        rec = store.youtube.get(key)
        if rec is None:
            rec = {"title": redact.redact(title)[:160], "times": []}
            store.youtube[key] = rec
        entries += 1
        if t and t not in rec["times"]:
            rec["times"].append(t)
        elif not t:
            rec["undated"] = rec.get("undated", 0) + 1
    for rec in store.youtube.values():
        rec["times"].sort()
    return {"entries": entries, "videos": len({k for k in store.youtube}), "skipped": skipped}


def watch_count(rec: dict) -> int:
    return max(1, len(rec.get("times", [])) + int(rec.get("undated", 0)))


def parse_iso(s):
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
