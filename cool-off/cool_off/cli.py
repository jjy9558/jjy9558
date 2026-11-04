"""명령줄 진입점. 입력을 해석하고 결과를 한국어 표로 보여 줍니다."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from typing import Callable, List, Optional, Tuple, Union

import os

from . import contract, core, importers
from .core import CYCLES, LedgerError, Ledger
from .notify import Notifier, NotifyError
from .store import DATA_FILE, Store, StoreError, home_dir
from .text import table, won

PROG = "cool-off"
CHICKEN_ENV = "COOLOFF_CHICKEN_PRICE"


def _money(text: str) -> int:
    try:
        return core.parse_money(text)
    except LedgerError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def _fmt_time(stamp: str) -> str:
    return stamp.replace("T", " ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROG, description="구매 쿨링오프 + 후회 일기")
    commands = parser.add_subparsers(dest="cmd", required=True, metavar="명령")

    want = commands.add_parser("want", help="사고 싶은 것을 쿨링오프 목록에 담기")
    want.add_argument("name")
    want.add_argument("--price", type=_money, required=True)
    want.add_argument("--url", default="")
    want.add_argument("--why", default="")
    want.add_argument("--days", type=int, default=core.DEFAULT_COOL_DAYS,
                      help="쿨링오프 기간(일), 기본 %(default)s")

    check = commands.add_parser("check", help="쿨링오프 종료, 사후 확인, 구독 갱신 확인")
    check.add_argument("--ntfy", metavar="TOPIC", help="ntfy 주제로 확인할 항목 알림 보내기")
    check.add_argument("--json", action="store_true",
                       help="비대화형 JSON 출력 (데이터 파일을 고치지 않고 알림도 보내지 않음)")

    bought = commands.add_parser("bought", help="구매 기록")
    bought.add_argument("name")
    bought.add_argument("--price", type=_money, default=None, help="생략하면 담아 둔 가격을 씀")
    bought.add_argument("--date", default=None, help="YYYY-MM-DD / 오늘 / 어제 (기본 오늘)")

    commands.add_parser("list", help="담아 둔 목록 보기")
    report = commands.add_parser("report", help="돌아보기 리포트")
    report.add_argument("--week", action="store_true", help="최근 7일 지출을 치킨 마릿수로 환산한 줄 추가")
    report.add_argument("--chicken-price", type=_money, default=None,
                        help=f"치킨 1마리 가격(원). 생략하면 환경변수 {CHICKEN_ENV} 또는 {core.CHICKEN_PRICE:,}원")

    imp_steam = commands.add_parser("import-steam", help="steam-buddy data.json 가져오기 (읽기 전용)")
    imp_steam.add_argument("path", nargs="?", default=None,
                           help="data.json 경로 (기본 $STEAM_BUDDY_HOME 또는 ~/.steam-buddy/data.json)")
    imp_ledger = commands.add_parser("import-ledger", help="paper-butler export-csv --what ledger 파일 가져오기")
    imp_ledger.add_argument("csv", help="paper-butler 가계부 CSV 경로")

    subs = commands.add_parser("sub", help="구독 관리")
    actions = subs.add_subparsers(dest="action", required=True, metavar="동작")
    sub_add = actions.add_parser("add", help="구독 추가")
    sub_add.add_argument("name")
    sub_add.add_argument("price", type=_money)
    sub_add.add_argument("--cycle", choices=CYCLES, default=core.CYCLE_MONTHLY)
    sub_add.add_argument("--day", type=int, default=None, help="결제일 1-31 (기본 오늘)")
    sub_add.add_argument("--month", type=int, default=None, help="연간 구독의 결제 월 1-12 (기본 이번 달)")
    actions.add_parser("list", help="구독 목록과 총합")
    sub_rm = actions.add_parser("remove", help="구독 삭제")
    sub_rm.add_argument("key", help="번호 또는 이름")
    return parser


def _is_tty(stream) -> bool:
    isatty = getattr(stream, "isatty", None)
    try:
        return bool(isatty and isatty())
    except (ValueError, OSError):
        return False


class App:
    def __init__(self, data_path, now: Union[None, dt.datetime, Callable[[], dt.datetime]] = None,
                 out=None, input_fn: Optional[Callable[[str], str]] = None,
                 interactive: Optional[bool] = None, opener=None):
        self.store = Store(data_path)
        if now is None:
            self._now = dt.datetime.now
        elif isinstance(now, dt.datetime):
            fixed = now
            self._now = lambda: fixed
        else:
            self._now = now
        self.out = out if out is not None else sys.stdout
        self.input = input_fn if input_fn is not None else input
        self.interactive = _is_tty(sys.stdin) if interactive is None else interactive
        self.opener = opener

    # ------------------------------------------------------------ 공통

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def _ask(self, prompt: str) -> str:
        try:
            return self.input(prompt).strip()
        except EOFError:
            return ""

    def run(self, argv: List[str]) -> int:
        parser = build_parser()
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 2
        if getattr(args, "json", False):
            return self.run_check_json(args)
        handler = getattr(self, "cmd_" + args.cmd.replace("-", "_"))
        try:
            ledger = Ledger(self.store.load())
            changed = bool(handler(args, ledger, self._now()))
            if changed:
                self.store.save(ledger.state)
        except (LedgerError, StoreError) as exc:
            self.say(f"오류: {exc}")
            return 1
        return 0

    # ------------------------------------------------------------ 담아 두기

    def cmd_want(self, args, ledger: Ledger, now: dt.datetime) -> bool:
        wish = ledger.add_wish(args.name, args.price, now, url=args.url, why=args.why, days=args.days)
        self.say(f"담아 뒀어요: #{wish['id']} {wish['name']} {won(wish['price'])}")
        self.say(f"쿨링오프 {args.days}일 — {_fmt_time(wish['cool_until'])} 이후 `check`에서 다시 물어볼게요.")
        return True

    def cmd_list(self, args, ledger: Ledger, now: dt.datetime) -> bool:
        rows = []
        for wish in ledger.state["wishes"]:
            if wish["status"] == core.STATUS_BOUGHT:
                continue
            until = _fmt_time(wish["cool_until"]) if wish["status"] == core.STATUS_COOLING else "-"
            rows.append([f"#{wish['id']}", wish["name"], won(wish["price"]),
                         core.STATUS_LABELS[wish["status"]], until])
        if not rows:
            self.say("담아 둔 것이 없어요.")
            return False
        self.say(table(["번호", "이름", "가격", "상태", "쿨링오프 끝"], rows, ["l", "l", "r", "l", "l"]))
        return False

    # ------------------------------------------------------------ 확인

    def run_check_json(self, args) -> int:
        """`check --json`: 입력을 받지 않고, 데이터 파일을 쓰지 않고, 알림을 보내지 않습니다.

        오류는 표준 오류로 내보내서 표준 출력에는 JSON만 남깁니다.
        """
        if args.ntfy:
            print("오류: --json과 --ntfy는 함께 쓸 수 없어요.", file=sys.stderr)
            return 2
        try:
            ledger = Ledger(self.store.load())  # load()는 읽기만 합니다.
            payload = contract.validate(contract.build(ledger, self._now()))
        except (LedgerError, StoreError, contract.ContractError) as exc:
            print(f"오류: {exc}", file=sys.stderr)
            return 1
        self.say(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    def cmd_check(self, args, ledger: Ledger, now: dt.datetime) -> bool:
        today = now.date()
        due = ledger.due_wishes(now)
        approved = ledger.approved_wishes()
        followups = ledger.due_followups(today)
        renewals = ledger.due_renewals(today)
        if not (due or approved or followups or renewals):
            self.say("지금 확인할 것이 없어요.")
            return False

        messages: List[Tuple[str, str]] = []  # (중복 방지 키, 알림 본문)

        if due:
            self.say("== 쿨링오프가 끝났어요 ==")
            for wish in due:
                why = f" — {wish['why']}" if wish["why"] else ""
                self.say(f"  #{wish['id']} {wish['name']} {won(wish['price'])}{why}")
                messages.append((f"wish:{wish['id']}:{wish['cool_until']}",
                                 f"[쿨링오프 끝] {wish['name']} {won(wish['price'])} — 아직 갖고 싶어?"))
            self.say()

        if approved:
            self.say("== 구매 허가 (아직 안 산 것) ==")
            for wish in approved:
                self.say(f"  #{wish['id']} {wish['name']} {won(wish['price'])}")
            self.say()

        if followups:
            self.say(f"== 사후 확인 ({core.FOLLOWUP_DAYS}일 지남) ==")
            for purchase in followups:
                self.say(f"  #{purchase['id']} {purchase['name']} {won(purchase['price'])} ({purchase['date']} 구매)")
                messages.append((f"fu:{purchase['id']}",
                                 f"[사후 확인] {purchase['name']} — 아직 써? 만족도 1-5"))
            self.say()

        if renewals:
            self.say("== 구독 갱신 임박 ==")
            for sub, renewal, left in renewals:
                when = "오늘" if left == 0 else f"{left}일 뒤"
                self.say(f"  {sub['name']} {won(sub['price'])} — {when} ({renewal.isoformat()})")
                messages.append((f"sub:{sub['id']}:{renewal.isoformat()}",
                                 f"[구독 갱신] {sub['name']} {won(sub['price'])} — {when} ({renewal.isoformat()})"))
            self.say()

        changed = False
        if args.ntfy:
            changed = self._notify(args.ntfy, messages, ledger, now) or changed

        if self.interactive:
            for wish in due:
                changed = self._ask_wish(wish, ledger, now) or changed
            for purchase in followups:
                changed = self._ask_followup(purchase, ledger, now) or changed
        elif due or followups:
            self.say("터미널에서 `cool-off check`를 실행하면 여기서 바로 답할 수 있어요.")
        return changed

    def _ask_wish(self, wish: dict, ledger: Ledger, now: dt.datetime) -> bool:
        answer = self._ask(f"아직 갖고 싶어? {wish['name']} (y/n/later): ").lower()
        if answer in ("y", "yes"):
            ledger.decide_wish(wish, "y", now)
            self.say("  → 구매 허가. 사고 나면 `bought`로 기록하세요.")
        elif answer in ("n", "no"):
            ledger.decide_wish(wish, "n", now)
            self.say(f"  → 참은 것으로 기록했어요. 아낀 돈 {won(wish['price'])}")
        elif answer in ("later", "l"):
            ledger.decide_wish(wish, "later", now)
            self.say(f"  → {core.LATER_HOURS}시간 뒤에 다시 물어볼게요.")
        else:
            self.say("  → 보류했어요. 다음 check에서 다시 물어볼게요.")
            return False
        return True

    def _ask_followup(self, purchase: dict, ledger: Ledger, now: dt.datetime) -> bool:
        sat_text = self._ask(f"아직 써? {purchase['name']} 만족도 1-5 (빈칸=나중에): ")
        if not sat_text:
            self.say("  → 나중에 다시 물어볼게요.")
            return False
        try:
            satisfaction = core.parse_satisfaction(sat_text)
            usage_text = self._ask("사용 빈도 (매일 / 주1-2 / 거의 안 씀): ")
            usage = core.normalize_usage(usage_text)
        except LedgerError as exc:
            self.say(f"  → 보류했어요: {exc}")
            return False
        ledger.record_followup(purchase, satisfaction, usage, now)
        self.say(f"  → 기록했어요: 만족도 {satisfaction}, {usage}")
        return True

    def _notify(self, topic: str, messages, ledger: Ledger, now: dt.datetime) -> bool:
        try:
            notifier = Notifier(topic, opener=self.opener)
        except NotifyError as exc:
            self.say(f"ntfy 설정 오류: {exc}")
            return False
        changed = False
        for key, body in messages:
            if ledger.is_notified(key):
                continue
            ok, detail = notifier.send(body)
            if ok:
                ledger.mark_notified(key, now)
                changed = True
                self.say(f"알림 전송: {body}")
            else:
                self.say(f"알림 전송 실패 (다음 check에서 다시 보냅니다): {detail}")
        return changed

    # ------------------------------------------------------------ 구매 / 사후 확인

    def cmd_bought(self, args, ledger: Ledger, now: dt.datetime) -> bool:
        bought_on = core.parse_day(args.date or "오늘", now.date())
        purchase, _wish = ledger.record_purchase(args.name, args.price, bought_on, now)
        self.say(f"구매 기록: {purchase['name']} {won(purchase['price'])} ({bought_on.isoformat()})")
        if purchase["during_cooling"]:
            self.say("쿨링오프가 끝나기 전에 산 것으로 기록했어요.")
        self.say(f"{core.followup_date(bought_on).isoformat()}에 `check`에서 만족도를 물어볼게요.")
        return True

    # ------------------------------------------------------------ 구독

    def cmd_sub(self, args, ledger: Ledger, now: dt.datetime) -> bool:
        if args.action == "add":
            sub = ledger.add_subscription(args.name, args.price, args.cycle, args.day, args.month, now.date())
            self.say(f"구독 추가: #{sub['id']} {sub['name']} {won(sub['price'])} ({core.describe_schedule(sub)})")
            return True
        if args.action == "remove":
            removed = ledger.remove_subscription(args.key)
            self.say(f"구독 삭제: {removed['name']}")
            self._funeral(removed, ledger.subscription_funeral(removed, now.date()))
            return True
        subs = ledger.state["subscriptions"]
        if not subs:
            self.say("등록된 구독이 없어요. `sub add`로 추가해 보세요.")
            return False
        today = now.date()
        rows = []
        for sub in subs:
            rows.append([
                f"#{sub['id']}",
                sub["name"],
                core.describe_schedule(sub),
                won(sub["price"]),
                core.next_renewal(sub, today).isoformat(),
                won(core.yearly_cost(sub)),
            ])
        self.say(table(["번호", "이름", "주기", "금액", "다음 갱신", "1년 환산"], rows,
                       ["l", "l", "l", "r", "l", "r"]))
        totals = ledger.subscription_totals()
        self.say()
        self.say(f"[구독료 총합 방송] 매달 {won(totals['monthly'])}, 1년이면 {won(totals['yearly'])}"
                 f" — 치킨 {totals['chickens']}마리")
        return False

    def _funeral(self, sub: dict, info: dict) -> None:
        years = str(info["from_year"]) if info["from_year"] == info["to_year"] else f"{info['from_year']}~{info['to_year']}"
        self.say()
        if info["payments"] == 0:
            self.say(f"고 {sub['name']}({years}), 한 푼도 못 받고 떠나다")
            return
        self.say(f"고 {sub['name']}({years}), 총 {core.korean_won(info['total'])}을 남기고 떠나다")
        span = f", {info['months']}개월" if info["months"] else ""
        self.say(f"  결제 {info['payments']}번{span}, 낸 돈 {won(info['total'])}. 삼가 명복을 빕니다.")

    # ------------------------------------------------------------ 가져오기

    def cmd_import_steam(self, args, ledger: Ledger, now: dt.datetime) -> bool:
        path = args.path or importers.default_steam_path()
        res = importers.import_steam(ledger, path)
        if not (res["added"] or res["duplicate"] or res["skipped"]):
            self.say(f"가져올 게임이 없어요 (빈 파일): {path}")
            return False
        self.say(f"스팀 가져오기: 새로 {res['added']}건, 이미 있음 {res['duplicate']}건"
                 f"{', 만족도 갱신 %d건' % res['updated'] if res['updated'] else ''}"
                 f", 건너뜀 {res['skipped']}건 (무료 게임이거나 형식 오류)")
        return bool(res["added"] or res["updated"])

    def cmd_import_ledger(self, args, ledger: Ledger, now: dt.datetime) -> bool:
        res = importers.import_ledger_csv(ledger, args.csv)
        if res["rows"] == 0:
            self.say(f"가져올 행이 없어요 (빈 파일): {args.csv}")
            return False
        self.say(f"가계부 가져오기: 새로 {res['added']}건, 이미 있음 {res['duplicate']}건"
                 f", 이중 집계라 뺀 것 {res['double']}건, 건너뜀 {res['skipped']}건 (취소 행/형식 오류)")
        cands = importers.subscription_candidates(ledger)
        if cands:
            self.say()
            self.say(f"== 구독 후보 (같은 가맹점+금액이 {importers.MIN_REPEAT_MONTHS}개월 이상 연속) ==")
            for c in cands:
                self.say(f"  {c['name']} {won(c['price'])} — {c['months']}개월 연속 "
                         f"→ cool-off sub add \"{c['name']}\" {c['price']} --day {c['day']}")
        return bool(res["added"])

    # ------------------------------------------------------------ 리포트

    def _chicken_price(self, args) -> int:
        if args.chicken_price:
            return args.chicken_price
        raw = os.environ.get(CHICKEN_ENV)
        if raw:
            return core.parse_money(raw)
        return core.CHICKEN_PRICE

    def _week_line(self, ledger: Ledger, now: dt.datetime, price: int) -> str:
        today = now.date()
        start = today - dt.timedelta(days=6)
        spent = sum(p["price"] for p in ledger.state["purchases"]
                    if start <= dt.date.fromisoformat(p["date"]) <= today)
        return (f"[치킨 환산 주간 보고] 최근 7일({start.isoformat()}~{today.isoformat()}) 지출 {won(spent)}"
                f" = 치킨 {spent / price:.1f}마리 (1마리 {won(price)})")

    def cmd_report(self, args, ledger: Ledger, now: dt.datetime) -> bool:
        rep = ledger.report()
        if args.week:
            self.say(self._week_line(ledger, now, self._chicken_price(args)))
            self.say()
        if rep["purchase_count"] == 0 and rep["saved_count"] == 0:
            self.say("아직 기록이 없어요. `want`로 담고 `bought`로 구매를 기록해 보세요.")
            return False

        self.say("== 돌아보기 ==")
        self.say(f"참은 금액: {won(rep['saved_total'])} (참은 것 {rep['saved_count']}건)")
        self.say(f"산 것: {rep['purchase_count']}건, 합계 {won(rep['spent_total'])}")
        if rep["avg_satisfaction"] is None:
            self.say("산 것의 평균 만족도: 아직 평가한 것이 없어요")
        else:
            self.say(f"산 것의 평균 만족도: {rep['avg_satisfaction']:.1f} / 5 (평가 {rep['rated_count']}건)")
        self.say()

        self.say("== 출처별 합계 ==")
        order = {src: i for i, src in enumerate(importers.SOURCE_ORDER)}
        rows = []
        for e in sorted(rep["by_source"], key=lambda e: order.get(e["source"], 99)):
            avg = f"{e['avg']:.1f}" if e["avg"] is not None else "-"
            rows.append([importers.SOURCE_LABELS.get(e["source"], e["source"]), e["count"],
                         won(e["total"]), avg])
        self.say(table(["출처", "건수", "합계", "평균 만족도"], rows, ["l", "r", "r", "r"]))
        self.say()

        self.say(f"== 후회 목록 (만족도 {core.REGRET_MAX_SATISFACTION} 이하 또는 거의 안 씀) ==")
        if rep["regrets"]:
            rows = [[p["date"], p["name"], won(p["price"]), p["satisfaction"], p["usage"] or "-"]
                    for p in rep["regrets"]]
            self.say(table(["구매일", "이름", "가격", "만족도", "사용 빈도"], rows,
                           ["l", "l", "r", "r", "l"]))
        else:
            self.say("없어요. 잘 고르고 있어요.")
        self.say()

        self.say("== 원당 만족도 (1만원당 점수, 높을수록 가성비) ==")
        if rep["ranking"]:
            rows = []
            for rank, p in enumerate(rep["ranking"], start=1):
                rows.append([rank, p["name"], importers.SOURCE_LABELS.get(importers.source_of(p), "-"),
                             won(p["price"]), p["satisfaction"], f"{core.value_per_man(p):.2f}"])
            self.say(table(["순위", "이름", "출처", "가격", "만족도", "1만원당"], rows,
                           ["r", "l", "l", "r", "r", "r"]))
        else:
            self.say("평가한 것이 없어요.")
        self.say()

        self.say("== 월별 구매 지출 (모든 출처 합산) ==")
        rows = [[month, won(total)] for month, total in rep["monthly"]]
        self.say(table(["월", "금액"], rows, ["l", "r"]))
        return False


def _force_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def main(argv: Optional[List[str]] = None) -> int:
    _force_utf8_stdout()
    argv = sys.argv[1:] if argv is None else list(argv)
    return App(home_dir() / DATA_FILE).run(argv)
