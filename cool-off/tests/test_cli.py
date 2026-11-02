import datetime as dt
import io
import socket
import tempfile
import unittest
from pathlib import Path

from cool_off.cli import App
from cool_off.store import Store
from tests.fakes import FakeOpener, at, network_down


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "data.json"
        self.answers = []
        self.prompts = []
        self.opener = FakeOpener()

    def app(self, now, interactive=False, opener=None):
        out = io.StringIO()
        def fake_input(prompt):
            self.prompts.append(prompt)
            return self.answers.pop(0) if self.answers else ""
        app = App(self.path, now=now, out=out, input_fn=fake_input,
                  interactive=interactive, opener=opener or self.opener)
        app.buffer = out
        return app

    def run_cmd(self, now, *argv, interactive=False, opener=None):
        app = self.app(now, interactive=interactive, opener=opener)
        code = app.run(list(argv))
        return code, app.buffer.getvalue()

    def state(self):
        return Store(self.path).load()


class WantAndListTests(Base):
    def test_want_persists_and_reports_cooling_end(self):
        code, out = self.run_cmd(at(2026, 11, 2, 10), "want", "에어팟 프로", "--price", "359,000",
                                 "--why", "출퇴근 노캔")
        self.assertEqual(code, 0)
        self.assertIn("에어팟 프로 359,000원", out)
        self.assertIn("2026-11-05 10:00", out)
        wish = self.state()["wishes"][0]
        self.assertEqual(wish["why"], "출퇴근 노캔")
        self.assertEqual(wish["status"], "cooling")

    def test_list_shows_status_table(self):
        self.run_cmd(at(2026, 11, 2), "want", "에어팟 프로", "--price", "359000")
        code, out = self.run_cmd(at(2026, 11, 3), "list")
        self.assertIn("쿨링오프 중", out)
        self.assertIn("359,000원", out)

    def test_missing_price_is_usage_error(self):
        code, _ = self.run_cmd(at(2026, 11, 2), "want", "에어팟")
        self.assertEqual(code, 2)

    def test_bad_price_is_usage_error(self):
        code, _ = self.run_cmd(at(2026, 11, 2), "want", "에어팟", "--price", "많이")
        self.assertEqual(code, 2)

    def test_domain_error_prints_message_and_fails(self):
        code, out = self.run_cmd(at(2026, 11, 2), "want", "에어팟", "--price", "1000", "--days", "-1")
        self.assertEqual(code, 1)
        self.assertIn("오류", out)
        self.assertEqual(self.state()["wishes"], [])


class CheckTests(Base):
    def setUp(self):
        super().setUp()
        self.run_cmd(at(2026, 11, 2, 10), "want", "에어팟 프로", "--price", "359000", "--why", "출퇴근")

    def test_nothing_due_says_so(self):
        code, out = self.run_cmd(at(2026, 11, 3), "check")
        self.assertIn("확인할 것이 없어요", out)

    def test_non_interactive_lists_without_changing_state(self):
        code, out = self.run_cmd(at(2026, 11, 5, 10), "check", interactive=False)
        self.assertIn("쿨링오프가 끝났어요", out)
        self.assertIn("에어팟 프로", out)
        self.assertIn("cool-off check", out)
        self.assertEqual(self.prompts, [])
        self.assertEqual(self.state()["wishes"][0]["status"], "cooling")

    def test_interactive_yes_moves_to_approved(self):
        self.answers = ["y"]
        code, out = self.run_cmd(at(2026, 11, 5, 10), "check", interactive=True)
        self.assertIn("아직 갖고 싶어? 에어팟 프로 (y/n/later): ", self.prompts)
        self.assertEqual(self.state()["wishes"][0]["status"], "approved")
        self.assertIn("구매 허가", out)

    def test_interactive_no_adds_to_saved_money(self):
        self.answers = ["n"]
        _, out = self.run_cmd(at(2026, 11, 5, 10), "check", interactive=True)
        self.assertEqual(self.state()["wishes"][0]["status"], "skipped")
        self.assertIn("아낀 돈 359,000원", out)
        _, report = self.run_cmd(at(2026, 11, 6), "report")
        self.assertIn("참은 금액: 359,000원", report)

    def test_interactive_later_asks_again_tomorrow(self):
        self.answers = ["later"]
        self.run_cmd(at(2026, 11, 5, 10), "check", interactive=True)
        wish = self.state()["wishes"][0]
        self.assertEqual(wish["status"], "cooling")
        self.assertEqual(wish["cool_until"], "2026-11-06T10:00")

    def test_blank_answer_keeps_item_pending(self):
        self.answers = [""]
        self.run_cmd(at(2026, 11, 5, 10), "check", interactive=True)
        self.assertEqual(self.state()["wishes"][0]["status"], "cooling")

    def test_ntfy_sends_prompt_once_and_dedupes(self):
        self.run_cmd(at(2026, 11, 5, 10), "check", "--ntfy", "cool-off-abc123")
        self.assertEqual(len(self.opener.requests), 1)
        self.assertIn("에어팟 프로", self.opener.requests[0]["body"])
        self.run_cmd(at(2026, 11, 5, 12), "check", "--ntfy", "cool-off-abc123")
        self.assertEqual(len(self.opener.requests), 1)

    def test_ntfy_failure_is_reported_and_retried_later(self):
        failing = FakeOpener(error=network_down())
        code, out = self.run_cmd(at(2026, 11, 5, 10), "check", "--ntfy", "cool-off-abc123", opener=failing)
        self.assertEqual(code, 0)
        self.assertIn("알림 전송 실패", out)
        self.assertEqual(self.state()["notified"], {})

        ok = FakeOpener()
        code, out = self.run_cmd(at(2026, 11, 5, 11), "check", "--ntfy", "cool-off-abc123", opener=ok)
        self.assertEqual(code, 0)
        self.assertEqual(len(ok.requests), 1)
        self.assertIn("알림 전송:", out)

    def test_ntfy_timeout_does_not_crash(self):
        failing = FakeOpener(error=socket.timeout("timed out"))
        code, out = self.run_cmd(at(2026, 11, 5, 10), "check", "--ntfy", "cool-off-abc123", opener=failing)
        self.assertEqual(code, 0)
        self.assertIn("알림 전송 실패", out)

    def test_invalid_ntfy_topic_reported(self):
        code, out = self.run_cmd(at(2026, 11, 5, 10), "check", "--ntfy", "bad topic!")
        self.assertIn("ntfy 설정 오류", out)
        self.assertEqual(self.opener.requests, [])


class BoughtAndFollowupTests(Base):
    def test_bought_links_wish_and_schedules_followup(self):
        self.run_cmd(at(2026, 11, 2), "want", "에어팟 프로", "--price", "359000")
        self.run_cmd(at(2026, 11, 5), "check", interactive=True)  # 아무 것도 없음
        self.answers = ["y"]
        self.run_cmd(at(2026, 11, 5, 10), "check", interactive=True)
        code, out = self.run_cmd(at(2026, 11, 6), "bought", "에어팟 프로", "--price", "329000")
        self.assertEqual(code, 0)
        self.assertIn("구매 기록: 에어팟 프로 329,000원 (2026-11-06)", out)
        self.assertIn("2026-12-06", out)
        state = self.state()
        self.assertEqual(state["wishes"][0]["status"], "bought")
        self.assertEqual(state["purchases"][0]["price"], 329000)

    def test_bought_with_date_option_and_default_price_from_wish(self):
        self.run_cmd(at(2026, 11, 2), "want", "키보드", "--price", "120000")
        code, out = self.run_cmd(at(2026, 11, 2), "bought", "키보드", "--date", "어제")
        self.assertEqual(code, 0)
        purchase = self.state()["purchases"][0]
        self.assertEqual(purchase["date"], "2026-11-01")
        self.assertEqual(purchase["price"], 120000)
        self.assertIn("쿨링오프가 끝나기 전에", out)

    def test_bought_unknown_without_price_fails(self):
        code, out = self.run_cmd(at(2026, 11, 2), "bought", "모름")
        self.assertEqual(code, 1)

    def test_followup_asked_after_thirty_days_and_stored(self):
        self.run_cmd(at(2026, 10, 1), "bought", "에어팟 프로", "--price", "329000")
        code, out = self.run_cmd(at(2026, 10, 30), "check")
        self.assertIn("확인할 것이 없어요", out)
        self.answers = ["4", "주1-2"]
        code, out = self.run_cmd(at(2026, 10, 31), "check", interactive=True)
        self.assertIn("만족도 1-5", self.prompts[-2])
        purchase = self.state()["purchases"][0]
        self.assertEqual(purchase["satisfaction"], 4)
        self.assertEqual(purchase["usage"], "주1-2")
        self.assertIn("기록했어요", out)

    def test_followup_invalid_answer_keeps_pending(self):
        self.run_cmd(at(2026, 10, 1), "bought", "가방", "--price", "90000")
        self.answers = ["9"]
        _, out = self.run_cmd(at(2026, 10, 31), "check", interactive=True)
        self.assertIn("보류", out)
        self.assertIsNone(self.state()["purchases"][0]["satisfaction"])


class SubscriptionCliTests(Base):
    def test_sub_add_and_list_totals_line(self):
        self.run_cmd(at(2026, 11, 2), "sub", "add", "넷플릭스", "13500", "--cycle", "monthly", "--day", "15")
        self.run_cmd(at(2026, 11, 2), "sub", "add", "정기 배송", "34300", "--day", "1")
        code, out = self.run_cmd(at(2026, 11, 2), "sub", "list")
        self.assertEqual(code, 0)
        self.assertIn("[구독료 총합 방송] 매달 47,800원, 1년이면 573,600원 — 치킨 28마리", out)
        self.assertIn("2026-11-15", out)

    def test_sub_reminder_three_days_before_renewal(self):
        self.run_cmd(at(2026, 11, 1), "sub", "add", "넷플릭스", "13500", "--day", "15")
        code, out = self.run_cmd(at(2026, 11, 12), "check")
        self.assertIn("넷플릭스 13,500원 — 3일 뒤 (2026-11-15)", out)

    def test_sub_remove_by_name(self):
        self.run_cmd(at(2026, 11, 1), "sub", "add", "넷플릭스", "13500")
        code, out = self.run_cmd(at(2026, 11, 1), "sub", "remove", "넷플릭스")
        self.assertIn("구독 삭제: 넷플릭스", out)
        _, out = self.run_cmd(at(2026, 11, 1), "sub", "list")
        self.assertIn("등록된 구독이 없어요", out)

    def test_bad_day_is_domain_error(self):
        code, out = self.run_cmd(at(2026, 11, 1), "sub", "add", "x", "1000", "--day", "40")
        self.assertEqual(code, 1)
        self.assertIn("결제일", out)


class ReportCliTests(Base):
    def test_report_sections(self):
        self.run_cmd(at(2026, 9, 5), "bought", "이어폰", "--price", "100000")
        self.run_cmd(at(2026, 9, 20), "bought", "키링", "--price", "30000")
        self.answers = ["5", "매일", "2", "주1-2"]
        self.run_cmd(at(2026, 10, 5), "check", interactive=True)
        self.run_cmd(at(2026, 10, 20), "check", interactive=True)
        code, out = self.run_cmd(at(2026, 10, 21), "report")
        self.assertEqual(code, 0)
        self.assertIn("평균 만족도: 3.5 / 5", out)
        self.assertIn("후회 목록", out)
        self.assertIn("키링", out)
        self.assertIn("1만원당", out)
        self.assertIn("2026-09", out)
        self.assertIn("130,000원", out)

    def test_empty_report(self):
        _, out = self.run_cmd(at(2026, 11, 2), "report")
        self.assertIn("아직 기록이 없어요", out)


class PersistenceTests(Base):
    def test_state_survives_new_app_instance(self):
        self.run_cmd(at(2026, 11, 2), "want", "마우스", "--price", "45000")
        _, out = self.run_cmd(at(2026, 11, 3), "list")
        self.assertIn("마우스", out)
        self.assertTrue(self.path.exists())


if __name__ == "__main__":
    unittest.main()
