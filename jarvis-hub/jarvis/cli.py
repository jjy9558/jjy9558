import argparse
import json
import logging
import secrets
import sys
import threading

from .config import ConfigError, load_client_settings, load_hub_config, load_worker_config


def _client(args):
    from .client import HubClient
    s = load_client_settings(args.config, args.hub, args.device, args.token)
    if not s["device"] or not s["token"]:
        print("기기 id/토큰이 필요합니다: --device/--token, 환경변수 JARVIS_DEVICE/JARVIS_TOKEN, "
              "또는 client.json", file=sys.stderr)
        sys.exit(2)
    return HubClient(s["hub"], s["device"], s["token"])


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_hub(args):
    from .hub import Hub, make_server
    try:
        cfg = load_hub_config(args.config)
    except FileNotFoundError:
        print("설정 파일이 없습니다: %s (hub.example.json을 복사하세요)" % args.config, file=sys.stderr)
        return 2
    except ConfigError as e:
        print("설정 오류:\n%s" % e, file=sys.stderr)
        return 2
    hub = Hub(cfg)
    srv = make_server(hub, args.host, args.port)
    host, port = srv.server_address[:2]
    threading.Thread(target=hub.run_ticker, daemon=True, name="ticker").start()
    print("자비스 허브: http://%s:%d/  기기 %d대, DB=%s, WOL=%s" % (
        host, port, len(cfg["devices"]), cfg["db"], "켜짐" if cfg["wake"]["enabled"] else "꺼짐"))
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("주의: %s 에 바인딩했습니다. Tailscale IP(100.x)로만 여는 것을 권장합니다." % host)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop()
        srv.server_close()
    return 0


def cmd_worker(args):
    from .client import HubClient
    from .handlers import build_handlers
    from .worker import Worker
    ov = {"hub": args.hub, "device": args.device, "token": args.token,
          "caps": args.caps.split(",") if args.caps is not None else None}
    try:
        cfg = load_worker_config(args.config, ov)
    except FileNotFoundError:
        print("설정 파일이 없습니다: %s" % args.config, file=sys.stderr)
        return 2
    except ConfigError as e:
        print("설정 오류:\n%s" % e, file=sys.stderr)
        return 2
    handlers = build_handlers(cfg)
    print("워커 %s → %s  caps=%s  핸들러=%s" % (cfg["device"], cfg["hub"], ",".join(cfg["caps"]) or "-",
                                              ",".join(sorted(handlers)) or "없음"))
    return Worker(cfg, HubClient(cfg["hub"], cfg["device"], cfg["token"]), handlers).run_forever()


def cmd_say(args):
    r = _client(args).say(" ".join(args.text), direct=args.direct, file=args.file)
    if args.json:
        _print(r)
    elif r.get("ignored"):
        print("(무시됨: %s — '자비스, ...'로 시작하거나 --direct)" % r.get("reason"))
    else:
        print(r.get("answer") or "(대답 없음)")
        if r.get("jobs"):
            print("  작업: %s" % ", ".join("#%d" % j for j in r["jobs"]))
    return 0


def cmd_submit(args):
    try:
        payload = json.loads(args.payload)
    except ValueError:
        print("--payload 는 JSON이어야 합니다", file=sys.stderr)
        return 2
    caps = [c for c in (args.caps or "").split(",") if c]
    _print(_client(args).submit(args.type, payload, caps, args.priority, notify=args.notify, title=args.title))
    return 0


def cmd_status(args):
    s = _client(args).status()
    if args.json:
        _print(s)
        return 0
    c = s["counts"]
    print("작업: 진행 %d / 대기 %d / 승인대기 %d / 완료 %d / 실패 %d" % (
        c["leased"], c["queued"], c["waiting_approval"], c["done"], c["failed"]))
    for d in s["devices"]:
        print("  %s %-10s %s" % ("●" if d["online"] else "○", d["label"], ",".join(d["caps"])))
    for j in s["jobs"][:10]:
        print("  #%-4d %-16s %-16s %s" % (j["id"], j["status"], j["type"], j["title"]))
    return 0


def cmd_approve(args):
    c = _client(args)
    _print(c.reject(args.id) if args.reject else c.approve(args.id))
    return 0


def cmd_events(args):
    c = _client(args)
    since = c.events(0)["last_id"] if not args.all else 0
    try:
        while True:
            r = c.events(since, wait=25)
            for e in r["events"]:
                print("[%s] %s" % (e["kind"], e["text"]))
                since = max(since, e["id"])
    except KeyboardInterrupt:
        return 0


def cmd_wol(args):
    from .wol import send_magic_packet
    n = send_magic_packet(args.mac, args.broadcast, args.port)
    print("매직 패킷 전송: %s → %s:%d (%d바이트)" % (args.mac, args.broadcast, args.port, n))
    return 0


def cmd_token(args):
    print(secrets.token_urlsafe(24))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jarvis", description="자비스 허브 + 분산 작업 큐")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def client_opts(p):
        p.add_argument("--config", default="client.json", help="client.json (hub/device/token)")
        p.add_argument("--hub")
        p.add_argument("--device")
        p.add_argument("--token")

    p = sub.add_parser("hub", help="허브 실행 (미니PC)")
    p.add_argument("--config", default="hub.json")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(fn=cmd_hub)

    p = sub.add_parser("worker", help="워커 실행")
    p.add_argument("--config")
    p.add_argument("--hub")
    p.add_argument("--device")
    p.add_argument("--token")
    p.add_argument("--caps", help="예: gpu,llm,windows")
    p.set_defaults(fn=cmd_worker)

    p = sub.add_parser("say", help="폰 대신 말하기 (텍스트 명령)")
    client_opts(p)
    p.add_argument("text", nargs="+")
    p.add_argument("--direct", action="store_true", help="호출어 없이도 처리")
    p.add_argument("--file", help="요약할 파일 이름 (메인PC files_dir 안)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_say)

    p = sub.add_parser("submit", help="작업 직접 넣기")
    client_opts(p)
    p.add_argument("type")
    p.add_argument("--payload", default="{}")
    p.add_argument("--caps", default="")
    p.add_argument("--priority", type=int, default=0)
    p.add_argument("--title", default="")
    p.add_argument("--notify", action="store_true")
    p.set_defaults(fn=cmd_submit)

    p = sub.add_parser("status", help="상태 보기")
    client_opts(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("approve", help="승인 대기 작업 승인 (--reject 로 거절)")
    client_opts(p)
    p.add_argument("id", type=int)
    p.add_argument("--reject", action="store_true")
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("events", help="이벤트 스트림 보기")
    client_opts(p)
    p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_events)

    p = sub.add_parser("wol", help="매직 패킷 직접 보내기 (테스트)")
    p.add_argument("mac")
    p.add_argument("--broadcast", default="255.255.255.255")
    p.add_argument("--port", type=int, default=9)
    p.set_defaults(fn=cmd_wol)

    p = sub.add_parser("token", help="기기 토큰 생성")
    p.set_defaults(fn=cmd_token)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return args.fn(args)
    except Exception as e:
        from .client import HubError, HubUnavailable
        if isinstance(e, (HubError, HubUnavailable)):
            print(str(e), file=sys.stderr)
            return 1
        raise
