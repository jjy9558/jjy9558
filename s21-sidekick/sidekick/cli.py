import argparse
import sys

from .config import ConfigError, load_config
from .stadium import EVENTS


def _serve(args):
    from .server import Sidekick, make_server
    try:
        cfg = load_config(args.config)
    except FileNotFoundError:
        print("설정 파일이 없습니다: %s (config.example.json을 복사하세요)" % args.config, file=sys.stderr)
        return 2
    except ConfigError as e:
        print("설정 오류:\n" + str(e), file=sys.stderr)
        return 2
    host = args.host or cfg["server"]["host"]
    port = args.port if args.port is not None else cfg["server"]["port"]
    app = Sidekick.from_config(cfg)
    srv = make_server(app, host, port)
    print("S21 Sidekick: http://%s:%d/  (알람 %s, 판정=%s, 디버거=%s)" % (
        host, srv.server_address[1], cfg["alarm"]["time"],
        "VLM" if app.alarm.vision else "수동확인", cfg["debugger"]["mode"]))
    if host not in ("127.0.0.1", "localhost"):
        print("주의: %s 에 바인딩했습니다. 같은 네트워크의 기기가 접근할 수 있으니 토큰을 꼭 길게." % host)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def notify(event, detail="", lines=None, config_path=None, url=None, token=None, environ=None,
           out=None, err=None):
    """커밋 관중석 알림. 종료 코드: 0 성공(또는 레이트 리밋으로 건너뜀), 1 서버/인증 실패, 2 설정 오류."""
    from .client import (ServerError, ServerUnavailable, SidekickClient, load_client_config,
                         resolve_config_path, server_token, server_url)
    out, err = out or sys.stdout, err or sys.stderr
    try:
        cfg = load_client_config(resolve_config_path(config_path, environ))
    except (OSError, ValueError) as e:
        err.write("설정을 읽지 못했습니다: %s\n" % e)
        return 2
    tok = token or server_token(cfg, environ)
    if not tok:
        err.write("토큰이 없습니다: --token, SIDEKICK_TOKEN, 또는 config.json의 server.token\n")
        return 2
    client = SidekickClient(server_url(cfg, environ, override=url), tok, timeout=5)
    body = {"event": event, "detail": detail or ""}
    if lines is not None:
        body["lines"] = lines
    try:
        r = client.call("/api/notify", body)
    except ServerError as e:
        if e.code == 429:
            err.write("관중석 쉬는 중 (레이트 리밋, %s초 뒤 가능). 건너뜀.\n" % e.body.get("retry_after", "?"))
            return 0
        err.write("관중석 알림 실패: %s\n" % e)
        return 1
    except ServerUnavailable as e:
        err.write("%s\n" % e)
        return 1
    out.write("관중석: %s\n" % (r.get("say") or "꺼져 있음"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sidekick", description="S21 Sidekick PC 서버")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="폰 페이지와 API 서버 실행")
    s.add_argument("--config", default="config.json")
    s.add_argument("--host")
    s.add_argument("--port", type=int)

    n = sub.add_parser("notify", help="커밋 관중석: 폰에 환호/야유/불꽃 보내기")
    n.add_argument("event", choices=sorted(EVENTS))
    n.add_argument("--detail", default="", help="같이 읽어줄 짧은 설명 (예: 브랜치 이름)")
    n.add_argument("--lines", type=int, help="PR 변경 줄 수 (pr_merged: 작으면 불꽃 대신 박수)")
    n.add_argument("--config", help="기본: $SIDEKICK_CONFIG 또는 저장소 루트의 config.json")
    n.add_argument("--url", help="서버 주소 (기본: config의 server.host/port, 또는 $SIDEKICK_URL)")
    n.add_argument("--token", help="기본: $SIDEKICK_TOKEN 또는 config의 server.token")

    h = sub.add_parser("hook", help="rm -rf 결재 보고: Claude Code PreToolUse 훅 (stdin JSON)")
    h.add_argument("--config", help="기본: $SIDEKICK_CONFIG 또는 저장소 루트의 config.json")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        return _serve(args)
    if args.cmd == "notify":
        return notify(args.event, args.detail, args.lines, args.config, args.url, args.token)
    from .hook import main as hook_main
    return hook_main(args.config)
