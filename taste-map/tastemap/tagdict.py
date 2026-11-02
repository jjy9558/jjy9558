"""오프라인 태그 사전: data/tags.json(번들) + <데이터 폴더>/tags.json(사용자)."""
import json
import re
from pathlib import Path

from .importers import norm_name

SHIPPED = Path(__file__).parent / "data" / "tags.json"


class TagDict:
    def __init__(self, tags=None, games=None):
        self.tags = tags or {}      # id -> {label, group, aliases}
        self.games = games or []    # [{names, appid, tags}]
        self._rx = {}
        self._by_appid = {}
        self._by_name = {}
        self._index()

    @classmethod
    def load(cls, user_path=None):
        data = json.loads(SHIPPED.read_text(encoding="utf-8"))
        tags = dict(data.get("tags", {}))
        games = list(data.get("games", []))
        if user_path and Path(user_path).exists():
            try:
                u = json.loads(Path(user_path).read_text(encoding="utf-8"))
            except ValueError:
                u = {}
            for tid, t in (u.get("tags") or {}).items():
                if isinstance(t, dict):
                    tags[tid] = {**tags.get(tid, {}), **t}
            games = list(u.get("games") or []) + games  # 사용자 항목이 먼저 맞도록
        return cls(tags, games)

    def _index(self):
        for tid, t in self.tags.items():
            pats = []
            for a in t.get("aliases", []) + [t.get("label", "")]:
                a = str(a).strip().lower()
                if not a:
                    continue
                if a.isascii():
                    pats.append(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])")
                else:
                    pats.append(re.escape(a))
            self._rx[tid] = re.compile("|".join(pats)) if pats else None
        for g in self.games:
            if g.get("appid") is not None:
                self._by_appid.setdefault(str(g["appid"]), g)
            for n in g.get("names", []):
                self._by_name.setdefault(norm_name(n), g)

    def label(self, tid):
        t = self.tags.get(tid)
        return t.get("label", tid) if t else tid

    def match_text(self, text) -> list:
        """글자 안에서 태그 id 를 찾는다(정렬된 목록)."""
        s = str(text or "").lower()
        return sorted(tid for tid, rx in self._rx.items() if rx and rx.search(s))

    def known_game(self, appid=None, name=None):
        if appid is not None and str(appid) in self._by_appid:
            return self._by_appid[str(appid)]
        if name:
            return self._by_name.get(norm_name(name))
        return None

    def resolve(self, word) -> str | None:
        """사용자가 적은 태그 글자를 태그 id 로 바꾼다. 사전에 없으면 None."""
        w = str(word).strip().lower()
        if w in self.tags:
            return w
        for tid, t in self.tags.items():
            if w == str(t.get("label", "")).lower() or w in [str(a).lower() for a in t.get("aliases", [])]:
                return tid
        return None
