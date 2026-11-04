"""시각대(F-5): 기본은 PC 로컬 날짜. 'local' | '+09:00' | IANA 이름. 로컬 시각대는 TZ 환경변수를 바꿔 흉내 낸다."""
import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from helpers import FakeOpener, World, jarvis_status, pb_done
from mbrief import contract
from mbrief.cli import main
from mbrief.config import ConfigError, parse_timezone

UTC_LATE = dt.datetime(2026, 11, 4, 22, 30, tzinfo=dt.timezone.utc)   # UTC 11/4 22:30 = KST 11/5 07:30


@contextlib.contextmanager
def local_tz(name):
    old = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


class TimezoneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.w = World(Path(self._tmp.name))

    def run_cli(self, *argv, tz_over=None, clock=UTC_LATE):
        cfg = self.w.config()
        raw = json.loads((self.w.root / "mbrief.json").read_text(encoding="utf-8"))
        raw.pop("timezone", None)
        raw.update(tz_over or {})
        (self.w.root / "mbrief.json").write_text(json.dumps(raw), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = main(["run", "--config", str(self.w.root / "mbrief.json"), "--json", "--dry-run", *argv], out,
                      opener=FakeOpener({"/api/status": jarvis_status()}), clock=lambda: clock, env={})
        text = out.getvalue()
        return rc, (json.loads(text.split("\n\n[dry-run]")[0]) if rc == 0 else None), err.getvalue()

    def test_default_follows_machine_local_date(self):
        with local_tz("UTC"):
            rc, brief, _ = self.run_cli()
        self.assertEqual((rc, brief["date"], brief["tz"]), (0, "2026-11-04", "+00:00"))
        with local_tz("Asia/Seoul"):
            rc, brief, _ = self.run_cli()
        self.assertEqual((rc, brief["date"], brief["tz"]), (0, "2026-11-05", "+09:00"))
        with local_tz("America/Los_Angeles"):
            rc, brief, _ = self.run_cli()
        self.assertEqual((brief["date"], brief["tz"]), ("2026-11-04", "-08:00"))

    def test_explicit_local_equals_default(self):
        with local_tz("UTC"):
            self.assertEqual(self.run_cli(tz_over={"timezone": "local"})[1]["date"], "2026-11-04")

    def test_fixed_offset_ignores_machine(self):
        with local_tz("UTC"):
            brief = self.run_cli(tz_over={"timezone": "+09:00"})[1]
        self.assertEqual((brief["date"], brief["tz"]), ("2026-11-05", "+09:00"))

    def test_iana_name(self):
        with local_tz("UTC"):
            brief = self.run_cli(tz_over={"timezone": "Asia/Seoul"})[1]
        self.assertEqual((brief["date"], brief["tz"]), ("2026-11-05", "+09:00"))

    def test_legacy_tz_offset_minutes_still_honoured_unless_timezone_given(self):
        with local_tz("UTC"):
            self.assertEqual(self.run_cli(tz_over={"tz_offset_minutes": 540})[1]["date"], "2026-11-05")
            self.assertEqual(self.run_cli(tz_over={"tz_offset_minutes": 540, "timezone": "local"})[1]["date"],
                             "2026-11-04")

    def test_date_option_uses_local_offset_for_that_day(self):
        with local_tz("America/Los_Angeles"):     # 10/31 은 PDT(-07:00), 11/4 는 PST(-08:00)
            self.assertEqual(self.run_cli("--date", "2026-10-31")[1]["tz"], "-07:00")
            brief = self.run_cli("--date", "2026-11-04")[1]
        self.assertEqual((brief["date"], brief["tz"]), ("2026-11-04", "-08:00"))

    def test_bad_timezone_is_config_error(self):
        rc, _, err = self.run_cli(tz_over={"timezone": "Mars/Base"})
        self.assertEqual(rc, 2)
        self.assertIn("timezone", err)

    def test_parse_timezone(self):
        self.assertIsNone(parse_timezone("local"))
        self.assertIsNone(parse_timezone(None))
        self.assertEqual(parse_timezone("+09:00").utcoffset(None), dt.timedelta(hours=9))
        self.assertEqual(parse_timezone("-0330").utcoffset(None), -dt.timedelta(hours=3, minutes=30))
        self.assertEqual(parse_timezone("UTC").utcoffset(None), dt.timedelta(0))
        with self.assertRaises(ConfigError):
            parse_timezone("+25:00")
        with self.assertRaises(ConfigError):
            parse_timezone("nowhere")

    def test_adapters_use_same_day_as_output(self):
        """UTC PC 에서 어댑터가 보는 오늘(ctx.today)과 brief 의 date 가 같다 = '기준일이 달라요' 경고가 없다."""
        with local_tz("UTC"):
            rc, brief, _ = self.run_cli()
        self.assertEqual(brief["date"], "2026-11-04")
        for name, st in brief["sources"].items():
            self.assertNotIn("기준일", st.get("note", ""), name)


class CardActionTests(unittest.TestCase):
    def test_object_actions_valid_and_strings_still_valid(self):
        card = {"kind": "bill", "title": "t", "detail": "", "due": None,
                "actions": ["공지 열기: https://x.example", pb_done(12)]}
        self.assertEqual(contract.validate_cards([card]), [])
        for bad in ([{"id": "", "label": "x"}], [{"id": "a"}], [{"id": "a", "label": "b", "text": 3}], [3]):
            self.assertTrue(contract.validate_cards([dict(card, actions=bad)]), bad)

    def test_paper_butler_emits_command_actions(self):
        self.assertEqual(pb_done(7)["id"], "paperbutler.done:7")
        self.assertTrue(pb_done(7)["text"].startswith("완료 처리: python -m paperbutler done 7"))


if __name__ == "__main__":
    unittest.main()
