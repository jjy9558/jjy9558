"""테스트용 가짜 Steam 응답. 실제 응답 모양을 흉내 낸다."""

from datetime import datetime

NOW = datetime(2026, 11, 1, 12, 0, 0)


def details_payload(appid, name, price=None, initial=None, discount=0, langs="English<strong>*</strong>, Korean",
                    early=False, release="1 Jan, 2024", free=False):
    d = {
        "name": name,
        "is_free": free,
        "supported_languages": langs,
        "genres": [{"id": "70", "description": "Early Access"}] if early else [{"id": "1", "description": "Action"}],
        "release_date": {"coming_soon": False, "date": release},
    }
    if price is not None:
        d["price_overview"] = {
            "currency": "KRW", "initial": (initial or price) * 100, "final": price * 100,
            "discount_percent": discount,
        }
    return {str(appid): {"success": True, "data": d}}


class FakeSteam:
    """URL별로 정해진 JSON을 돌려주는 fetch. 호출 기록도 남긴다."""

    def __init__(self):
        self.search_items = []
        self.details = {}
        self.reviews = {}      # (appid, language) -> (pos, neg)
        self.news = {}         # appid -> [unix dates]
        self.owned = []
        self.calls = []
        self.bundles = {}      # bundle_id -> bundledetails 의 data
        self.wishlist = None   # {appid: {...}} 또는 None(실패)

    def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        if "storesearch" in url:
            return {"total": len(self.search_items), "items": self.search_items}
        if "appdetails" in url:
            appid = str(params["appids"])
            return {appid: self.details.get(appid, {"success": False})}
        if "appreviews" in url:
            appid = int(url.rstrip("/").split("/")[-1])
            pos, neg = self.reviews.get((appid, params["language"]), self.reviews.get((appid, "all"), (0, 0)))
            return {"success": 1, "query_summary": {
                "num_reviews": 0, "review_score_desc": "Very Positive",
                "total_positive": pos, "total_negative": neg, "total_reviews": pos + neg}}
        if "GetNewsForApp" in url:
            dates = self.news.get(int(params["appid"]), [])
            return {"appnews": {"appid": params["appid"], "newsitems": [
                {"gid": str(i), "title": f"공지{i}", "date": d} for i, d in enumerate(dates)]}}
        if "GetOwnedGames" in url:
            return {"response": {"game_count": len(self.owned), "games": [
                {"appid": a, "name": n, "playtime_forever": m} for a, n, m in self.owned]}}
        if "bundledetails" in url:
            bid = str(params["bundleids"])
            if bid not in self.bundles:
                return {bid: {"success": False}}
            return {bid: {"success": True, "data": self.bundles[bid]}}
        if "wishlistdata" in url:
            if self.wishlist is None:
                return {"success": 2}
            return {str(a): {"name": "w"} for a in self.wishlist}
        raise AssertionError("예상 못한 URL: " + url)
