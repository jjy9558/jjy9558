"""명령줄 진입점: python -m homelabe2e <명령>."""
import argparse
import sys
from typing import TextIO

from . import __version__
from .harness import PROJECTS, ROOT, have

# 시나리오 -> 필요한 형제 프로젝트 (tests/test_s*.py 와 같은 순서)
SCENARIOS = [
    ("1", "일기 → 아침 브리핑 → 책상 카드 / 알람 게이트", ("voice-journal", "morning-brief", "phone-home", "s21-sidekick")),
    ("2", "고지서·결제문자 → 납부 카드 / 가계부 CSV → cool-off", ("paper-butler", "morning-brief", "cool-off")),
    ("3", "sidekick 결재 훅 ↔ phone-home 프록시·책상", ("s21-sidekick", "phone-home", "object-memory")),
    ("4", "jarvis 작업 → notify.say → sidekick", ("jarvis-hub", "s21-sidekick")),
    ("5", "life-nagger sidekick 채널 / 한숨 전달", ("life-nagger", "s21-sidekick")),
    ("6", "object-memory 프레임 → 출발 체크", ("object-memory", "s21-sidekick")),
    ("7", "생활 로그 → story-factory → shorts-linter", ("story-factory", "shorts-linter", "life-nagger",
                                                    "decision-pet", "cool-off", "steam-buddy", "voice-journal")),
    ("8", "decision-pet 결정 의회", ("decision-pet", "cool-off", "steam-buddy", "paper-butler", "voice-journal")),
    ("9", "steam-buddy · paper-butler · cool-off 돈 기록", ("steam-buddy", "cool-off", "paper-butler")),
]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="homelab-e2e", description="프로젝트끼리 실제로 말이 통하는지 확인하는 교차 프로젝트 e2e 테스트")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("projects", help="시나리오별로 필요한 형제 프로젝트가 있는지 보기")
    return p


def cmd_projects(args: argparse.Namespace, out: TextIO) -> int:
    print("저장소: %s" % ROOT, file=out)
    for sid, title, needs in SCENARIOS:
        missing = have(*needs)
        mark = "준비됨" if not missing else "건너뜀(없음: %s)" % ", ".join(missing)
        print("  시나리오 %s  %-44s %s" % (sid, title, mark), file=out)
    print("패키지: " + ", ".join("%s=%s" % kv for kv in sorted(PROJECTS.items())), file=out)
    print("실행: python -m unittest discover -s tests -v", file=out)
    return 0


COMMANDS = {"projects": cmd_projects}


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # cp949 콘솔에서 한글/이모지로 죽지 않게
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    return COMMANDS[args.cmd](args, out or sys.stdout)
