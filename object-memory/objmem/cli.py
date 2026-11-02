import argparse
import json
import sys

from . import ask as askmod
from . import config as cfgmod
from .core import App
from .server import make_server
from .store import fts5_available


def _out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(prog="objmem", description="내 물건 기억 (로컬 VLM)")
    p.add_argument("--config", help="설정 파일 (기본 ~/.objmem/config.json, 환경변수 OBJMEM_CONFIG)")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("config", help="설정 보기/생성")
    c.add_argument("--init", action="store_true", help="토큰이 든 새 설정 파일 생성")
    s = sub.add_parser("serve", help="수집 서버 시작")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    i = sub.add_parser("ingest", help="폴더의 JPEG 사진 가져오기")
    i.add_argument("folder")
    i.add_argument("--camera", default="")
    i.add_argument("--no-describe", action="store_true", help="VLM 호출 없이 저장만 (나중에 describe)")
    sub.add_parser("describe", help="아직 설명이 없는 사진 처리")
    a = sub.add_parser("ask", help="물건 위치 묻기")
    a.add_argument("question", nargs="+")
    a.add_argument("--llm", action="store_true", help="답변을 VLM 서버로 다듬기")
    a.add_argument("--json", action="store_true")
    sub.add_parser("purge", help="보관 기간이 지난 사진/설명 삭제")
    sub.add_parser("status", help="저장 현황")
    args = p.parse_args(argv)

    if args.cmd == "config" and args.init:
        try:
            path = cfgmod.init(args.config)
        except FileExistsError as e:
            print("이미 있습니다: %s" % e, file=sys.stderr)
            return 1
        print("생성: %s (토큰이 들어 있으니 남에게 보이지 마세요)" % path)
        return 0
    cfg = cfgmod.load(args.config)
    if args.cmd == "config":
        shown = dict(cfg)
        if shown.get("token"):
            shown["token"] = "***"
        shown["fts5_available"] = fts5_available()
        _out(shown)
        return 0
    app = App(cfg)
    if args.cmd == "serve":
        srv = make_server(app, args.host, args.port)
        host, port = srv.server_address[:2]
        if host not in ("127.0.0.1", "::1", "localhost"):
            print("경고: %s 에 바인딩합니다. 신뢰하는 네트워크(예: Tailscale)에서만 쓰세요." % host, file=sys.stderr)
        print("objmem 서버: http://%s:%d/  (폰 페이지) — Ctrl+C 로 종료" % (host, port))
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if args.cmd == "ingest":
        _out(app.ingest_folder(args.folder, args.camera, describe=not args.no_describe))
    elif args.cmd == "describe":
        _out(app.describe_pending())
    elif args.cmd == "ask":
        r = askmod.answer(app, " ".join(args.question), True if args.llm else None)
        if args.json:
            _out(r)
        else:
            print(r["answer"])
    elif args.cmd == "purge":
        _out(app.purge())
    elif args.cmd == "status":
        _out(app.store.stats())
    return 0
