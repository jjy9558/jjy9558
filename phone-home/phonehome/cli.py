"""명령줄 진입점: python -m phonehome <명령>."""
import argparse
import sys
from pathlib import Path
from typing import TextIO

from . import __version__
from . import config as C
from ._jjkit.httpkit import generate_token, serve_in_thread


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="phone-home", description='S21 폰 홈: 서비스 타일 런처 + 토큰 하나 리버스 프록시 + 사장님 책상')
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    p.add_argument("--config", help="설정 파일 (기본 ~/.phone-home/config.json, 환경변수 PHONE_HOME_CONFIG)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="서버 실행 (기본 127.0.0.1:8790)")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--verbose", action="store_true", help="요청 경로 로그 출력")
    sub.add_parser("check", help="뒤쪽 서비스 상태 확인")
    t = sub.add_parser("token", help="클라이언트 토큰")
    t.add_argument("action", choices=["new", "show"])
    i = sub.add_parser("init", help="설정 파일 새로 만들기 (토큰 생성)")
    i.add_argument("--force", action="store_true")
    return p


def _path(args) -> Path:
    return Path(args.config) if args.config else C.default_path()


def cmd_init(args, out: TextIO) -> int:
    p = _path(args)
    if p.exists() and not args.force:
        print("이미 있습니다: %s (--force 로 덮어쓰기)" % p, file=out)
        return 1
    C.write(p, C.example())
    print("만들었습니다: %s" % p, file=out)
    print("뒤쪽 서비스 토큰은 services.<이름>.token (또는 token_env/token_file)에 넣으세요.", file=out)
    return 0


def cmd_token(args, out: TextIO) -> int:
    p = _path(args)
    if args.action == "show":
        cfg = C.load(p)
        tok = cfg["server"]["token"]
        if not tok:
            print("토큰이 없습니다. python -m phonehome token new", file=out)
            return 1
        print(tok, file=out)
        return 0
    import json
    raw = {}
    if p.exists():
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = C.example()
    raw.setdefault("server", {})["token"] = generate_token()
    C.normalize(raw)
    C.write(p, raw)
    print("새 토큰을 %s 에 저장했습니다. 폰에서 http://<주소>:<포트>/#token=%s 로 한 번 여세요."
          % (p, raw["server"]["token"]), file=out)
    return 0


def cmd_check(args, out: TextIO) -> int:
    from .proxy import health_all
    from .server import App
    cfg = C.load(_path(args))
    app = App(cfg)
    res = health_all(app.backends, float(cfg["server"].get("health_timeout") or 3))
    label = {"up": "살아 있음", "down": "꺼짐/연결 실패", "auth": "토큰 확인 필요", "error": "오류"}
    bad = 0
    for name, s in cfg["services"].items():
        r = res[name]
        bad += not r["ok"]
        extra = ("%dms" % r["ms"]) if "ms" in r else r.get("error", "")
        tok = "토큰 있음" if s.get("token") else "토큰 없음"
        print("%-14s %-6s %-14s %s  (%s, %s)" % (name, "OK" if r["ok"] else "XX", label.get(r["state"], r["state"]),
                                                s["url"], extra, tok), file=out)
    if not cfg["server"]["token"]:
        print("경고: phone-home 클라이언트 토큰이 없습니다 (token new).", file=out)
    return 1 if bad else 0


def cmd_serve(args, out: TextIO) -> int:
    from .server import App, make_passthrough_servers, make_server
    cfg = C.load(_path(args))
    app = App(cfg)
    try:
        srv = make_server(app, host=args.host, port=args.port, quiet=not args.verbose)
    except ValueError as e:
        print("시작 실패: %s" % e, file=out)
        return 2
    extras = make_passthrough_servers(app, host=args.host, quiet=not args.verbose)
    for x in extras:
        serve_in_thread(x)
        print("  passthrough %s → :%d" % (x.passthrough, x.server_address[1]), file=out)
    host, port = srv.server_address[:2]
    print("phone-home: http://%s:%d/  (Ctrl+C로 종료)" % (host, port), file=out)
    if hasattr(out, "flush"):
        out.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        for x in extras:
            x.shutdown()
            x.server_close()
    return 0


COMMANDS = {"serve": cmd_serve, "check": cmd_check, "token": cmd_token, "init": cmd_init}


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # cp949 콘솔에서 한글/이모지로 죽지 않게
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args, out or sys.stdout)
    except ValueError as e:
        print("설정 오류: %s" % e, file=out or sys.stdout)
        return 2
