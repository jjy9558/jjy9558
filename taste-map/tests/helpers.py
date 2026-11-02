"""가짜 입력 파일 만들기: steam-buddy data.json, game-diary sqlite, 테이크아웃 watch-history.json."""
import json
import sqlite3
from pathlib import Path

STEAM_GAMES = {
    # appid: (이름, 구매일, 가격, 분, 만족도)
    1145360: ("Hades", "2026-01-10T12:00:00", 25000, 80 * 60, 5),
    646570: ("Slay the Spire", "2026-02-03T12:00:00", 15000, 60 * 60, 5),
    2379780: ("Balatro", "2026-03-05T12:00:00", 16000, 40 * 60, 4),
    413150: ("Stardew Valley", "2025-12-20T12:00:00", 17000, 50 * 60, 4),
    1245620: ("Elden Ring", "2026-04-01T12:00:00", 60000, 120 * 60, 5),
    9100: ("Soccer Season", "2026-05-01T12:00:00", 40000, 3 * 60, 1),
    999001: ("Mystery Game", "2026-01-02T12:00:00", 20000, 0, None),
}


def make_steam(dirpath, with_compat=False, refunded=True):
    games = {}
    for appid, (name, when, price, minutes, rating) in STEAM_GAMES.items():
        g = {"appid": appid, "name": name, "purchased_at": when, "price_paid": price, "minutes": minutes,
             "source": "직접입력"}
        if rating:
            g["rating"] = rating
        games[str(appid)] = g
    if refunded:
        games["555"] = {"appid": 555, "name": "Refunded One", "purchased_at": "2026-06-01T00:00:00",
                        "price_paid": 10000, "minutes": 30, "refunded": True}
    data = {"games": games, "config": {"key": "k", "known_appids": [1145360]}}
    p = Path(dirpath) / "data.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if with_compat:
        (Path(dirpath) / "compat.json").write_text(json.dumps({"games": {
            "7002": {"name": "Deck Hater", "deck": "unsupported", "anticheat": {"kind": "EasyAntiCheat"}},
            "1": {"example": True, "name": "ex", "deck": "verified"}}}), encoding="utf-8")
    return p


def make_diary(dirpath):
    p = Path(dirpath) / "diary.sqlite3"
    con = sqlite3.connect(p)
    con.executescript("""
    CREATE TABLE files(path TEXT PRIMARY KEY, hash TEXT, bytes INTEGER, mtime REAL, game TEXT, appid TEXT,
      ts TEXT, width INTEGER, height INTEGER, thumb TEXT, skip_reason TEXT);
    CREATE TABLE captions(hash TEXT PRIMARY KEY, caption TEXT, mood TEXT, tags TEXT, notable INTEGER,
      model TEXT, created TEXT);""")
    shots = [("Hades", "1145360", "h1", '["boss fight", "roguelike", "dark"]', "tense", 1),
             ("Hades", "1145360", "h2", '["boss fight", "roguelike"]', "tense", 0),
             ("Stardew Valley", "413150", "s1", '["farm", "cozy"]', "peaceful", 0),
             ("Dave the Diver", "1868140", "d1", '["cozy", "pixel art"]', "peaceful", 1),
             ("Hades", "1145360", None, None, None, 0)]
    for i, (game, appid, h, tags, mood, notable) in enumerate(shots):
        con.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (f"C:/Users/secretname/shot{i}.png", h, 1000, 1.0, game, appid, "2026-03-0%dT10:00:00" % (i + 1),
                     1920, 1080, "", None))
        if h:
            con.execute("INSERT INTO captions VALUES(?,?,?,?,?,?,?)",
                        (h, "채팅창에 Kim_Minsu 가 보임", mood, tags, notable, "m", "2026-03-01T00:00:00"))
    con.execute("INSERT INTO files VALUES('C:/big.png','hb',9,1,'Big','1',?,1,1,'', 'too big')", ("2026-03-09T00:00:00",))
    con.commit()
    con.close()
    return p


def make_takeout(dirpath, n_extra=0):
    items = [
        {"header": "YouTube", "title": "Watched Hades 공략 가이드", "titleUrl": "https://www.youtube.com/watch?v=aaa111",
         "subtitles": [{"name": "SecretChannel", "url": "https://www.youtube.com/channel/UCsecret"}],
         "time": "2026-02-01T10:00:00.000Z", "products": ["YouTube"]},
        {"header": "YouTube", "title": "Watched Hades 공략 가이드", "titleUrl": "https://www.youtube.com/watch?v=aaa111",
         "subtitles": [{"name": "SecretChannel", "url": "https://www.youtube.com/channel/UCsecret"}],
         "time": "2026-02-02T10:00:00.000Z", "products": ["YouTube"]},
        {"header": "YouTube", "title": "Watched Slay the Spire roguelike tips", "titleUrl": "https://www.youtube.com/watch?v=bbb222",
         "time": "2026-02-03T10:00:00.000Z"},
        {"header": "YouTube", "title": "Watched Lofi playlist mail me at someone@example.com",
         "titleUrl": "https://youtu.be/ccc333", "time": "2025-12-30T10:00:00.000Z"},
        {"header": "YouTube", "title": "Watched Some Ad", "titleUrl": "https://www.youtube.com/watch?v=ad1",
         "details": [{"name": "From Google Ads"}], "time": "2026-02-04T10:00:00.000Z"},
        {"header": "YouTube", "title": "Watched https://www.youtube.com/watch?v=zzz999",
         "titleUrl": "https://www.youtube.com/watch?v=zzz999", "time": "2026-02-05T10:00:00.000Z"},
    ]
    for i in range(n_extra):
        items.append({"title": f"Watched Soccer highlights {i}", "titleUrl": f"https://www.youtube.com/watch?v=x{i}",
                      "time": "2026-02-06T10:00:00.000Z"})
    p = Path(dirpath) / "watch-history.json"
    p.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return p
