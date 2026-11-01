import io
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from steam_buddy.cli import App, build_parser
from steam_buddy.store import Store
from tests.fakes import NOW, FakeSteam, details_payload
from steam_buddy.steam import SteamClient, SteamError


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "data.json"
        self.fake = FakeSteam()
        self.pushed = []
        self.fake.search_items = [{"id": 10, "name": "Hades"}]
        self.fake.details.update(details_payload(10, "Hades", price=26000))
        self.fake.details.update(details_payload(11, "Hades II", price=30000))
        self.fake.reviews[(10, "all")] = (950, 50)
        self.make_app()

    def make_app(self, now=NOW):
        self.out = io.StringIO()
        self.app = App(store=Store(self.path), client=SteamClient(fetch=self.fake), now=now,
                       out=self.out, push=lambda *a: self.pushed.append(a))

    def run_cmd(self, *argv, now=None):
        if now is not None:
            self.make_app(now)
        else:
            self.out.seek(0)
            self.out.truncate()
        args = build_parser().parse_args(list(argv))
        return getattr(self.app, "cmd_" + args.cmd)(args)

    def text(self):
        return self.out.getvalue()


class CheckTests(Base):
    def test_check(self):
        self.assertEqual(self.run_cmd("check", "Hades"), 0)
        t = self.text()
        self.assertIn("₩26,000", t)
        self.assertIn("한국어    자막", t)
        self.assertIn("95%", t)

    def test_check_early_access_risk(self):
        self.fake.details.update(details_payload(12, "Rotten", price=10000, early=True,
                                                 release="1 Jan, 2020"))
        self.fake.reviews[(12, "all")] = (20, 30)
        self.run_cmd("check", "12")
        self.assertIn("얼리액세스 방치 위험", self.text())
        self.assertIn("방치 위험 높음", self.text())

    def test_no_results(self):
        self.fake.search_items = []
        with self.assertRaises(SteamError):
            self.run_cmd("check", "nothing")


class FlowTests(Base):
    def test_buy_status_play_keep(self):
        self.assertEqual(self.run_cmd("buy", "Hades"), 0)
        self.assertEqual(self.app.store.get(10)["price_paid"], 26000)
        self.assertIn("기록했어요", self.text())

        self.run_cmd("status")
        self.assertIn("여유", self.text())
        self.assertIn("Hades", self.text())

        self.run_cmd("play", "Hades", "1h30m")
        self.assertEqual(self.app.store.get(10)["minutes"], 90)
        self.assertIn("30분 남음", self.text())
        self.run_cmd("play", "Hades", "20m", "--add")
        self.assertEqual(self.app.store.get(10)["minutes"], 110)

        self.run_cmd("status")
        self.assertIn("급함", self.text())

        self.run_cmd("play", "Hades", "2h")
        self.assertIn("넘었어요", self.text())

        self.run_cmd("keep", "Hades")
        self.run_cmd("status")
        self.assertIn("없어요", self.text())      # 보유확정은 기본 목록에서 숨김
        self.run_cmd("status", "--all")
        self.assertIn("보유확정", self.text())

        # 파일에 실제로 저장되었는지
        self.assertTrue(Store(self.path).get(10)["kept"])

    def test_buy_with_date_and_price(self):
        self.run_cmd("buy", "10", "--date", "2026-10-25T12:00", "--price", "15000")
        g = self.app.store.get(10)
        self.assertEqual(g["price_paid"], 15000)
        self.assertEqual(g["purchased_at"], "2026-10-25T12:00:00")
        self.run_cmd("status")
        self.assertIn("11/08 12:00", self.text())   # 14일 뒤 마감
        self.assertIn("여유", self.text())

    def test_buy_yesterday(self):
        self.run_cmd("buy", "10", "--date", "어제")
        self.assertEqual(self.app.store.get(10)["purchased_at"], (NOW - timedelta(days=1)).isoformat())

    def test_buy_falls_back_when_store_missing(self):
        self.run_cmd("buy", "777", "--price", "5000", "--name", "Delisted")
        self.assertEqual(self.app.store.get(777)["name"], "Delisted")
        with self.assertRaises(SteamError):
            self.run_cmd("buy", "778")

    def test_refunded_hidden_from_report(self):
        self.run_cmd("buy", "Hades")
        self.run_cmd("refunded", "Hades")
        self.run_cmd("report")
        self.assertIn("기록된 게임이 없어요", self.text())

    def test_remove(self):
        self.run_cmd("buy", "Hades")
        self.run_cmd("remove", "Hades")
        self.assertIsNone(self.app.store.get(10))

    def test_ambiguous_name(self):
        self.run_cmd("buy", "10")
        self.run_cmd("buy", "11")
        with self.assertRaises(SteamError) as cm:
            self.run_cmd("play", "hades", "1h")
        self.assertIn("여러 게임", str(cm.exception))
        self.assertIn("Hades II(11)", str(cm.exception))
        # appid로는 지정 가능
        self.run_cmd("play", "11", "1h")
        self.assertEqual(self.app.store.get(11)["minutes"], 60)

    def test_unknown_name(self):
        with self.assertRaises(SteamError):
            self.run_cmd("keep", "없는게임")


class SyncTests(Base):
    def config(self):
        self.run_cmd("config", "--key", "ABCDEF123", "--steamid", "7656119800000000")

    def test_no_config(self):
        self.assertEqual(self.run_cmd("sync"), 1)
        self.assertIn("config", self.text())

    def test_first_sync_baseline_then_detect_new(self):
        self.config()
        self.fake.owned = [(1, "Old A", 500), (2, "Old B", 0)]
        self.assertEqual(self.run_cmd("sync"), 0)
        self.assertIn("첫 동기화", self.text())
        self.assertEqual(self.app.store.games, {})   # 기존 보유 게임은 구매로 기록하지 않음

        self.fake.owned.append((10, "Hades", 25))
        self.run_cmd("sync", now=NOW + timedelta(hours=6))
        g = self.app.store.get(10)
        self.assertEqual(g["source"], "자동감지")
        self.assertEqual(g["minutes"], 25)
        self.assertEqual(g["price_paid"], 26000)
        self.assertIn("새 게임 감지: Hades", self.text())

        # 세 번째 동기화: 중복 감지 없이 플레이 시간만 갱신
        self.fake.owned[-1] = (10, "Hades", 70)
        self.run_cmd("sync")
        self.assertEqual(self.app.store.get(10)["minutes"], 70)
        self.assertNotIn("새 게임 감지", self.text())

    def test_sync_updates_manual_game(self):
        self.config()
        self.run_cmd("buy", "Hades")
        self.fake.owned = [(10, "Hades", 45)]
        self.run_cmd("sync")
        self.assertEqual(self.app.store.get(10)["minutes"], 45)
        self.assertEqual(self.app.store.get(10)["source"], "직접입력")

    def test_config_masks_key(self):
        self.config()
        self.run_cmd("config")
        self.assertIn("ABCD…", self.text())
        self.assertNotIn("ABCDEF123", self.text())


class AlertTests(Base):
    def test_nothing_urgent_no_push(self):
        self.run_cmd("buy", "Hades")
        self.run_cmd("alert", "--ntfy", "mytopic")
        self.assertEqual(self.pushed, [])
        self.assertIn("급한 환불 기한 없음", self.text())

    def test_urgent_pushes(self):
        self.run_cmd("buy", "Hades", "--date", "2026-10-20T12:00")  # 마감까지 1일 남음
        self.run_cmd("alert", "--ntfy", "mytopic")
        self.assertEqual(len(self.pushed), 1)
        topic, title, msg = self.pushed[0]
        self.assertEqual(topic, "mytopic")
        self.assertIn("Hades", msg)
        self.assertIn("mytopic", self.text())

    def test_uses_config_topic_and_skips_kept(self):
        self.run_cmd("config", "--ntfy", "saved")
        self.run_cmd("buy", "Hades", "--date", "2026-10-20T12:00")
        self.run_cmd("keep", "Hades")
        self.run_cmd("alert")
        self.assertEqual(self.pushed, [])
        self.run_cmd("buy", "11", "--date", "2026-10-20T12:00")
        self.run_cmd("alert")
        self.assertEqual(self.pushed[0][0], "saved")
        self.assertNotIn("Hades:", self.pushed[0][2].replace("Hades II:", ""))

    def test_urgent_without_topic_prints_only(self):
        self.run_cmd("buy", "Hades", "--date", "2026-10-20T12:00")
        self.assertEqual(self.run_cmd("alert"), 0)
        self.assertEqual(self.pushed, [])
        self.assertIn("환불 기한 임박", self.text())

    def test_push_failure_returns_1(self):
        def boom(*a):
            raise OSError("down")
        self.run_cmd("buy", "Hades", "--date", "2026-10-20T12:00")
        self.app.push = boom
        self.assertEqual(self.run_cmd("alert", "--ntfy", "t"), 1)
        self.assertIn("알림 전송 실패", self.text())

    def test_alert_syncs_first_and_survives_sync_error(self):
        self.run_cmd("config", "--key", "k", "--steamid", "s")
        def bad(url, params):
            raise SteamError("offline")
        self.app.client = SteamClient(fetch=bad)
        self.assertEqual(self.run_cmd("alert"), 0)
        self.assertIn("동기화 건너뜀", self.text())


class ReportTests(Base):
    def test_per_hour_values(self):
        self.run_cmd("buy", "10")                       # 26000원
        self.run_cmd("buy", "11")                       # 30000원
        self.run_cmd("play", "10", "2h")                # 13000/h
        self.run_cmd("play", "11", "10h")               # 3000/h
        self.run_cmd("rate", "10", "4", "--note", "재밌음")
        self.run_cmd("report")
        t = self.text()
        self.assertIn("₩13,000/h", t)
        self.assertIn("₩3,000/h", t)
        self.assertIn("★★★★", t)
        self.assertIn("재밌음", t)
        self.assertLess(t.index("Hades II"), t.index("Hades  "))  # 시간당 싼 게임이 먼저
        self.assertIn("합계 ₩56,000, 12.0시간, 시간당 ₩4,666", t)

    def test_unplayed_and_regret(self):
        self.run_cmd("buy", "10")
        self.run_cmd("buy", "11")
        self.run_cmd("play", "11", "5h")
        self.run_cmd("rate", "11", "2")
        self.run_cmd("report")
        self.assertIn("아직 안 켜본 게임 1개: Hades", self.text())
        self.assertIn("후회 목록(만족 2 이하): Hades II", self.text())

    def test_rate_range(self):
        self.run_cmd("buy", "10")
        with self.assertRaises(SteamError):
            self.run_cmd("rate", "10", "6")


class MainTests(Base):
    def test_main_reports_errors(self):
        from steam_buddy.cli import main
        err = io.StringIO()
        import contextlib
        with contextlib.redirect_stderr(err):
            code = main(["play", "nope", "1h"], app=self.app)
        self.assertEqual(code, 1)
        self.assertIn("오류", err.getvalue())

    def test_main_bad_duration(self):
        from steam_buddy.cli import main
        import contextlib
        self.run_cmd("buy", "10")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["play", "10", "abc"], app=self.app), 1)


if __name__ == "__main__":
    unittest.main()
