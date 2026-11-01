import argparse
import sys

from .config import ConfigError, load_config
from .server import Sidekick, make_server


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sidekick", description="S21 Sidekick PC 서버")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="폰 페이지와 API 서버 실행")
    s.add_argument("--config", default="config.json")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    args = ap.parse_args(argv)

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
