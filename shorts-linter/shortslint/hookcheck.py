"""첫 3초 훅 점검: 규칙 기반 점수(결정적, LLM 없음) + 템플릿 재작성 제안 + 선택적 오디오 시작 점검."""
import os
import re

from . import media, metadata, phrasebook, promise, subtitles, titles

VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v")
SENT_SPLIT = re.compile(r"(?<=[.!?。…])\s+|\n+")
NUM_RE = re.compile(r"\d")
QUOTE_RE = re.compile(r"[\"“”'‘’「」『』]")


def _stem(path):
    name = os.path.basename(path)
    return name[:-len(".meta.json")] if name.endswith(".meta.json") else os.path.splitext(name)[0]


def resolve(target, video=None):
    """target(meta.json/srt/ass/txt) -> dict(meta, cues, text, video, source)."""
    if not os.path.isfile(target):
        raise ValueError("파일 없음: %s" % target)
    base = os.path.dirname(os.path.abspath(target))
    stem = _stem(target)
    ext = os.path.splitext(target)[1].lower()
    meta, cues, text = {}, [], ""
    if ext == ".json":
        meta = metadata.load_meta(target)
    else:
        for c in (stem + ".meta.json", stem + ".json"):
            p = os.path.join(base, c)
            if os.path.isfile(p) and os.path.abspath(p) != os.path.abspath(target):
                meta = metadata.load_meta(p)
                break
    if ext in (".srt", ".ass", ".ssa"):
        cues = subtitles.load_cues(target)
    else:
        for e in (".srt", ".ass", ".ssa"):
            p = os.path.join(base, stem + e)
            if os.path.isfile(p):
                cues = subtitles.load_cues(p)
                break
    if ext == ".txt":
        with open(target, encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
    elif not cues:
        text = titles.load_transcript(meta, target if ext == ".json" else os.path.join(base, stem + ".meta.json"))
    vid = video
    if not vid and meta.get("video"):
        v = str(meta["video"])
        vid = v if os.path.isabs(v) else os.path.join(base, v)
    if not vid:
        for e in VIDEO_EXT:
            p = os.path.join(base, stem + e)
            if os.path.isfile(p):
                vid = p
                break
    return {"meta": meta, "cues": cues, "text": text, "video": vid}


def first_window(cues, text, cfg):
    """-> (훅 텍스트, 출처 설명, 경고 목록)."""
    h = cfg["hook"]
    warns = []
    if cues:
        cues = sorted(cues, key=lambda c: c.start)
        sec = h["seconds"]
        sel = [c for c in cues if c.start < sec]
        if not sel:
            sel = cues[:1]
            warns.append("첫 대사가 %.1f초에 시작 (%g초 안에 말이 없음)" % (cues[0].start, sec))
        elif sel[0].start > h["first_cue_late"]:
            warns.append("첫 대사가 %.1f초에 시작 (늦음)" % sel[0].start)
        txt = " ".join(c.text.replace("\n", " ").strip() for c in sel)
        return re.sub(r"\s+", " ", txt).strip(), "자막 0~%g초" % sec, warns
    text = (text or "").strip()
    first = SENT_SPLIT.split(text, 1)[0].strip() if text else ""
    limit = h["fallback_chars"]
    if len(re.sub(r"\s", "", first)) > limit * 3 // 2:
        out, n = [], 0
        for w in first.split():
            out.append(w)
            n += len(w)
            if n >= limit:
                break
        first = " ".join(out)
    return first, "대본 첫 문장(약 %d자)" % limit, warns


def _flat(s):
    return re.sub(r"\s+", "", s.lower())


def score_hook(window, title, cfg):
    """-> {score, status, reasons:[(✅|⚠️, msg)], parts}. 100 = 도입 40 + 길이 30 + 약속 키워드 30."""
    h = cfg["hook"]
    reasons = []
    w = window.strip()
    lead = re.sub(r"^[\s\"“”'‘’「」『』\-–—.…,]+", "", w)
    n = len(re.sub(r"\s", "", w))
    # 도입 (40)
    filler = next((f for f in h["filler_starts"] if lead.startswith(f)), None)
    feats = []
    if NUM_RE.search(w):
        feats.append("숫자")
    if "?" in w or "？" in w or any(q in w for q in h["question_words"]) or re.search(r"(까요|나요|일까|니)\s*$", w):
        feats.append("질문")
    if QUOTE_RE.search(w):
        feats.append("인용")
    conflicts = [c for c in h["conflict_words"] if c in w]
    if conflicts:
        feats.append("갈등(%s)" % ", ".join(conflicts[:2]))
    if filler:
        opener = 0
        reasons.append(("⚠️", "인사/군더더기로 시작: '%s' - 바로 갈등·숫자·질문으로 시작하세요" % filler.strip()))
    elif feats:
        opener = 32 if len(feats) == 1 else 40
        reasons.append(("✅", "구체적 요소: " + ", ".join(feats)))
    else:
        opener = 15
        reasons.append(("⚠️", "숫자·질문·인용·갈등 표현이 없음"))
    # 길이 (30)
    if n < h["min_chars"]:
        length = 15
        reasons.append(("⚠️", "너무 짧음 (%d자)" % n))
    elif n <= h["ideal_max_chars"]:
        length = 30
        reasons.append(("✅", "길이 적절 (%d자)" % n))
    elif n <= h["long_chars"]:
        length = 20
        reasons.append(("⚠️", "다소 김 (%d자)" % n))
    else:
        length = 10
        reasons.append(("⚠️", "3초에 읽기엔 김 (%d자)" % n))
    # 제목 약속 키워드 (30)
    kws = promise.keywords(re.sub(r"#\S+", " ", title or ""))
    if not kws:
        prom = 15
        reasons.append(("⚠️", "제목이 없어 약속 키워드 점검 불가 (중립)"))
    else:
        flat = _flat(w)
        hits = [k for k in kws if promise.keyword_hit(k, flat)]
        if hits:
            prom = 30
            reasons.append(("✅", "제목 키워드 포함: " + ", ".join(hits[:3])))
        else:
            prom = 0
            reasons.append(("⚠️", "제목의 약속 키워드가 첫 구간에 없음: " + ", ".join(kws[:3])))
    total = opener + length + prom
    ok = total >= h["ok_score"] and not filler
    return {"score": total, "ok": ok, "reasons": reasons,
            "parts": {"도입": opener, "길이": length, "약속": prom}}


def check_audio_start(video, cfg, run_fn=None, which=None):
    """오디오가 audio_start_max초 안에 시작하는지. -> (status, msg). status: ok/warn/skip."""
    h = cfg["hook"]
    which = which or media.ffmpeg_path
    run_fn = run_fn or media.run
    if not video:
        return "skip", "영상 파일 없음"
    if not os.path.isfile(video):
        return "skip", "영상 파일 없음: %s" % video
    ff = which()
    if not ff:
        return "skip", "ffmpeg 없음"
    sec = h["seconds"]
    cmd = [ff, "-hide_banner", "-nostats", "-t", str(sec), "-i", video, "-vn",
           "-af", "silencedetect=noise=%sdB:d=0.1" % h["audio_noise_db"], "-f", "null", "-"]
    try:
        rc, _, err = run_fn(cmd, cfg["video"].get("ffmpeg_timeout", 600))
    except (OSError, ValueError) as e:  # noqa: BLE001
        return "skip", "ffmpeg 실행 실패: %s" % e
    if rc != 0:
        return "skip", "ffmpeg 분석 실패"
    if "Stream #" in err and "Audio" not in err:
        return "skip", "오디오 스트림 없음"
    sil = media.parse_silences(err, sec)
    lead = next((e for s, e in sil if s <= 0.05), 0.0)
    if lead > h["audio_start_max"]:
        return "warn", "오디오가 %.2f초에야 시작 (기준 %.1f초 이내)" % (lead, h["audio_start_max"])
    return "ok", "오디오 시작 %.2f초 이내" % lead


def run(target, cfg, book, video=None, audio=True, run_fn=None, which=None):
    src = resolve(target, video)
    meta = src["meta"]
    window, origin, warns = first_window(src["cues"], src["text"], cfg)
    if not window:
        raise ValueError("대본(자막/원고)을 찾지 못했습니다: %s" % target)
    res = score_hook(window, meta.get("title"), cfg)
    for wmsg in warns:
        res["reasons"].append(("⚠️", wmsg))
        res["ok"] = False
    hk = cfg["hooks"]
    sug = phrasebook.suggest(book, meta, hk["suggest_count"], hk["overuse_window"], hk["overuse_max"])
    res.update(window=window, origin=origin, suggestions=sug, audio=None)
    if audio:
        res["audio"] = check_audio_start(src["video"], cfg, run_fn, which)
        if res["audio"][0] == "warn":
            res["ok"] = False
    res["fallback"] = [] if sug else phrasebook.fallback_suggestions(meta, hk["suggest_count"])
    return res


def format_result(r):
    lines = ["%s 첫 3초 훅 점검 (%d/100: 도입 %d/40, 길이 %d/30, 약속 %d/30) - %s"
             % ("✅" if r["ok"] else "⚠️", r["score"], r["parts"]["도입"], r["parts"]["길이"],
                r["parts"]["약속"], r["origin"]),
             '  "%s"' % r["window"]]
    for mark, msg in r["reasons"]:
        lines.append("  %s %s" % (mark, msg))
    if r["audio"]:
        st, msg = r["audio"]
        lines.append("  %s %s" % ({"ok": "✅", "warn": "⚠️", "skip": "⏭️"}[st],
                                  msg if st != "skip" else "오디오 시작 점검 건너뜀: " + msg))
    lines.append("  재작성 제안:")
    if r["suggestions"]:
        for i, s in enumerate(r["suggestions"], 1):
            lines.append("    %d. %s  (%s)%s" % (i, s["filled"], s["id"], " ⚠️ 최근 자주 사용" if s["overused"] else ""))
    else:
        for i, s in enumerate(r["fallback"], 1):
            lines.append("    %d. %s" % (i, s))
    return "\n".join(lines)
