"""명령줄 인터페이스 (헤드리스에서도 동작)."""
import argparse
import sys

from .core import Pet, epitaph
from .experiments import write_default_experiments
from .storage import Store


def _build_parser():
    p = argparse.ArgumentParser(prog="decision_pet", description="결정해주는 데스크톱 펫")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("add", help="할 일 추가")
    a.add_argument("title")
    a.add_argument("--effort", type=int, default=15, choices=[5, 15, 60])
    a.add_argument("--tags", default="", help="쉼표로 구분")

    sub.add_parser("now", help="지금 뭐 해?")
    sub.add_parser("list", help="할 일 목록")
    d = sub.add_parser("done", help="끝냈어")
    d.add_argument("id", nargs="?", type=int)
    s = sub.add_parser("skip", help="미룰래")
    s.add_argument("id", nargs="?", type=int)

    dc = sub.add_parser("decide", help="결정해줘 (2~5개 선택지)")
    dc.add_argument("options", nargs="+", help='"라면" "김밥" "굶기" 또는 "라면 vs 김밥"')
    dc.add_argument("--mode", choices=["coin", "score", "llm", "council"], default="coin")
    dc.add_argument("--criteria", default="", help='점수표 기준 "맛:3,가격:2" (2~3개, 이름:가중치)')

    dc.add_argument("--llm", action="store_true", help="council: 로컬 LLM 으로 발언 말투 다듬기 (실패하면 템플릿)")
    for flag, key in (("--cool-off", "cool_off"), ("--steam-buddy", "steam_buddy"),
                      ("--life-nagger", "life_nagger"), ("--voice-journal", "voice_journal"),
                      ("--paper-butler", "paper_butler")):
        dc.add_argument(flag, dest="src_" + key, default="", metavar="PATH",
                        help="council: %s 데이터 경로 (기본: config.sources 또는 프로젝트 기본 위치)" % key)

    g = sub.add_parser("graveyard", help="안 한 일 묘지")
    g.add_argument("--resurrect", type=int, metavar="ID", help="부활시키기")

    sub.add_parser("experiment", help="오늘의 15분 실험")
    sub.add_parser("experiment-done", help="오늘의 실험 완료 표시")
    sub.add_parser("followup", help="지난 결정 만족도 묻기")
    sub.add_parser("history", help="만족도 기록")

    iv = sub.add_parser("import-vjournal", help="voice-journal 에서 포기한 선언을 묘지로 (읽기 전용)")
    iv.add_argument("db")

    lu = sub.add_parser("lunch", help="점심 운명 로딩")
    lu.add_argument("--menu", default="", help='후보 "김치찌개,라면,돈까스" (한 번 주면 저장)')
    lu.add_argument("--no-anim", action="store_true", help="로딩 애니메이션 생략")

    i = sub.add_parser("import-todos", help="마크다운의 '- [ ]' 항목 가져오기")
    i.add_argument("file")

    sub.add_parser("gui", help="데스크톱 펫 실행")
    return p


def _parse_criteria(text):
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, w = part.partition(":")
        out.append((name.strip(), float(w) if w.strip() else 1.0))
    return out


def _council(args, pet, out):
    from . import council
    question = " ".join(args.options)
    overrides = {k: getattr(args, "src_" + k) for k in council.SOURCE_KEYS}
    paths = council.resolve_paths(pet.config.get("sources"), overrides)
    evidence = council.collect(paths, pet.now().date())
    result = council.convene(question, evidence, pet.config.get("council"))
    if args.llm:
        council.phrase_with_llm(result, pet.config["llm"], pet.opener)
    for line in council.render(result):
        out(line)
    pet.record_council(result)
    return 0


def run(argv, pet=None, out=print, inp=input):
    args = _build_parser().parse_args(argv)
    if args.cmd is None:
        _build_parser().print_help()
        return 0
    if args.cmd == "gui":
        try:
            from .gui import run_gui  # 지연 import: 디스플레이 없는 환경 보호
        except ImportError:
            out("tkinter를 불러올 수 없어요. (Linux: sudo apt install python3-tk)")
            return 1
        run_gui()
        return 0
    pet = pet or Pet()

    if args.cmd == "add":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        t = pet.add(args.title, args.effort, tags)
        out("추가했어요 #%d: %s (%d분)" % (t["id"], t["title"], t["effort"]))

    elif args.cmd == "now":
        for t in pet.sweep():
            out("🪦 " + epitaph(t))
        res = pet.suggest()
        if res is None:
            out("할 일이 없어요! 'add'로 추가하거나 'experiment'를 해보세요.")
        else:
            t = res["task"]
            out("오늘은 이거 해: #%d %s (%d분)" % (t["id"], t["title"], t["effort"]))
            out("이유: " + res["explanation"])

    elif args.cmd == "list":
        todo = pet.tasks("todo")
        if not todo:
            out("할 일이 없어요.")
        for t in todo:
            out("#%d [%d분] %s (미룬 %d회)" % (t["id"], t["effort"], t["title"], t["times_skipped"]))

    elif args.cmd == "done":
        try:
            t = pet.done(args.id)
        except (LookupError, KeyError) as e:
            out(str(e.args[0]) if e.args else str(e))
            return 1
        out("잘했어요! 끝냈어요: %s" % t["title"])

    elif args.cmd == "skip":
        try:
            t, buried = pet.skip(args.id)
        except (LookupError, KeyError) as e:
            out(str(e.args[0]) if e.args else str(e))
            return 1
        out("괜찮아요, 미뤘어요 (%d회): %s" % (t["times_skipped"], t["title"]))
        if buried:
            out("🪦 " + epitaph(t))

    elif args.cmd == "decide":
        options = args.options
        criteria = scores = None
        if args.mode == "council":
            return _council(args, pet, out)
        try:
            if len(options) == 1:
                from .decide import parse_options
                options = parse_options(options[0])
            if args.mode == "score":
                criteria = _parse_criteria(args.criteria)
                if not criteria:
                    out("점수표 모드는 --criteria \"맛:3,가격:2\" 가 필요해요.")
                    return 1
                scores = {}
                for o in options:
                    scores[o] = {}
                    for name, w in criteria:
                        scores[o][name] = int(inp("%s의 '%s' 점수 (1~5): " % (o, name)))
            res = pet.decide(options, args.mode, criteria, scores)
        except ValueError as e:
            out(str(e))
            return 1
        out("결정! → %s" % res["choice"])
        out("이유: " + res["reason"])

    elif args.cmd == "graveyard":
        if args.resurrect is not None:
            try:
                t = pet.resurrect(args.resurrect)
            except (ValueError, KeyError) as e:
                out(str(e.args[0]) if e.args else str(e))
                return 1
            out("부활! #%d %s" % (t["id"], t["title"]))
            return 0
        graves = pet.graveyard()
        if not graves:
            out("묘지가 비어 있어요. 평화롭네요.")
        for t in graves:
            out("#%d 🪦 %s" % (t["id"], epitaph(t)))

    elif args.cmd == "import-vjournal":
        try:
            added = pet.import_vjournal(args.db)
        except ValueError as e:
            out(str(e))
            return 1
        out("%d개를 묘지에 묻었어요." % len(added))
        for t in added:
            out("  #%d 🪦 %s" % (t["id"], epitaph(t)))

    elif args.cmd == "lunch":
        from .lunch import animate, parse_menu
        try:
            choice, excluded = pet.lunch(parse_menu(args.menu) if args.menu else None)
        except ValueError as e:
            out(str(e))
            return 1
        if args.no_anim:
            out("오늘의 점심 운명: %s" % choice)
        else:
            animate(choice, pet.rng, sys.stdout.write, flush=sys.stdout.flush)
        if excluded:
            out("(최근에 먹은 %s은(는) 제외했어요)" % ", ".join(excluded))

    elif args.cmd == "experiment":
        write_default_experiments(pet.store.experiments_path)
        text, done = pet.experiment_today()
        out("오늘의 15분 실험: %s%s" % (text, " (완료!)" if done else ""))
        out("목록 편집: %s" % pet.store.experiments_path)

    elif args.cmd == "experiment-done":
        out("실험 완료: %s" % pet.complete_experiment())

    elif args.cmd == "followup":
        pending = pet.pending_followups()
        if not pending:
            out("물어볼 결정이 없어요.")
        for d in pending:
            ans = inp("어제 결정 만족? '%s' → %s (1~5, 엔터=건너뛰기): " % (
                " vs ".join(d["options"]), d["choice"])).strip()
            if ans.isdigit() and 1 <= int(ans) <= 5:
                pet.rate_decision(d["id"], int(ans))
            else:
                pet.dismiss_followup(d["id"])

    elif args.cmd == "history":
        h = pet.satisfaction_history()
        if not h:
            out("아직 만족도 기록이 없어요.")
        for k, (avg, n) in sorted(h.items()):
            out("%s: 평균 %.2f (%d건)" % (k, avg, n))

    elif args.cmd == "import-todos":
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            out("파일을 읽을 수 없어요: %s" % e)
            return 1
        added = pet.import_markdown(text)
        out("%d개 가져왔어요." % len(added))
        for t in added:
            out("  #%d %s (%d분)" % (t["id"], t["title"], t["effort"]))
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    return run(sys.argv[1:] if argv is None else argv, pet=Pet(Store()))
