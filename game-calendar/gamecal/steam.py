"""Steam GetNewsForApp 호출 (지연 + 디스크 캐시) 및 steam-buddy 가져오기."""
import json
import os
import time
import urllib.parse
import urllib.request

API = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"


class SteamNewsFetcher:
    def __init__(self, cache_dir, delay=1.5, ttl_hours=6, opener=None, sleep=time.sleep,
                 clock=time.time, timeout=20):
        self.cache_dir, self.delay, self.ttl = cache_dir, delay, ttl_hours * 3600
        self.opener = opener or urllib.request.build_opener()
        self.sleep, self.clock, self.timeout = sleep, clock, timeout
        self._last = None

    def _cache_path(self, appid, count):
        return os.path.join(self.cache_dir, "news_%s_%s.json" % (appid, count))

    def __call__(self, appid, count=10):
        path = self._cache_path(appid, count)
        cached = None
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cached = json.load(f)
            except (OSError, ValueError):
                cached = None
        if cached and self.clock() - cached.get("fetched_at", 0) < self.ttl:
            return cached["items"]
        if self._last is not None:
            wait = self.delay - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        qs = urllib.parse.urlencode({"appid": appid, "count": count,
                                     "feeds": "steam_community_announcements"})
        req = urllib.request.Request(API + "?" + qs, headers={"User-Agent": "gamecal/0.1"})
        try:
            self._last = self.clock()
            with self.opener.open(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            items = data["appnews"]["newsitems"]
        except Exception:
            if cached:  # 오래됐어도 캐시로 대체
                return cached["items"]
            raise
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": self.clock(), "items": items}, f, ensure_ascii=False)
        return items


def import_steam_buddy(path):
    """steam-buddy data.json (읽기 전용) -> {appid(int): name}"""
    path = os.path.expanduser(path)
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key, g in (data.get("games") or {}).items():
        try:
            appid = int(g.get("appid", key))
        except (TypeError, ValueError):
            continue
        out[appid] = g.get("name") or "App %d" % appid
    for a in (data.get("config") or {}).get("known_appids") or []:
        try:
            out.setdefault(int(a), "App %d" % int(a))
        except (TypeError, ValueError):
            pass
    return out
