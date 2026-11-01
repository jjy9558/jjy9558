"""Steam 공개 API 호출. 키 없이 쓰는 스토어 API가 기본이고, 플레이 시간 동기화만 Web API 키가 필요하다."""

import json
import re
import urllib.parse
import urllib.request

STORE = "https://store.steampowered.com"
WEBAPI = "https://api.steampowered.com"
UA = "steam-buddy/0.1"


class SteamError(Exception):
    pass


def _get_json(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # 네트워크, HTTP, JSON 오류를 한 종류로 묶는다
        raise SteamError(f"Steam 요청 실패: {url} ({e})") from e


class SteamClient:
    """fetch(url, params) -> dict 를 주입할 수 있어서 테스트에서 네트워크 없이 쓸 수 있다."""

    def __init__(self, fetch=_get_json, cc="KR"):
        self.fetch = fetch
        self.cc = cc

    def search(self, term):
        data = self.fetch(f"{STORE}/api/storesearch/", {"term": term, "l": "korean", "cc": self.cc})
        return [{"appid": it["id"], "name": it["name"]} for it in data.get("items", [])]

    def app_details(self, appid):
        data = self.fetch(f"{STORE}/api/appdetails", {"appids": appid, "cc": self.cc, "l": "english"})
        entry = data.get(str(appid)) or {}
        if not entry.get("success"):
            raise SteamError(f"앱 {appid} 정보를 찾지 못했어요.")
        d = entry["data"]
        price = d.get("price_overview") or {}
        genres = [g.get("description", "") for g in d.get("genres", [])]
        genre_ids = [str(g.get("id")) for g in d.get("genres", [])]
        return {
            "appid": int(appid),
            "name": d.get("name", str(appid)),
            "is_free": bool(d.get("is_free")),
            # 스토어 API는 원화도 100을 곱한 값으로 준다
            "price": price.get("final", 0) // 100 if price else 0,
            "initial_price": price.get("initial", 0) // 100 if price else 0,
            "discount": price.get("discount_percent", 0) if price else 0,
            "currency": price.get("currency", ""),
            "korean": korean_support(d.get("supported_languages", "")),
            "early_access": "70" in genre_ids or "Early Access" in genres,
            "genres": genres,
            "release_date": (d.get("release_date") or {}).get("date", ""),
            "coming_soon": bool((d.get("release_date") or {}).get("coming_soon")),
        }

    def reviews(self, appid, language="all"):
        data = self.fetch(
            f"{STORE}/appreviews/{appid}",
            {"json": 1, "language": language, "purchase_type": "all", "num_per_page": 0},
        )
        q = data.get("query_summary") or {}
        pos, neg = q.get("total_positive", 0), q.get("total_negative", 0)
        total = pos + neg
        return {
            "desc": q.get("review_score_desc", ""),
            "positive": pos,
            "negative": neg,
            "total": total,
            "ratio": (pos / total) if total else None,
        }

    def last_update(self, appid):
        """개발사 공지(업데이트 노트) 중 가장 최근 것의 unix 시각. 없으면 None."""
        data = self.fetch(
            f"{WEBAPI}/ISteamNews/GetNewsForApp/v2/",
            {"appid": appid, "count": 5, "feeds": "steam_community_announcements"},
        )
        items = (data.get("appnews") or {}).get("newsitems", [])
        if not items:
            return None
        latest = max(items, key=lambda it: it.get("date", 0))
        return {"date": latest.get("date"), "title": latest.get("title", "")}

    def owned_games(self, key, steamid):
        data = self.fetch(
            f"{WEBAPI}/IPlayerService/GetOwnedGames/v1/",
            {"key": key, "steamid": steamid, "include_appinfo": 1, "include_played_free_games": 1},
        )
        games = (data.get("response") or {}).get("games", [])
        return [
            {"appid": g["appid"], "name": g.get("name", str(g["appid"])), "minutes": g.get("playtime_forever", 0)}
            for g in games
        ]

    def bundle_details(self, bundle_id):
        """번들 구성 게임과 가격 (best-effort).

        bundledetails 응답 모양은 공식 문서가 없어 {"<id>": {"success": true, "data": {...}}} 로 가정한다.
        data에서 name, 구성 앱(apps: [{id, name}] 또는 appids: [..]), 가격(price.final 또는 final_price,
        원화는 100배 값)을 읽고, 못 읽는 항목은 비워 둔다.
        """
        data = self.fetch(f"{STORE}/api/bundledetails",
                          {"bundleids": bundle_id, "cc": self.cc, "l": "korean"})
        entry = (data or {}).get(str(bundle_id)) if isinstance(data, dict) else None
        if not isinstance(entry, dict) or not entry.get("success", True) or not isinstance(entry.get("data"), dict):
            raise SteamError(f"번들 {bundle_id} 정보를 읽지 못했어요. 게임을 직접 적어 주세요.")
        d = entry["data"]
        appids = []
        for a in d.get("apps") or []:
            aid = a.get("id", a.get("appid")) if isinstance(a, dict) else a
            if str(aid).isdigit():
                appids.append(int(aid))
        for aid in d.get("appids") or []:
            if str(aid).isdigit() and int(aid) not in appids:
                appids.append(int(aid))
        raw = (d.get("price") or {}).get("final") if isinstance(d.get("price"), dict) else None
        if raw is None:
            raw = d.get("final_price")
        return {"name": d.get("name", str(bundle_id)), "appids": appids,
                "price": int(raw) // 100 if raw is not None else None}

    def wishlist(self, steamid):
        """공개 위시리스트의 appid 목록. 옛 wishlistdata 엔드포인트라 막혀 있을 수 있다(첫 페이지만)."""
        data = self.fetch(f"{STORE}/wishlist/profiles/{steamid}/wishlistdata/", {"p": 0})
        if not isinstance(data, dict) or not data or "success" in data:
            raise SteamError("위시리스트를 가져오지 못했어요. 비공개이거나 Steam이 이 방식을 막았을 수 있어요. "
                             "--appids나 --from-search를 쓰세요.")
        return [int(k) for k in data if str(k).isdigit()]


def korean_support(languages_html):
    """supported_languages 문자열에서 한국어 지원 수준을 읽는다.

    예: 'English<strong>*</strong>, Korean, Japanese<strong>*</strong>' 에서 *는 음성 지원 표시다.
    """
    text = languages_html or ""
    m = re.search(r"Korean(\s*<strong>\*</strong>)?", text)
    if not m:
        return "없음"
    return "자막+음성" if m.group(1) else "자막"
