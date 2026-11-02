"""CLI: play / new / serve"""
import argparse
import os
import sys

from .dice import Dice
from .engine import GameMaster, HELP
from .llm import AnthropicLLM, LLMError, OpenAICompatLLM
from .presets import PRESETS, apply_preset
from .state import World, world_path


def make_llm(args):
    if args.backend == "anthropic":
        return AnthropicLLM(model=args.model)
    return OpenAICompatLLM(base_url=args.base_url, model=args.model or "local")


def run_wizard(directory, input_fn=input, print_fn=print):
    names = list(PRESETS)
    print_fn("새 세계 만들기")
    for i, n in enumerate(names, 1):
        print_fn("  %d. %s" % (i, n))
    print_fn("  0. 직접 입력")
    sel = input_fn("프리셋 번호 [1]: ").strip() or "1"
    name = input_fn("세계 이름 (파일명, 예: mydungeon): ").strip() or "mydungeon"
    path = world_path(directory, name)
    if os.path.exists(path):
        raise FileExistsError("이미 존재하는 세계입니다: " + path)
    w = World(path)
    if sel == "0":
        w.set_bible(name=name,
                    setting=input_fn("배경 설정: ").strip(),
                    tone=input_fn("분위기(톤): ").strip() or "모험적이고 가족과 함께 즐길 수 있는 분위기",
                    rules=input_fn("특별 규칙(선택): ").strip(),
                    player_name=input_fn("플레이어 이름: ").strip() or "모험가")
        start = input_fn("시작 장소: ").strip()
        if start:
            w.add_location(start)
            w.set("location", start)
            w.db.execute("DELETE FROM locations WHERE name='시작 지점'")
        w.commit()
    else:
        try:
            preset = names[int(sel) - 1]
        except (ValueError, IndexError):
            w.close()
            os.remove(path)
            raise ValueError("잘못된 선택: " + sel)
        apply_preset(w, preset, name)
        pn = input_fn("플레이어 이름 [%s]: " % w.get("player_name")).strip()
        if pn:
            w.set_bible(player_name=pn)
            w.commit()
    print_fn("생성 완료: " + path)
    return w, path


def repl(gm, input_fn=input, print_fn=print):
    print_fn("[%s] %s" % (gm.world.get("name"), HELP))
    try:
        first = gm.start()
        if first:
            print_fn("\n" + first.narration + "\n")
        else:
            print_fn("\n(이어하기) 현재 위치: %s\n" % gm.world.location)
    except LLMError as e:
        print_fn("LLM 오류: %s" % e)
    while True:
        try:
            text = input_fn("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text in ("/종료", "/quit", "/exit"):
            break
        try:
            reply, res = gm.handle(text)
        except LLMError as e:
            print_fn("LLM 오류: %s (이 턴은 반영되지 않았을 수 있으니 /되감기를 고려하세요)" % e)
            continue
        print_fn("\n" + reply + "\n")
    gm.world.commit()
    print_fn("저장되었습니다. 다음에 이어서 하세요.")


def build_parser():
    p = argparse.ArgumentParser(prog="gmtrpg", description="Claude GM 지속 세계 TRPG")
    sub = p.add_subparsers(dest="cmd")
    for name in ("play", "serve", "new"):
        s = sub.add_parser(name)
        s.add_argument("--dir", default="worlds", help="세계 DB 폴더")
        if name == "new":
            continue
        s.add_argument("--world", required=True)
        s.add_argument("--backend", choices=["openai", "anthropic"], default="openai")
        s.add_argument("--base-url", default=None, help="OpenAI 호환 서버 (기본 http://127.0.0.1:8080/v1)")
        s.add_argument("--model", default=None)
        s.add_argument("--seed", type=int, default=None, help="주사위 시드")
        s.add_argument("--recent-turns", type=int, default=6)
        if name == "serve":
            s.add_argument("--host", default="127.0.0.1")
            s.add_argument("--port", type=int, default=8765)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.cmd:
        build_parser().print_help()
        return 1
    if args.cmd == "new":
        try:
            run_wizard(args.dir)
        except (FileExistsError, ValueError) as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    path = world_path(args.dir, args.world)
    if not os.path.exists(path):
        print("세계가 없습니다: %s\n먼저 `python -m gmtrpg new` 를 실행하세요." % path, file=sys.stderr)
        return 1
    gm = GameMaster(World(path), make_llm(args), Dice(args.seed), recent_turns=args.recent_turns)
    if args.cmd == "play":
        repl(gm)
        return 0
    from .web import make_server
    srv = make_server(gm, args.host, args.port)
    print("http://%s:%d 에서 실행 중 (Ctrl+C로 종료)" % (args.host, srv.server_address[1]))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
