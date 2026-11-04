"""시나리오 1: voice-journal API 일기 → morning-brief run → 계약 파일 2개 → phone-home /desk 카드.

덤: morning-brief --speak 가 진짜 s21-sidekick /api/say 로 낭독문을 넣는지도 본다.
"""
import base64
import html
import json
import secrets
import unittest

from homelabe2e import services as S
from homelabe2e.case import E2ECase
from homelabe2e.harness import FakeLLM, http, http_json, import_from


class JournalToBriefToDesk(E2ECase):
    needs = ("voice-journal", "morning-brief", "phone-home", "s21-sidekick")

    def test_entry_api_brief_contract_desk(self):
        st = self.stack
        llm = st.fake_llm()                                   # VJ_LLM_* 는 가짜 로컬 LLM 으로
        db = st.path("vjournal/journal.db")
        vj = S.voice_journal(st, db, llm.url)
        hdr = {"X-VJ-Token": vj.token}

        # 토큰 없이/틀린 토큰은 막힘, /health 는 열림 (문서화된 계약)
        self.assertEqual(http_json("POST", vj.url + "/entry", {"text": "x"})[0], 401)
        self.assertEqual(http_json("POST", vj.url + "/entry", {"text": "x"}, {"X-VJ-Token": "wrong-token"})[0], 401)
        self.assertEqual(http_json("GET", vj.url + "/health")[0], 200)

        code, r = http_json("POST", vj.url + "/entry", {"text": "세금 서류 챙기기 #중요", "flag": True}, hdr)
        self.assertEqual(code, 201, r)
        code, r = http_json("POST", vj.url + "/entry", {"text": "내일까지 발표 자료 끝낸다", "mood": 3},
                            {"Authorization": "Bearer " + vj.token})
        self.assertEqual(code, 201, r)
        # text/plain 본문도 문서화돼 있음
        code, _, raw = http("POST", vj.url + "/entry", "산책 다녀옴 #일상".encode("utf-8"),
                            dict(hdr, **{"Content-Type": "text/plain; charset=utf-8"}))
        self.assertEqual(code, 201, raw)
        code, m = http_json("GET", vj.url + "/morning", headers=hdr)
        self.assertEqual(code, 200, m)

        sk = S.sidekick(st)
        cfg = self.mbrief_config(vjournal_db=db, sidekick=sk)
        out = cfg.parent / "out"
        p = self.run_cli("morning-brief", ["run", "--config", cfg, "--date", self.today.isoformat(), "--speak"],
                         timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

        brief = self.load_json(out / "brief-latest.json")
        cards = self.load_json(out / "cards-latest.json")
        contract = import_from("morning-brief", "mbrief.contract")
        self.assertEqual(contract.validate_brief(brief), [])
        self.assertEqual(contract.validate_cards(cards), [])
        self.assertEqual(brief["date"], self.today.isoformat())
        self.assertEqual(brief["sources"]["voice_journal"]["status"], "ok", brief["sources"])
        kinds = [c["kind"] for c in cards]
        self.assertIn("flag", kinds)                         # --flag / #중요 메모
        self.assertIn("goal-due", kinds)                     # '내일까지 ... 끝낸다' 선언
        self.assertTrue(any("세금 서류" in c["detail"] or "세금 서류" in c["title"] for c in cards), cards)
        # 자기 검사 명령도 통과
        self.assertEqual(self.run_cli("morning-brief", ["check", "--config", cfg, "--out", out]).returncode, 0)

        # --speak: 진짜 sidekick 이벤트 큐에 낭독문
        ev = S.wait_sidekick_say(sk, lambda e: e.get("module") == "morning-brief")
        self.assertTrue(brief["speak_text"].startswith(ev["text"][:20]), (ev["text"], brief["speak_text"]))

        # phone-home 사장님 책상이 같은 파일을 읽는다
        ph = S.phone_home(st, {"sidekick": {"url": sk.url, "token": sk.token}}, out / "cards-latest.json")
        code, _, page = http("GET", ph.url + "/desk")
        self.assertEqual(code, 401)                          # 토큰 없으면 로그인 화면
        code, _, page = http("GET", ph.url + "/desk", headers={"X-Phone-Token": ph.token})
        self.assertEqual(code, 200)
        text = page.decode("utf-8")
        for c in cards:
            self.assertIn(html.escape(c["title"][:120], quote=False)[:40], text.replace("&#x27;", "'"))
        code, snap = http_json("GET", ph.url + "/desk/api/items", headers={"X-Phone-Token": ph.token})
        self.assertEqual(code, 200, snap)
        self.assertEqual(snap["cards_status"], "ok")
        self.assertEqual([c["title"] for c in snap["cards"]], [c["title"][:120] for c in cards])
        # 확인 도장은 desk-log 에만 남고 카드 파일은 그대로
        before = (out / "cards-latest.json").read_bytes()
        code, r = http_json("POST", ph.url + "/desk/api/stamp", {"key": snap["cards"][0]["key"], "stamp": "ok"},
                            {"X-Phone-Token": ph.token})
        self.assertEqual(code, 200, r)
        self.assertEqual((out / "cards-latest.json").read_bytes(), before)

    def test_phone_quick_entry_through_proxy(self):
        """phone-home 의 voice-journal 타일(한 줄 일기)은 /s/voice-journal/entry 로 프록시되고
        서버가 X-VJ-Token 을 붙인다."""
        st = self.stack
        db = st.path("vjournal/journal.db")
        vj = S.voice_journal(st, db)
        ph = S.phone_home(st, {"voice-journal": {"url": vj.url, "token": vj.token}}, st.path("cards.json"))
        H = {"X-Phone-Token": ph.token}
        code, tiles = http_json("GET", ph.url + "/api/tiles", headers=H)
        tile = [t for t in tiles["tiles"] if t["name"] == "voice-journal"][0]
        self.assertEqual(tile["quick"]["path"], "/entry")
        code, r = http_json("POST", ph.url + "/s/voice-journal" + tile["quick"]["path"],
                            {tile["quick"]["field"]: "폰에서 한 줄 #폰"}, H)
        self.assertEqual(code, 201, r)
        code, found = http_json("GET", vj.url + "/search?q=" + "%ED%8F%B0%EC%97%90%EC%84%9C",
                                headers={"X-VJ-Token": vj.token})
        self.assertTrue(any("폰에서 한 줄" in x["text"] for x in found["results"]), found)
        code, h = http_json("GET", ph.url + "/api/health", headers=H)
        self.assertEqual(h["services"]["voice-journal"]["state"], "up")


class AlarmGateReadsBrief(E2ECase):
    """계약 1: s21-sidekick 알람 게이트(morning_gate)가 morning-brief 의 brief-latest.json speak_text 를
    승인 때만 읽는다. 사진 판정 VLM 은 가짜 로컬 서버."""
    needs = ("voice-journal", "morning-brief", "s21-sidekick")

    def setUp(self):
        super().setUp()
        st = self.stack
        db = st.path("vjournal/journal.db")
        self.run_cli("voice-journal", ["--db", db, "add", "오늘까지 보고서 끝낸다"])
        cfg = self.mbrief_config(vjournal_db=db)
        self.run_cli("morning-brief", ["run", "--config", cfg, "--date", self.today.isoformat()])
        self.brief_path = cfg.parent / "out" / "brief-latest.json"
        self.brief = self.load_json(self.brief_path)
        self.verdict = {"approved": True, "reason": "세면대 앞"}
        self.vlm = st.fake_llm(lambda body: json.dumps(self.verdict, ensure_ascii=False))
        self.sk = S.sidekick(st, {
            "vision": {"base_url": self.vlm.url, "model": "fake", "timeout": 10},
            "alarm": {"enabled": True, "emergency_code": "%06d" % (secrets.randbelow(900000) + 100000),
                      "morning_gate": {"enabled": True, "brief_path": str(self.brief_path), "speak_reject": True}}})
        self.H = {"X-Sidekick-Token": self.sk.token}

    def ring_and_check(self):
        self.assertEqual(http_json("POST", self.sk.url + "/api/alarm/test", {}, self.H)[0], 200)
        img = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8fake\xff\xd9").decode("ascii")
        code, r = http_json("POST", self.sk.url + "/api/alarm/check", {"image": img}, self.H, timeout=20)
        self.assertEqual(code, 200, r)
        return r

    def test_approved_reads_speak_text(self):
        r = self.ring_and_check()
        self.assertEqual(r["verdict"], "approved", r)
        self.assertEqual(r.get("brief"), "ok", r)
        self.assertIn(self.brief["speak_text"][:30], r["say"])
        self.assertTrue(any(FakeLLM.has_image(q["body"]) for q in self.vlm.requests))

    def test_rejected_never_reads_speak_text(self):
        self.verdict = {"approved": False, "reason": "아직 누워 있음"}
        r = self.ring_and_check()
        self.assertEqual(r["verdict"], "rejected", r)
        self.assertNotIn(self.brief["speak_text"][:30], r["say"])
        self.assertIn(self.brief["reject_text"], r["say"])


if __name__ == "__main__":
    unittest.main()
