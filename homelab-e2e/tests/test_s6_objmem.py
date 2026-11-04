"""시나리오 6: object-memory 에 합성 프레임 업로드(가짜 VLM 이 물건 목록을 답함)
→ s21-sidekick 출발 체크의 objmem_items provider 가 '지갑: 오늘 HH:MM 책상 위에서 봤어요' 를 말한다.
"""
import json
import secrets
import unittest
import urllib.parse

from homelabe2e import services as S
from homelabe2e.case import E2ECase
from homelabe2e.harness import FakeLLM, http, http_json


def synthetic_jpeg() -> bytes:
    """SOI/EOI 마커만 맞춘 합성 JPEG 바이트 (object-memory 는 디코딩하지 않는다)."""
    return b"\xff\xd8\xff\xe0" + secrets.token_bytes(256) + b"\xff\xd9"


def vlm(body: dict) -> str:
    if FakeLLM.has_image(body):
        return "```json\n" + json.dumps({"objects": [
            {"name": "지갑", "where": "책상 위", "confidence": 0.9},
            {"name": "리모컨", "where": "소파 왼쪽 쿠션 위", "confidence": "80%"}],
            "scene": "책상과 소파가 보이는 방"}, ensure_ascii=False) + "\n```"
    return "알 수 없음"


class ObjectMemoryToDeparture(E2ECase):
    needs = ("object-memory", "s21-sidekick")

    def test_frame_then_departure_says_item(self):
        st = self.stack
        llm = st.fake_llm(vlm)
        om = S.object_memory(st, llm.url)
        H = {"X-ObjMem-Token": om.token, "Content-Type": "image/jpeg"}
        self.assertEqual(http("POST", om.url + "/api/frame?camera=" + urllib.parse.quote("거실"), synthetic_jpeg(),
                              {"Content-Type": "image/jpeg"})[0], 401)
        code, r = http_json("POST", om.url + "/api/frame?camera=" + urllib.parse.quote("거실"), synthetic_jpeg(), H,
                            timeout=20)
        self.assertEqual(code, 200, r)
        self.assertEqual(r["status"], "stored", r)
        self.assertTrue(r.get("described"), r)
        self.assertTrue(any(FakeLLM.has_image(q["body"]) for q in llm.requests))
        code, ans = http_json("GET", om.url + "/api/ask?" + urllib.parse.urlencode({"q": "지갑 어디 있어", "llm": "0"}),
                              headers={"X-ObjMem-Token": om.token})
        self.assertEqual(code, 200, ans)
        self.assertTrue(ans["found"], ans)
        self.assertEqual(ans["sightings"][0]["where"], "책상 위")

        sk = S.sidekick(st, {"departure": {"providers": [
            {"name": "objmem_items", "enabled": True, "url": om.url, "token": om.token,
             "items": ["지갑", "열쇠"], "stale_hours": 24, "timeout": 5}]}})
        code, out = http_json("POST", sk.url + "/api/departure", {"source": "button"},
                              {"X-Sidekick-Token": sk.token}, timeout=20)
        self.assertEqual(code, 200, out)
        say = out["say"]
        self.assertIn("물건 기억 - 지갑: 오늘", say)
        self.assertIn("책상 위에서 봤어요", say)
        self.assertIn("열쇠: 모름", say)                 # 본 적 없는 물건은 거짓 안심 대신 '모름'
        S.wait_sidekick_say(sk, lambda e: e.get("module") == "departure" and "지갑" in e["text"])

    def test_objmem_down_means_provider_silently_skipped(self):
        st = self.stack
        from homelabe2e.harness import free_port
        sk = S.sidekick(st, {"departure": {"providers": [
            {"name": "reminders", "items": ["휴대폰 충전기"]},
            {"name": "objmem_items", "enabled": True, "url": "http://127.0.0.1:%d" % free_port(),
             "token": "x" * 24, "items": ["지갑"], "timeout": 2}]}})
        code, out = http_json("POST", sk.url + "/api/departure", {"source": "button"},
                              {"X-Sidekick-Token": sk.token}, timeout=20)
        self.assertEqual(code, 200, out)
        self.assertNotIn("물건 기억", out["say"])
        self.assertNotIn("모름", out["say"])
        self.assertIn("휴대폰 충전기", out["say"])


if __name__ == "__main__":
    unittest.main()
