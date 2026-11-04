"""시나리오 테스트 공통 바탕: 임시 세계(Stack) 만들고 끝나면 모든 프로세스를 죽이고 지운다."""
from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path

from .harness import Stack, have


class E2ECase(unittest.TestCase):
    needs: tuple[str, ...] = ()
    maxDiff = 4000

    @classmethod
    def setUpClass(cls):
        missing = have(*cls.needs)
        if missing:
            raise unittest.SkipTest("형제 프로젝트가 없음: %s" % ", ".join(missing))

    def setUp(self):
        self.stack = Stack(prefix="e2e-%s-" % self.__class__.__name__.lower()[:20])
        self.addCleanup(self.stack.close)
        self.today = dt.date.today()

    # -- 편의 --
    def run_cli(self, project, args, **kw):
        return self.stack.run(project, args, **kw)

    def load_json(self, path: Path):
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))

    def mbrief_config(self, *, vjournal_db=None, paperbutler_db=None, cooloff_data=None, jarvis=None,
                      sidekick=None, out_dir=None) -> Path:
        """morning-brief 설정: 쓰지 않는 어댑터는 끄고, 쓰는 것은 경로를 명시한다(실제 HOME 을 안 보게)."""
        src = {
            "game_calendar": {"enabled": False}, "community_briefing": {"enabled": False},
            "sound_watch": {"enabled": False},
            "claude_night": {"enabled": True, "projects_dir": str(self.stack.path("claude-projects/.keep").parent)},
            "voice_journal": {"enabled": bool(vjournal_db), "db": str(vjournal_db or "")},
            "paper_butler": {"enabled": bool(paperbutler_db), "db": str(paperbutler_db or "")},
            "cool_off": {"enabled": bool(cooloff_data), "data": str(cooloff_data or "")},
            "jarvis": {"enabled": False},
        }
        if jarvis:
            src["jarvis"] = dict({"enabled": True, "watch_devices": []}, **jarvis)
        cfg = {"out_dir": str(out_dir or self.stack.path("mbrief/out/.keep").parent), "sources": src,
               "notify": {"sidekick": {"url": sidekick.url if sidekick else "http://127.0.0.1:9",
                                       "token": sidekick.token if sidekick else "", "token_env": ""},
                          "ntfy": {"topic": ""}}}
        return self.stack.write_json("mbrief/mbrief.json", cfg)
