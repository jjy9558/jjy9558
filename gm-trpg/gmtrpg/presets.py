"""월드 프리셋."""
import json

PRESETS = {
    "판타지 던전": {
        "setting": "고대 왕국의 지하에 가라앉은 미궁 '잿빛 회랑'. 마을 사람들은 이곳의 유물을 찾아 모험가를 보낸다.",
        "tone": "모험적이고 따뜻한 하이 판타지. 위험은 있으나 잔혹 묘사는 피한다.",
        "rules": "판정은 d20+보정 vs 난이도. HP가 0이 되면 기절하며 마을에서 깨어난다(사망 없음).",
        "player_name": "모험가", "hp": 24, "stats": {"전투": 2, "탐색": 1, "설득": 0, "은신": 1},
        "start": "달빛 마을 여관",
        "locations": [("달빛 마을 여관", "난로가 따뜻한 모험가들의 쉼터", ["달빛 마을 광장"]),
                      ("달빛 마을 광장", "분수대가 있는 마을 중심", ["잿빛 회랑 입구"]),
                      ("잿빛 회랑 입구", "이끼 낀 거대한 석문", [])],
        "npcs": [("여관 주인 마리", "수다스럽고 인정 많은 중년 여성", "단골 손님", "달빛 마을 여관")],
        "items": [("낡은 단검", 1), ("횃불", 3), ("빵", 2)],
        "quests": [("잿빛 회랑의 첫 유물", "회랑 1층에서 빛나는 유물을 찾아 마리에게 보여주기")],
        "facts": ["마리는 십 년 전 회랑에서 돌아오지 못한 남편을 기다리고 있다."],
    },
    "사이버펑크 서울 2087": {
        "setting": "2087년 네오 서울. 거대 기업 '한울'이 하늘을 가리고, 청계천 지하 시장에는 해커와 밀수꾼이 모인다.",
        "tone": "네온이 번지는 느와르. 도덕적 회색지대를 다루되 전연령 수준을 유지한다.",
        "rules": "판정은 d20+보정 vs 난이도. 해킹과 잠입이 전투보다 유리하다. HP 0은 체포/기절로 처리.",
        "player_name": "러너", "hp": 18, "stats": {"해킹": 2, "사격": 1, "설득": 1, "잠입": 1},
        "start": "청계천 지하 시장",
        "locations": [("청계천 지하 시장", "홀로그램 간판이 빛나는 암시장", ["한울 타워 로비", "러너의 은신처"]),
                      ("한울 타워 로비", "보안 드론이 순찰하는 유리 로비", []),
                      ("러너의 은신처", "허름한 컨테이너 방", [])],
        "npcs": [("정보상 오빠", "말끝마다 농담을 던지는 정보 브로커", "믿을 만한 거래 상대", "청계천 지하 시장")],
        "items": [("해킹 데크", 1), ("크레딧 칩", 3)],
        "quests": [("사라진 데이터 칩", "한울 타워에서 유출된 데이터 칩의 행방을 추적하기")],
        "facts": ["정보상 오빠는 한울의 전직 직원이었다는 소문이 있다."],
    },
    "조선 괴담": {
        "setting": "조선 후기 한양 외곽의 고을. 밤이면 도깨비와 처녀귀신 이야기가 떠돌고, 플레이어는 괴이를 조사하는 젊은 서생이다.",
        "tone": "으스스하지만 과도한 공포 묘사는 피하는 전통 괴담. 해학과 인정이 섞여 있다.",
        "rules": "판정은 d20+보정 vs 난이도. 귀신은 원한을 풀어 주면 물러난다. HP 0이면 정신을 잃고 아침에 깨어난다.",
        "player_name": "서생", "hp": 16, "stats": {"학식": 2, "담력": 0, "설득": 1, "관찰": 1},
        "start": "버드나무 주막",
        "locations": [("버드나무 주막", "고을 어귀의 허름한 주막", ["고을 장터", "서낭당 고개"]),
                      ("고을 장터", "낮에는 북적이는 장터", []),
                      ("서낭당 고개", "밤마다 불빛이 보인다는 고개", [])],
        "npcs": [("주모 억순", "입담 좋고 겁 없는 주모", "친절한 집주인", "버드나무 주막")],
        "items": [("부적", 1), ("붓과 먹", 1), ("엽전", 5)],
        "quests": [("고개의 불빛", "서낭당 고개에 나타나는 불빛의 정체 알아내기")],
        "facts": ["억순은 고개의 불빛을 본 이후 손님이 줄었다고 투덜댄다."],
    },
}


def apply_preset(world, preset, name=None):
    p = PRESETS[preset] if isinstance(preset, str) else preset
    world.set_bible(name=name or (preset if isinstance(preset, str) else None), setting=p["setting"],
                    tone=p["tone"], rules=p["rules"], player_name=p["player_name"])
    world.set("hp", p["hp"])
    world.set("max_hp", p["hp"])
    world.set("stats", json.dumps(p.get("stats", {}), ensure_ascii=False))
    for loc, desc, links in p["locations"]:
        world.add_location(loc, desc, links)
    world.set("location", p["start"])
    if p["start"] != "시작 지점":
        world.db.execute("DELETE FROM locations WHERE name='시작 지점'")
    for n, t, r, l in p["npcs"]:
        world.upsert_npc(n, t, r, l)
    for n, q in p["items"]:
        world.add_item(n, q)
    for t, d in p["quests"]:
        world.add_quest(t, d)
    for f in p["facts"]:
        world.add_fact(f)
    world.commit()
