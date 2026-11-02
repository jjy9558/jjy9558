"""명령행: python -m gamediary scan|caption|diary|album|config"""
import argparse
import json
import os
import sys
from datetime import date, datetime

from . import __version__
from .album import render_album
from .config import DEFAULTS, KINDS, coerce, expand, load_config, save_config
from .diary import render_diary, week_range
from .llm import LLMClient, LLMError, caption_image
from .sources import load_names, scan_sources
from .store import Store

PRIVACY = ("[개인정보 안내] 스크린샷에는 채팅·사용자 이름이 보일 수 있습니다. 캡션은 설정한 base_url "
           "서버로만 전송되며 로컬 DB에만 저장됩니다. LLM 없이 쓰려면 --no-llm 을 사용하세요.")


def _client(cfg):
    return LLMClient(cfg["base_url"], cfg["model"], cfg["api_key"], cfg["timeout"])


def cmd_config(args, cfg):
    if args.init and not os.path.exists(cfg["_path"]):
        print("설정 파일 생성:", save_config(cfg))
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        cfg[k] = coerce(k, v)
    for spec in args.add_source or []:
        path, _, kind = spec.partition("::")
        kind = kind or "folder"
        if kind not in KINDS:
            print("kind 는 %s 중 하나여야 합니다." % "/".join(KINDS), file=sys.stderr)
            return 2
        cfg["sources"].append({"path": path, "kind": kind})
    if args.set or args.add_source:
        print("저장:", save_config(cfg))
    show = {k: v for k, v in cfg.items() if not k.startswith("_")}
    print("설정 파일:", cfg["_path"])
    print(json.dumps(show, ensure_ascii=False, indent=2))
    return 0


def cmd_scan(args, cfg):
    if not cfg["sources"]:
        print("소스가 없습니다. 예: python -m gamediary config --add-source \"C:\\Program Files (x86)\\Steam\\userdata\"::steam")
        return 1
    store = Store(expand(cfg["db_path"]))
    shots = []
    for s in scan_sources(cfg, load_names(cfg)):
        if "_warn" in s:
            print("경고:", s["_warn"])
        else:
            shots.append(s)
    st = store.upsert_shots(shots, int(cfg["max_mb"] * 1048576))
    gone = store.prune_missing()
    print("발견 %d장 (신규/변경 %d, 기존 %d, 건너뜀 %d, 삭제됨 %d)" % (
        len(shots), st["new"], st["unchanged"], st["skipped"], gone))
    for r in store.skipped():
        print("  건너뜀:", r["path"], "-", r["skip_reason"])
    counts = {}
    for s in shots:
        counts[s["game"]] = counts.get(s["game"], 0) + 1
    for g, n in sorted(counts.items(), key=lambda x: -x[1]):
        print("  %s: %d장" % (g, n))
    return 0


def cmd_caption(args, cfg, client=None):
    if args.no_llm:
        print("--no-llm: 캡션 생성을 건너뜁니다 (일기·앨범은 캡션 없이 만들어집니다).")
        return 0
    print(PRIVACY)
    store = Store(expand(cfg["db_path"]))
    todo = store.pending(args.limit)
    if not todo:
        print("새로 캡션할 스크린샷이 없습니다.")
        return 0
    client = client or _client(cfg)
    ok = fail = consecutive = 0
    for i, r in enumerate(todo, 1):
        path = r["path"]
        if cfg.get("caption_source") == "thumb" and r.get("thumb") and os.path.isfile(r["thumb"]):
            path = r["thumb"]
        try:
            data = caption_image(client, path, r["game"])
            store.save_caption(r["hash"], data, getattr(client, "model", ""))
            ok += 1
            consecutive = 0
            print("[%d/%d] %s: %s" % (i, len(todo), r["game"], data["caption"]))
        except (LLMError, OSError) as e:
            fail += 1
            consecutive += 1
            print("[%d/%d] 실패: %s (%s)" % (i, len(todo), os.path.basename(path), e))
            if consecutive >= 3:
                print("연속 3회 실패하여 중단합니다. 서버(base_url)를 확인하세요.")
                break
    print("완료: 성공 %d, 실패 %d" % (ok, fail))
    return 0 if fail == 0 else 1


def cmd_diary(args, cfg, client=None):
    store = Store(expand(cfg["db_path"]))
    day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    if args.week:
        start, end = week_range(day)
        name = "diary-week-%s.md" % start.isoformat()
    else:
        start = end = day
        name = "diary-%s.md" % day.isoformat()
    if not args.no_llm and client is None:
        client = _client(cfg)
    if args.no_llm:
        client = None
    out_dir = expand(cfg["out_dir"])
    os.makedirs(out_dir, exist_ok=True)
    md = render_diary(store.all_shots(), start, end, client, out_dir,
                      cfg["session_gap_hours"], weekly=args.week)
    p = os.path.join(out_dir, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(md)
    print("일기 저장:", p)
    return 0


def cmd_album(args, cfg):
    store = Store(expand(cfg["db_path"]))
    out_dir = expand(cfg["out_dir"])
    os.makedirs(out_dir, exist_ok=True)
    page = render_album(store.all_shots(), out_dir, "게임 앨범", args.embed_thumbs)
    p = os.path.join(out_dir, "album.html")
    with open(p, "w", encoding="utf-8") as f:
        f.write(page)
    print("앨범 저장:", p)
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="gamediary", description="게임 스크린샷 AI 앨범·일기")
    ap.add_argument("--config", help="설정 파일 경로")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("config", help="설정 보기/수정")
    c.add_argument("--init", action="store_true", help="기본 설정 파일 생성")
    c.add_argument("--set", action="append", metavar="KEY=VALUE")
    c.add_argument("--add-source", action="append", metavar="PATH[::steam|gamebar|folder]")
    sub.add_parser("scan", help="스크린샷 폴더 스캔")
    c = sub.add_parser("caption", help="VLM 으로 캡션 생성")
    c.add_argument("--limit", type=int)
    c.add_argument("--no-llm", action="store_true")
    c = sub.add_parser("diary", help="Markdown 일기 생성")
    c.add_argument("--week", action="store_true", help="해당 날짜가 속한 주(월~일)")
    c.add_argument("--date", help="YYYY-MM-DD (기본: 오늘)")
    c.add_argument("--no-llm", action="store_true")
    c = sub.add_parser("album", help="HTML 앨범 생성")
    c.add_argument("--embed-thumbs", action="store_true", help="Steam 썸네일을 HTML 에 내장")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        return {"config": cmd_config, "scan": cmd_scan, "caption": cmd_caption,
                "diary": cmd_diary, "album": cmd_album}[args.cmd](args, cfg)
    except (ValueError, KeyError) as e:
        print("오류:", e, file=sys.stderr)
        return 2
