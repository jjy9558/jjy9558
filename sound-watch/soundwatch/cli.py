"""명령줄: serve / analyze / enroll / templates / report / calibrate / purge"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import threading
import time
from typing import List, Optional

from . import __version__, audio, report as rpt
from .clips import purge_old_clips
from .config import (NOMINAL_OFFSET_DB, Calibration, Paths, ensure_token, load_config, save_config,
                     set_calibration, tz_of)
from .engine import analyze_samples
from .notify import Notifier
from .store import Store
from .templates import EnrollError, TemplateStore


def _out(s: str = ""):
    print(s)


def _mmss(t: float) -> str:
    return "%d:%04.1f" % (int(t // 60), t % 60)


def _paths(args) -> Paths:
    return Paths(args.data_dir or os.environ.get("SOUNDWATCH_HOME") or "data")


def _parse_start(s: Optional[str], cfg) -> float:
    if not s:
        return time.time()
    tz = tz_of(cfg)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=tz).timestamp()
        except ValueError:
            pass
    raise SystemExit("시작 시각 형식이 올바르지 않습니다: %s (예: 2026-11-01 22:00)" % s)


# ----------------------------------------------------------------------------- 명령
def cmd_serve(args) -> int:
    from .server import SoundWatchApp, make_server
    p = _paths(args)
    cfg = load_config(p.config)
    created = ensure_token(cfg)
    if args.host:
        cfg["server"]["host"] = args.host
    if args.port is not None:
        cfg["server"]["port"] = args.port
    if created or not os.path.exists(p.config):
        save_config(p.config, cfg)
    os.makedirs(p.data_dir, exist_ok=True)
    store = Store(p.db)
    tstore = TemplateStore(p.templates).load()
    notifier = Notifier(cfg)
    app = SoundWatchApp(cfg, store, notifier, tstore, p.clips)
    host, port = cfg["server"]["host"], int(cfg["server"]["port"])
    srv = make_server(app, host, port, quiet=not args.verbose)
    port = srv.server_address[1]
    clips_on = cfg["clips"]["enabled"]
    if clips_on:
        purge_old_clips(p.clips, cfg["clips"]["retention_days"])
    _out("sound-watch %s" % __version__)
    _out("데이터 폴더: %s" % os.path.abspath(p.data_dir))
    _out("템플릿 %d개: %s" % (len(tstore.templates), ", ".join(tstore.templates) or "(없음: enroll 로 등록)"))
    _out("보정: %s" % app.engine.cal.label())
    _out("클립 저장: %s" % ("켜짐 (보관 %s일)" % cfg["clips"]["retention_days"] if clips_on else "꺼짐 (레벨만 기록)"))
    _out("폰에서 열기: http://%s:%d/#token=%s" % ("<PC주소>" if host in ("0.0.0.0", "::") else host, port, cfg["server"]["token"]))
    _out("  (HTTPS/localhost 에서만 마이크 사용 가능 — README 참고)  Ctrl+C 로 종료")

    stop = threading.Event()

    def maintenance():
        while not stop.wait(3600):
            if clips_on:
                purge_old_clips(p.clips, cfg["clips"]["retention_days"])
            store.commit()
    threading.Thread(target=maintenance, daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        _out("종료 중...")
    finally:
        stop.set()
        srv.server_close()
        app.close()
        notifier.close()
        store.close()
    return 0


def cmd_analyze(args) -> int:
    p = _paths(args)
    cfg = load_config(p.config)
    x = audio.load_16k_mono(args.wav)
    tstore = TemplateStore(p.templates).load()
    templates = [] if args.no_templates else list(tstore.templates.values())
    store = Store(p.db) if args.log else None
    start = _parse_start(args.start, cfg)
    res = analyze_samples(x, cfg, templates, store=store, start_epoch=start,
                          clips_dir=p.clips if args.log else None)
    if store:
        store.close()
    cal = Calibration.from_cfg(cfg)
    s = res["summary"]
    if args.json:
        out = {"summary": s, "noise_events": [{"start_s": e.start, "end_s": e.end, "duration_s": e.duration,
                                              "peak_db": cal.to_db(e.peak_dbfs), "leq_db": cal.to_db(e.leq_dbfs),
                                              "kind": e.kind} for e in res["noise_events"]],
               "detections": [d.to_dict() for d in res["detections"]], "level_basis": cal.label()}
        _out(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    _out("파일: %s  (%.1f초)" % (args.wav, s["duration_s"]))
    _out("레벨 기준: %s" % cal.label())
    _out("전체 Leq(A) %.1f dB, 최대(100ms) %.1f dB  [dBFS(A): %.1f / %.1f]" % (
        s["leq_db"], s["max_db"], s["leq_dbfs_a"], s["max_dbfs_a"]))
    _out("")
    _out("소음 이벤트 (기준 %.1f dB 이상, %d ms 이상): %d건" % (cfg["noise"]["threshold_db"], cfg["noise"]["min_duration_ms"], len(res["noise_events"])))
    for e in res["noise_events"]:
        _out("  %s ~ %s  %5.1f초  최고 %.1f dB  Leq %.1f dB  %s" % (_mmss(e.start), _mmss(e.end), e.duration,
                                                                  cal.to_db(e.peak_dbfs), cal.to_db(e.leq_dbfs), e.kind))
    _out("")
    _out("소리 인식: %d건 (템플릿 %d개)" % (len(res["detections"]), len(templates)))
    for d in res["detections"]:
        extra = "점수 %.2f" % d.score if d.kind == "template" else json.dumps(d.info, ensure_ascii=False)
        _out("  %s  [%s] %s  %s" % (_mmss(d.t_start), d.kind, d.name, extra))
    return 0


def cmd_enroll(args) -> int:
    p = _paths(args)
    tstore = TemplateStore(p.templates).load()
    try:
        t, msgs = tstore.enroll(args.name, args.wav, say=args.say or "", cooldown=args.cooldown,
                                threshold=args.threshold, replace=args.replace)
    except (EnrollError, audio.AudioError) as e:
        print("등록 실패: %s" % e, file=sys.stderr)
        return 2
    for m in msgs:
        _out("참고: " + m)
    _out("등록: %s — 샘플 %d개, 임계값 %.2f, 쿨다운 %g초" % (t.name, len(t.samples), t.threshold, t.cooldown))
    # 자체 검증: 등록한 파일을 실시간 경로(스트리밍)로 다시 돌려 본다. 소리 앞에 1초 이상 배경만 있어야 감지된다.
    from .engine import analyze_samples as _an
    ok = 0
    for w in args.wav:
        x = audio.load_16k_mono(w)
        r = _an(x, load_config(p.config), [t], start_epoch=0.0)
        hit = [d for d in r["detections"] if d.kind == "template" and d.name == t.name]
        ok += 1 if hit else 0
        _out("  자체 검증 %s: %s%s" % (os.path.basename(w), "감지됨" if hit else "감지 안 됨",
                                    " (점수 %.2f)" % hit[0].score if hit else " — 소리 앞에 1초 이상 배경 소리만 있게 다시 녹음하거나 --threshold 를 올려 보세요"))
    if len(t.samples) == 1:
        _out("팁: 같은 소리를 2~3번 더 녹음해 같은 이름으로 enroll 하면 임계값이 자동 조정되고 안정적입니다.")
    return 0 if ok == len(args.wav) else 1


def cmd_templates(args) -> int:
    p = _paths(args)
    tstore = TemplateStore(p.templates).load()
    if args.remove:
        if tstore.remove(args.remove):
            _out("삭제: %s" % args.remove)
            return 0
        print("없는 템플릿: %s" % args.remove, file=sys.stderr)
        return 1
    if not tstore.templates:
        _out("등록된 템플릿이 없습니다. 예: python -m soundwatch enroll 초인종 doorbell.wav")
        return 0
    for t in tstore.templates.values():
        frames = [len(s) for s in t.samples]
        _out("%-12s 샘플 %d개 (프레임 %s, 약 %s초)  임계값 %.2f  쿨다운 %g초  알림: %s" % (
            t.name, len(t.samples), "/".join(map(str, frames)), "/".join("%.1f" % (n * 0.05) for n in frames),
            t.threshold, t.cooldown, t.message()))
    return 0


def cmd_report(args) -> int:
    p = _paths(args)
    cfg = load_config(p.config)
    tz = tz_of(cfg)
    today = dt.datetime.now(tz).date()
    if args.start_date or args.end_date:
        sd = dt.date.fromisoformat(args.start_date) if args.start_date else today
        ed = dt.date.fromisoformat(args.end_date) if args.end_date else today
    elif args.days:
        ed, sd = today, today - dt.timedelta(days=args.days - 1)
    elif args.week:
        ed, sd = today, today - dt.timedelta(days=6)
    else:
        sd = ed = today
    if sd > ed:
        print("시작일이 종료일보다 늦습니다", file=sys.stderr)
        return 2
    if not os.path.exists(p.db):
        print("기록 DB가 없습니다: %s (serve 로 먼저 기록하세요)" % p.db, file=sys.stderr)
        return 1
    store = Store(p.db)
    data = rpt.build_report_data(store, cfg, sd, ed, top_n=args.top)
    store.close()
    os.makedirs(p.reports, exist_ok=True)
    stem = "report_%s_%s" % (sd.strftime("%Y%m%d"), ed.strftime("%Y%m%d"))
    out = args.out or os.path.join(p.reports, stem + ".html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(rpt.render_html(data))
    _out("리포트: %s  (이벤트 %d건, 야간 %d건)" % (out, data["total_events"], data["total_night"]))
    if not args.no_csv:
        base = os.path.splitext(out)[0]
        rpt.write_csv(base + "_events.csv", rpt.events_csv(data))
        rpt.write_csv(base + "_daily.csv", rpt.daily_csv(data))
        _out("CSV: %s_events.csv, %s_daily.csv (UTF-8 BOM, 엑셀에서 바로 열림)" % (base, base))
    if not data["cal"].calibrated:
        _out("주의: 보정하지 않은 상대값입니다. 실제 소음계와 비교해 `calibrate --offset` 으로 보정하세요.")
    return 0


def cmd_calibrate(args) -> int:
    p = _paths(args)
    cfg = load_config(p.config)
    if args.clear:
        cfg["calibration"] = {"offset_db": 0.0, "set_at": None, "note": "", "calibrated": False}
        save_config(p.config, cfg)
        _out("보정을 해제했습니다 (상대값).")
        return 0
    offset = args.offset
    if offset is None and args.meter is not None and args.shown is not None:
        offset = args.meter - args.shown
    if offset is None:
        cal = Calibration.from_cfg(cfg)
        _out("현재: %s" % cal.label())
        _out("표시 dB = A-가중 dBFS + %.0f(공칭) + 보정 %+.1f" % (NOMINAL_OFFSET_DB, cal.offset_db))
        if cal.set_at:
            _out("설정 시각: %s  메모: %s" % (cal.set_at, cal.note))
        _out("사용법: calibrate --offset 3.5   또는   calibrate --meter 62.0 --shown 58.5  (실제 소음계 값, 이 도구가 보여준 값)")
        return 0
    if abs(offset) > 40:
        print("오프셋 %+.1f dB 가 너무 큽니다 (±40 dB 이내)" % offset, file=sys.stderr)
        return 2
    set_calibration(cfg, offset, args.note or "")
    save_config(p.config, cfg)
    _out("보정 오프셋 %+.1f dB 저장. 과거 기록의 표시 값도 이 값으로 다시 계산되어 보입니다." % offset)
    _out("주의: 하나의 소리에서 맞춘 단일 값이며 검증되지 않습니다. 리포트에 '사용자 보정'으로 표기됩니다.")
    return 0


def cmd_purge(args) -> int:
    p = _paths(args)
    cfg = load_config(p.config)
    days = args.days if args.days is not None else cfg["clips"]["retention_days"]
    removed = purge_old_clips(p.clips, days)
    _out("보관 %g일 초과 클립 %d개 삭제" % (days, len(removed)))
    return 0


# ----------------------------------------------------------------------------- 파서
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", help="데이터 폴더 (기본: $SOUNDWATCH_HOME 또는 ./data)")
    ap = argparse.ArgumentParser(prog="soundwatch", description="집 안 소리 알림 + 층간소음 기록/리포트")
    ap.add_argument("--version", action="version", version="sound-watch " + __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", parents=[common], help="폰 오디오 수신 서버 실행")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--verbose", action="store_true")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("analyze", parents=[common], help="WAV 분석 (레벨/소음 이벤트/소리 인식)")
    s.add_argument("wav")
    s.add_argument("--json", action="store_true")
    s.add_argument("--log", action="store_true", help="결과를 DB에 기록")
    s.add_argument("--start", help="파일 시작 시각 (예: 2026-11-01 22:00), --log 와 함께 쓰는 것을 권장")
    s.add_argument("--no-templates", action="store_true")
    s.set_defaults(fn=cmd_analyze)

    s = sub.add_parser("enroll", parents=[common], help="소리 등록: enroll 초인종 doorbell.wav [more.wav]")
    s.add_argument("name")
    s.add_argument("wav", nargs="+")
    s.add_argument("--say", help="알림 문구 (예: '세탁이 끝났어요')")
    s.add_argument("--cooldown", type=float, help="재알림 방지 시간(초)")
    s.add_argument("--threshold", type=float, help="일치 임계값(0~1, 낮을수록 엄격). 기본 0.35")
    s.add_argument("--replace", action="store_true", help="기존 샘플을 버리고 새로 등록")
    s.set_defaults(fn=cmd_enroll)

    s = sub.add_parser("templates", parents=[common], help="등록된 템플릿 목록/삭제")
    s.add_argument("--remove", metavar="NAME")
    s.set_defaults(fn=cmd_templates)

    s = sub.add_parser("report", parents=[common], help="층간소음 리포트(HTML + CSV) 생성")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--week", action="store_true", help="오늘 포함 최근 7일")
    g.add_argument("--days", type=int, help="오늘 포함 최근 N일")
    s.add_argument("--from", dest="start_date", metavar="YYYY-MM-DD")
    s.add_argument("--to", dest="end_date", metavar="YYYY-MM-DD")
    s.add_argument("--out", help="HTML 출력 경로")
    s.add_argument("--no-csv", action="store_true")
    s.add_argument("--top", type=int, default=10)
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("calibrate", parents=[common], help="소음계와 비교한 보정 오프셋 입력")
    s.add_argument("--offset", type=float, help="더할 dB (실제 소음계 값 - 이 도구가 보여준 값)")
    s.add_argument("--meter", type=float, help="실제 소음계가 가리킨 값")
    s.add_argument("--shown", type=float, help="같은 순간 이 도구가 보여준 값")
    s.add_argument("--note", help="메모 (소음계 모델, 측정 조건 등)")
    s.add_argument("--clear", action="store_true")
    s.set_defaults(fn=cmd_calibrate)

    s = sub.add_parser("purge", parents=[common], help="보관 기한이 지난 클립 삭제")
    s.add_argument("--days", type=float)
    s.set_defaults(fn=cmd_purge)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # 윈도우 콘솔(cp949) 대비
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except audio.AudioError as e:
        print("오디오 오류: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
