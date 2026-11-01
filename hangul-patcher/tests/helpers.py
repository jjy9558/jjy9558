"""테스트 공용: 합성 게임 픽스처와 가짜 LLM. 실제 게임 콘텐츠는 전혀 사용하지 않는다."""

import json
import os
import re
from pathlib import Path

from hpatch.llm import LLMUnreachable


def wjson(path: Path, data, pretty=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    else:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    path.write_bytes(text.encode("utf-8"))


def cmd(code, params, indent=0):
    return {"code": code, "indent": indent, "parameters": params}


def snapshot(root: Path):
    """폴더 전체의 {상대경로: 바이트} (백업/작업 폴더 제외 옵션 없음)."""
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = Path(dp) / fn
            out[p.relative_to(root).as_posix()] = p.read_bytes()
    return out


def map_events(mz=False):
    face = ["Actor1", 0, 0, 2]
    return [
        cmd(101, face + (["Alice"] if mz else [])),
        cmd(401, ["Hello \\C[2]traveler\\C[0], welcome to \\N[1]'s town!"]),
        cmd(401, ["You have \\V[3] gold. \\{Big\\} \\. \\| \\! \\> \\< \\^"]),
        cmd(102, [["Yes", "No thanks", "100"], 1, 0, 2, 0]),
        cmd(402, [0, "Yes"], 1),
        cmd(401, ["Great!"], 1),
        cmd(0, [], 1),
        cmd(402, [1, "No thanks"], 1),
        cmd(0, [], 1),
        cmd(404, []),
        cmd(105, [2, False]),
        cmd(405, ["The story begins"]),
        cmd(405, ["in a quiet village."]),
        cmd(111, [1, 5, 0]),  # 무관한 명령 (정수 파라미터)
        cmd(355, ["$gameVariables.setValue(1, 'Do not touch')"]),
        cmd(108, ["comment: Do not translate"]),
        cmd(0, []),
    ]


def make_rpgmaker(root: Path, mz=False):
    base = root if mz else root / "www"
    data = base / "data"
    (base / "js").mkdir(parents=True, exist_ok=True)
    (base / "js" / ("rmmz_core.js" if mz else "rpg_core.js")).write_text("// stub\n")
    wjson(data / "System.json", {
        "gameTitle": "Test Quest",
        "currencyUnit": "G",
        "locale": "ja_JP",
        "switches": ["", "Editor Only Switch"],
        "variables": ["", "Editor Only Var"],
        "armorTypes": ["", "General Armor"],
        "elements": ["", "Physical", "Fire"],
        "equipTypes": ["", "Weapon"],
        "skillTypes": ["", "Magic"],
        "weaponTypes": ["", "Sword"],
        "terms": {
            "basic": ["Level", "Lv", "HP", "HP", "MP", "MP", "TP", "TP", "EXP", "EXP"],
            "commands": ["Fight", "Escape", None, "Attack", "Guard", "Item"],
            "params": ["Max HP", "Max MP"],
            "messages": {"actionFailure": "There was no effect on %1!", "levelUp": "%1 is now %2 %3!", "possession": "Possession"},
        },
        "startMapId": 1,
        **({"advanced": {"gameId": 1234, "mainFontFilename": "mplus-1m-regular.ttf",
                         "numberFontFilename": "mplus-2p-bold-sub.ttf", "fontSize": 26}} if mz else {}),
    })
    wjson(data / "MapInfos.json", [None, {"id": 1, "name": "Editor Name", "parentId": 0}])
    wjson(data / "Actors.json", [None, {"id": 1, "name": "Alice", "nickname": "Brave", "profile": "A young hero.\nLikes tea.",
                                        "classId": 1, "initialLevel": 1, "note": "keep me"}])
    wjson(data / "Classes.json", [None, {"id": 1, "name": "Hero", "params": [[1, 2], [3, 4]], "note": ""}])
    wjson(data / "Items.json", [None, {"id": 1, "name": "Potion", "description": "Restores 100 HP.", "price": 50, "note": "<tag>"}])
    wjson(data / "Weapons.json", [None, {"id": 1, "name": "Iron Sword", "description": "A plain sword.", "price": 100}])
    wjson(data / "Armors.json", [None, {"id": 1, "name": "Leather Armor", "description": "Light armor.", "price": 80}])
    wjson(data / "Skills.json", [None, {"id": 1, "name": "Fireball", "description": "Burns the enemy.",
                                        "message1": "%1 casts Fireball!", "message2": "", "stypeId": 1}])
    wjson(data / "States.json", [None, {"id": 1, "name": "Poison", "message1": "%1 is poisoned!", "message2": "%1 is poisoned!",
                                        "message3": "", "message4": "%1 is cured.", "iconIndex": 4}])
    wjson(data / "Enemies.json", [None, {"id": 1, "name": "Slime", "battlerName": "Slime", "exp": 5, "params": [10, 0, 5, 5, 1, 1, 1, 1]}])
    wjson(data / "CommonEvents.json", [None, {"id": 1, "name": "Common 1", "trigger": 0, "switchId": 1,
                                              "list": [cmd(101, ["", 0, 0, 2] + ([""] if mz else [])),
                                                       cmd(401, ["Common event text."]), cmd(0, [])]}])
    wjson(data / "Troops.json", [None, {"id": 1, "name": "Slime x2", "members": [{"enemyId": 1, "x": 1, "y": 2, "hidden": False}],
                                        "pages": [{"conditions": {"turnEnding": False}, "span": 0,
                                                   "list": [cmd(101, ["", 0, 0, 2] + ([""] if mz else [])),
                                                            cmd(401, ["The slime wobbles."]), cmd(0, [])]}]}])
    wjson(data / "Map001.json", {
        "displayName": "Starting Town", "width": 2, "height": 2, "data": [1, 2, 3, 4], "note": "",
        "events": [None, {"id": 1, "name": "EV001", "x": 1, "y": 1, "note": "",
                          "pages": [{"conditions": {"switch1Valid": False}, "list": map_events(mz)}]}],
    })
    fonts = base / "fonts"
    fonts.mkdir(exist_ok=True)
    (fonts / "gamefont.css").write_text(
        '@font-face {\n    font-family: GameFont;\n    src: url("mplus-1m-regular.ttf");\n}\n', encoding="utf-8")
    (fonts / "mplus-1m-regular.ttf").write_bytes(b"fake-original-font")
    return root


RPY_SCRIPT = r'''# synthetic test script
define e = Character("Eileen", color="#c8ffc8")
define n = Character(None)
define mystery = Character('Mystery "Man"')
default points = 0

init python:
    def helper():
        return "not dialogue"
    say_me = "python string"

screen hud():
    text "Screen text should be skipped"
    textbutton "Skip button" action NullAction()

label start:
    scene bg room
    show eileen happy
    "Narration with [player_name] and {i}tags{/i}."
    e "Hello, [player_name]! You owe me 100%% of \"honor\"."
    e happy "Attributes line" with dissolve
    "Mystery" "String speaker line."
    n "Narrator via variable"
    $ points += 1
    menu:
        "Which way?"
        "Go left" if points > 0:
            e "Left it is."
            jump left
        "Go \"right\"":
            e 'Right it is, it\'s fine.'
            jump right
        "Stay":
            pass
    return

label left:
    e "Duplicate line."
    e "Duplicate line."
    python:
        x = "also not dialogue"
    return

label .local:
    e "Local label line."
    return
'''

RPY_SCRIPT2 = r'''label other:
    e "Another file line."
    menu:
        "Go left" if True:
            e "Again left."
        "Brand new choice":
            pass
    return
'''


def make_renpy(root: Path, with_rpy=True):
    g = root / "game"
    g.mkdir(parents=True, exist_ok=True)
    (root / "renpy").mkdir(exist_ok=True)
    if with_rpy:
        (g / "script.rpy").write_text(RPY_SCRIPT, encoding="utf-8")
        (g / "chapter2.rpy").write_text(RPY_SCRIPT2, encoding="utf-8")
    else:
        (g / "script.rpyc").write_bytes(b"RENPY RPC2 stub")
        (g / "archive.rpa").write_bytes(b"RPA-3.0 stub")
    return root


class FakeLLM:
    """Items JSON 을 읽어 "KO:" + 원문(플레이스홀더 유지) 로 답한다. mode 로 오작동을 흉내낸다."""

    def __init__(self, mode="good", fail_after=None):
        self.mode = mode
        self.calls = []
        self.fail_after = fail_after

    def complete(self, messages):
        self.calls.append(messages)
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise LLMUnreachable("fake: connection refused")
        user = messages[-1]["content"]
        items = json.loads(user.split("Items:\n", 1)[1])
        out = []
        for it in items:
            text = "KO:" + it["text"]
            if self.mode == "drop_placeholder_in_batch" and len(items) > 1 and re.search(r"<\d+>", it["text"]):
                text = "KO:" + re.sub(r"<\d+>", "", it["text"])
            if self.mode == "always_drop" and re.search(r"<\d+>", it["text"]):
                text = "KO:" + re.sub(r"<\d+>", "", it["text"])
            out.append({"id": it["id"], "text": text})
        if self.mode == "malformed":
            return "Sure! Here you go:\n```json\n" + json.dumps(out, ensure_ascii=False) + "\n```\nHope it helps"
        if self.mode == "skip_second" and len(out) > 1:
            out = out[:1] + out[2:]
        return json.dumps(out, ensure_ascii=False)
