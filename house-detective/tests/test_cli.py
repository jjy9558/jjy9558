import io
import tempfile
import unittest
from datetime import date
from pathlib import Path

from housedet import fridge, store
from housedet.cli import main


def run(home, *argv, today="2026-11-02"):
    buf = io.StringIO()
    code = main(["--home", str(home), "--today", today, *argv], out=buf)
    return code, buf.getvalue()


class FridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_boundaries(self):
        shelf = store.builtin_shelf()
        start = date(2026, 12, 29)
        exp, _ = fridge.compute_expiry(shelf, "우유", "냉장", start)
        self.assertEqual(exp, date(2027, 1, 3))  # 연말 경계 +5일
        exp, _ = fridge.compute_expiry(shelf, "계란", "냉장", date(2028, 2, 20))
        self.assertEqual(exp, date(2028, 3, 12))  # 윤년 +21일
        it = {"expires": "2026-11-02"}
        self.assertEqual(fridge.status(it, date(2026, 11, 2)), ("임박", 0))
        self.assertEqual(fridge.status(it, date(2026, 11, 3)), ("지남", -1))
        self.assertEqual(fridge.status({"expires": "2026-11-05"}, date(2026, 11, 2)), ("여유", 3))

    def test_unknown_item_is_unknown(self):
        code, text = run(self.home, "fridge", "add", "가나다볶음")
        self.assertEqual(code, 0)
        self.assertIn("기한 모름", text)
        _, rep = run(self.home, "fridge", "check")
        self.assertIn("기한 모름", rep)
        self.assertNotIn("현행범", rep)

    def test_unknown_storage_is_unknown(self):
        code, text = run(self.home, "fridge", "add", "우유", "--storage", "냉동")
        self.assertIn("기한 모름", text)

    def test_sorted_soonest_first(self):
        run(self.home, "fridge", "add", "계란")  # +21
        run(self.home, "fridge", "add", "두부", "--date", "2026-10-31")  # 11-03
        run(self.home, "fridge", "add", "우유", "--date", "2026-10-20")  # 지남
        run(self.home, "fridge", "add", "신기한것")
        _, text = run(self.home, "fridge", "list")
        pos = [text.index(n) for n in ("우유", "두부", "계란", "신기한것")]
        self.assertEqual(pos, sorted(pos))

    def test_report_expired_and_disclaimer(self):
        run(self.home, "fridge", "add", "우유", "--date", "2026-10-20")
        run(self.home, "fridge", "add", "두부", "--date", "2026-10-31")
        code, rep = run(self.home, "fridge", "check")
        self.assertIn("의심되면 버리세요", rep)
        self.assertIn("먹지 말고 버리세요", rep)
        self.assertIn("두부조림", rep)
        self.assertNotIn("우유 달걀", rep)  # 지난 품목은 요리 제안 없음
        _, lst = run(self.home, "fridge", "list")
        self.assertIn("의심되면 버리세요", lst)

    def test_caption_and_remove(self):
        cap = self.home / "cap.txt"
        cap.write_text("냉장고 선반에 딸기우유와 계란, 대파가 보인다", encoding="utf-8")
        code, text = run(self.home, "fridge", "add", "--from-caption", str(cap))
        self.assertEqual(code, 0)
        for n in ("우유", "계란", "대파"):
            self.assertIn(n, text)
        self.assertNotIn("딸기:", text)
        self.assertEqual(run(self.home, "fridge", "remove", "계란")[0], 0)
        self.assertEqual(run(self.home, "fridge", "remove", "없는것")[0], 1)

    def test_bad_date(self):
        self.assertEqual(run(self.home, "fridge", "add", "우유", "--date", "어제")[0], 2)


class RecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_known(self):
        code, text = run(self.home, "recycle", "치킨", "뼈")
        self.assertIn("일반쓰레기", text)
        self.assertIn("지자체", text)

    def test_unknown(self):
        code, text = run(self.home, "recycle", "양자컴퓨터")
        self.assertIn("모름: 지자체 확인", text.replace("모름. 지자체 확인", "모름: 지자체 확인"))
        self.assertIn("모름", text)
        self.assertIn("지자체", text)

    def test_user_rule_overrides(self):
        run(self.home, "rules", "add", "종이컵", "--bin", "우리 동네는 종이류", "--why", "구청 안내문")
        _, text = run(self.home, "recycle", "종이컵")
        self.assertIn("우리 동네는 종이류", text)
        self.assertIn("내 규칙", text)
        self.assertNotIn("난제", text)
        run(self.home, "rules", "remove", "종이컵")
        _, text = run(self.home, "recycle", "종이컵")
        self.assertIn("난제", text)

    def test_user_rule_new_item(self):
        run(self.home, "rules", "add", "양자컴퓨터", "--bin", "특수폐기물")
        _, text = run(self.home, "recycle", "양자컴퓨터")
        self.assertIn("특수폐기물", text)

    def test_caption(self):
        cap = self.home / "c.txt"
        cap.write_text("책상 위에 페트병과 건전지가 있다", encoding="utf-8")
        _, text = run(self.home, "recycle", "--from-caption", str(cap))
        self.assertIn("페트병", text)
        self.assertIn("폐건전지", text)

    def test_no_safety_claims_in_data(self):
        for r in store.builtin_recycle():
            self.assertNotIn("안전합니다", r["why"] + r["line"])


class DataTests(unittest.TestCase):
    def test_shelf_never_encourages_eating(self):
        for e in store.builtin_shelf():
            self.assertTrue(e["days"], e["name"])
            self.assertNotIn("괜찮", e.get("ideas", ""))


if __name__ == "__main__":
    unittest.main()
