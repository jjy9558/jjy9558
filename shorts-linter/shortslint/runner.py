"""영상 1개 / 폴더 배치 실행."""
import os

from . import metadata, promise, subtitles, videochecks
from .model import Result, VideoReport

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v")


def _sidecar(path, exts):
    stem = os.path.splitext(path)[0]
    for e in exts:
        p = stem + e
        if os.path.isfile(p):
            return p
    return None


def check_video(path, cfg, meta_path=None):
    """-> (VideoReport, title or None)"""
    rep = VideoReport(path)
    res, _info = videochecks.run_video_checks(path, cfg["video"])
    rep.results.extend(res)
    # 자막
    sub = _sidecar(path, (".srt", ".ass", ".ssa"))
    cues = []
    if sub:
        try:
            cues = subtitles.load_cues(sub)
            rep.results.extend(subtitles.check_subtitles(cues, cfg, sub.lower().endswith((".ass", ".ssa"))))
        except Exception as e:  # noqa: BLE001
            rep.results.append(Result("자막 파싱", "warn", "%s: %s" % (os.path.basename(sub), e), "자막"))
    else:
        rep.results.append(subtitles.skipped())
    # 메타
    meta_path = meta_path or _sidecar(path, (".meta.json", ".json"))
    meta, title = None, None
    if meta_path:
        try:
            meta = metadata.load_meta(meta_path)
            title = (meta.get("title") or "").strip() or None
            rep.results.extend(metadata.check_metadata(meta, cfg, os.path.dirname(os.path.abspath(meta_path))))
        except Exception as e:  # noqa: BLE001
            rep.results.append(Result("meta.json", "fail", "읽기 실패: %s" % e, "메타데이터"))
    else:
        rep.results.append(Result("메타데이터", "skip", "meta.json 없음 (--meta 또는 <영상>.meta.json)", "메타데이터"))
    # 약속 일치
    if meta is not None:
        transcript = subtitles.cues_text(cues)
        tp = meta.get("transcript") or ""
        tfile = (tp if os.path.isabs(tp) else os.path.join(os.path.dirname(os.path.abspath(meta_path)), tp)) if tp else None
        tfile = tfile if tfile and os.path.isfile(tfile) else _sidecar(path, (".txt",))
        if tfile:
            with open(tfile, encoding="utf-8-sig", errors="replace") as f:
                transcript = (transcript + "\n" + f.read()).strip()
        rep.results.extend(promise.check_promise(meta, transcript, cfg))
    return rep, title


def list_videos(folder):
    return [os.path.join(folder, n) for n in sorted(os.listdir(folder))
            if n.lower().endswith(VIDEO_EXT) and os.path.isfile(os.path.join(folder, n))]


def check_dir(folder, cfg):
    reports, titles = [], []
    for v in list_videos(folder):
        rep, title = check_video(v, cfg)
        reports.append(rep)
        titles.append((os.path.basename(v), title))
    dups = metadata.find_duplicates(titles, cfg["metadata"]["duplicate_similarity"])
    for rep in reports:
        rep.results.extend(dups.get(os.path.basename(rep.path), []))
    return reports
