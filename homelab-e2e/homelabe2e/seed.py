"""각 프로젝트 '자기 CLI' 로 그럴듯한 생활 데이터를 심는다 (DB/JSON 을 손으로 쓰지 않음).

모두 Stack 의 임시 HOME 과 프로젝트별 환경변수(…_HOME) 아래에만 쓴다.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .harness import Stack

# steam-buddy 의 buy 는 --price/--name 을 줘도 스토어 API(app_details)를 먼저 부른다.
# 외부 네트워크 금지라서 steam-buddy 가 문서화한 주입점(SteamClient(fetch=...))으로 '오프라인' 을 넣고
# 그 프로젝트의 CLI main 을 그대로 실행한다. (FINDINGS.md F-4 참고)
STEAM_OFFLINE = (
    "import sys\n"
    "from steam_buddy.cli import App, main\n"
    "from steam_buddy.steam import SteamClient, SteamError\n"
    "def offline(url, params):\n"
    "    raise SteamError('e2e: 외부 네트워크 없음 (%s)' % url)\n"
    "raise SystemExit(main(sys.argv[1:], app=App(client=SteamClient(fetch=offline))))\n"
)


class Seeder:
    def __init__(self, stack: Stack):
        self.st = stack
        self.pet_home = stack.path("decision-pet/.keep").parent
        self.cool_home = stack.path("cool-off/.keep").parent
        self.steam_home = stack.path("steam-buddy/.keep").parent
        self.pb_home = stack.path("paperbutler/.keep").parent
        self.vj_db = stack.path("vjournal/journal.db")
        self.nagger_state = stack.home / ".life-nagger"          # life-nagger 문서화된 기본 위치
        stack.extra_env.update({"DECISION_PET_HOME": str(self.pet_home), "COOLOFF_HOME": str(self.cool_home),
                                "STEAM_BUDDY_HOME": str(self.steam_home), "PAPERBUTLER_HOME": str(self.pb_home)})

    # -- life-nagger --
    def nagger_counts(self, sigh=5, pushup=20):
        nag_cfg = self.st.write_json("life-nagger/config.json", {"outputs": {"toast": {"enabled": False}}})
        self.st.run("life-nagger", ["--config", nag_cfg, "count", "sigh", "-n", str(sigh)])
        self.st.run("life-nagger", ["--config", nag_cfg, "count", "pushup", "-n", str(pushup)])
        return self.nagger_state

    # -- voice-journal: 선언했다가 접기 --
    def vjournal_quit(self):
        self.st.run("voice-journal", ["--db", self.vj_db, "add", "이번 주에 블로그 2개 쓴다"])
        p = self.st.run("voice-journal", ["--db", self.vj_db, "add", "블로그는 접었다. 요즘 너무 바빠서"])
        return p.stdout

    # -- decision-pet: 미루고 미뤄서 묘지로 + voice-journal 포기 선언 가져오기 --
    def pet_graveyard(self, titles=("블로그 초안 다듬기", "책상 정리")):
        ids = []
        for t in titles:
            out = self.st.run("decision-pet", ["add", t, "--effort", "15"]).stdout
            ids.append(int(out.split("#", 1)[1].split(":", 1)[0]))
        for i in ids:
            for _ in range(5):                                   # max_skips 기본 5
                self.st.run("decision-pet", ["skip", str(i)])
        if not self.vj_db.exists():
            self.vjournal_quit()
        self.st.run("decision-pet", ["import-vjournal", self.vj_db])
        return self.pet_home

    # -- steam-buddy → cool-off --
    def steam_games(self, days_ago=20):
        when = (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()
        games = [("9000001", "충동 로그라이크", 21000, 1), ("9000002", "세일 RPG", 33000, 2),
                 ("9000003", "번들 퍼즐", 5000, None)]
        for appid, name, price, rating in games:
            self.st.run("steam-buddy", ["buy", appid, "--name", name, "--price", str(price), "--date", when],
                        code=STEAM_OFFLINE)
            if rating:
                self.st.run("steam-buddy", ["rate", appid, str(rating)], code=STEAM_OFFLINE)
        return self.steam_home / "data.json"

    def cooloff_from_steam(self):
        self.st.run("cool-off", ["import-steam"])
        return self.cool_home / "data.json"

    def cooloff_want(self, name="엘든링 DLC", price=45000):
        self.st.run("cool-off", ["want", name, "--price", str(price), "--why", "친구가 하길래"])

    # -- paper-butler 결제 문자 --
    def paper_payments(self):
        today = dt.date.today()
        sms = ["[Web발신]\n신한카드 승인 홍*동\n38,000원 일시불\n%s 18:47\n교보문고 광화문점\n" % today.strftime("%m/%d"),
               "[Web발신]\n삼성카드 승인 홍*동\n12,340원 일시불\n%s 09:15\n스타벅스 강남점\n" % today.strftime("%m/%d")]
        for s in sms:
            self.st.run("paper-butler", ["add", "-", "--type", "card", "--no-llm", "--today", today.isoformat()],
                        input=s)
        return self.pb_home / "paperbutler.db"
