"""구매 기록을 JSON 파일 하나에 저장한다. 위치는 STEAM_BUDDY_HOME 또는 ~/.steam-buddy."""

import json
import os
from pathlib import Path


def data_dir():
    return Path(os.environ.get("STEAM_BUDDY_HOME") or Path.home() / ".steam-buddy")


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_dir() / "data.json"
        self.data = {"games": {}, "config": {}}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.setdefault("games", {})
            self.data.setdefault("config", {})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def games(self):
        return self.data["games"]

    @property
    def config(self):
        return self.data["config"]

    def get(self, appid):
        return self.games.get(str(appid))

    def put(self, game):
        self.games[str(game["appid"])] = game

    def remove(self, appid):
        return self.games.pop(str(appid), None)

    def find(self, query):
        """appid 또는 이름 일부로 저장된 게임을 찾는다. 여러 개면 전부 돌려준다."""
        q = str(query).strip()
        if q.isdigit() and q in self.games:
            return [self.games[q]]
        q = q.lower()
        return [g for g in self.games.values() if q in g["name"].lower()]
