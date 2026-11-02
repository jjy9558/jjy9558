"""영상 검사 (ffprobe/ffmpeg). ffmpeg가 없으면 해당 검사는 SKIP."""
import os

from . import media
from .model import FAIL, OK, SKIP, WARN, Result

CAT = "영상"


def _r(name, status, msg):
    return Result(name, status, msg, CAT)


def check_size(path, v):
    mb = os.path.getsize(path) / 1048576.0
    if mb > v["max_size_mb"]:
        return _r("파일 크기", FAIL, "%.1f MB > 최대 %s MB" % (mb, v["max_size_mb"]))
    if mb > v["warn_size_mb"]:
        return _r("파일 크기", WARN, "%.1f MB (업로드 시간 주의, 권장 <= %s MB)" % (mb, v["warn_size_mb"]))
    return _r("파일 크기", OK, "%.1f MB" % mb)


def checks_from_info(info, v):
    out = []
    d = info.duration
    if not info.has_video:
        return [_r("비디오 스트림", FAIL, "비디오 스트림이 없습니다")]
    # 길이
    if d > v["max_duration"]:
        out.append(_r("길이", FAIL, "%.1f초 > 최대 %ss" % (d, v["max_duration"])))
    elif v.get("warn_over") and d > v["warn_over"]:
        out.append(_r("길이", WARN, "%.1f초 (%ss 초과: 시청 지속률 주의)" % (d, v["warn_over"])))
    elif d < v["min_duration"]:
        out.append(_r("길이", WARN, "%.1f초 - 너무 짧음 (< %ss)" % (d, v["min_duration"])))
    else:
        out.append(_r("길이", OK, "%.1f초" % d))
    # 화면비
    w, h = info.width, info.height
    target = v["aspect"][0] / float(v["aspect"][1])
    ratio = w / float(h) if h else 0
    if h and abs(ratio - target) <= v["aspect_tolerance"] * target:
        out.append(_r("화면비", OK, "%dx%d (9:16)" % (w, h)))
    else:
        out.append(_r("화면비", FAIL, "%dx%d (%.3f) - 9:16 세로 영상이 아님" % (w, h, ratio)))
    # 해상도
    if w >= v["min_width"] and h >= v["min_height"]:
        out.append(_r("해상도", OK, "%dx%d" % (w, h)))
    elif w < v["hard_min_width"] or h < v["hard_min_height"]:
        out.append(_r("해상도", FAIL, "%dx%d < %dx%d" % (w, h, v["hard_min_width"], v["hard_min_height"])))
    else:
        out.append(_r("해상도", WARN, "%dx%d < 권장 %dx%d" % (w, h, v["min_width"], v["min_height"])))
    # fps
    f = info.fps
    if f < v["hard_min_fps"]:
        out.append(_r("프레임레이트", FAIL, "%.2f fps (너무 낮음)" % f))
    elif f < v["min_fps"] - 0.5 or f > v["max_fps"] + 0.5:
        out.append(_r("프레임레이트", WARN, "%.2f fps (권장 %s~%s)" % (f, v["min_fps"], v["max_fps"])))
    else:
        out.append(_r("프레임레이트", OK, "%.2f fps" % f))
    # 코덱/컨테이너
    cont = set(info.container.split(","))
    probs = []
    if not cont & set(v["containers"]):
        probs.append("컨테이너 %s" % info.container)
    if info.vcodec not in v["video_codecs"]:
        probs.append("비디오 코덱 %s" % info.vcodec)
    if info.has_audio and info.acodec not in v["audio_codecs"]:
        probs.append("오디오 코덱 %s" % info.acodec)
    if info.pix_fmt and info.pix_fmt not in ("yuv420p", "yuvj420p", "yuv420p10le"):
        probs.append("pix_fmt %s (yuv420p 권장)" % info.pix_fmt)
    if probs:
        out.append(_r("코덱/컨테이너", WARN, "; ".join(probs) + " - H.264+AAC MP4 권장"))
    else:
        out.append(_r("코덱/컨테이너", OK, "%s / %s / %s" % (info.container.split(",")[0], info.vcodec, info.acodec or "-")))
    # 오디오 유무
    out.append(_r("오디오", OK, "%s" % info.acodec) if info.has_audio
               else _r("오디오", FAIL, "오디오 스트림 없음 (나레이션 누락?)"))
    return out


def checks_from_audio(stderr, info, v):
    out = []
    ln = media.parse_loudnorm(stderr)
    if ln is None:
        out.append(_r("라우드니스", WARN, "loudnorm 결과를 파싱하지 못했습니다"))
    else:
        i, tp = ln.get("input_i"), ln.get("input_tp")
        if not isinstance(i, float) or i == float("-inf") or i < -69:
            out.append(_r("라우드니스", FAIL, "사실상 무음 트랙"))
        else:
            diff = i - v["target_lufs"]
            msg = "%.1f LUFS (목표 %s, 차이 %+.1f LU)" % (i, v["target_lufs"], diff)
            if abs(diff) > v["lufs_fail"]:
                out.append(_r("라우드니스", FAIL, msg))
            elif abs(diff) > v["lufs_warn"]:
                out.append(_r("라우드니스", WARN, msg))
            else:
                out.append(_r("라우드니스", OK, msg))
        if isinstance(tp, float):
            msg = "%.1f dBTP (한도 %s)" % (tp, v["max_true_peak"])
            if tp > 0:
                out.append(_r("트루피크", FAIL, msg + " - 클리핑"))
            elif tp > v["max_true_peak"]:
                out.append(_r("트루피크", WARN, msg))
            else:
                out.append(_r("트루피크", OK, msg))
    sil = media.parse_silences(stderr, info.duration)
    total = sum(e - s for s, e in sil)
    long_sil = [(s, e) for s, e in sil if e - s >= v["silence_min"]]
    if not sil:
        out.append(_r("무음 구간", OK, "%ss 이상 무음 없음" % v["silence_min"]))
    else:
        fmt = ", ".join("%.1f~%.1fs" % (s, e) for s, e in long_sil[:4])
        notes = []
        status = OK
        if long_sil:
            status = WARN
            notes.append("긴 무음 %d곳 (%s)" % (len(long_sil), fmt))
        if info.duration and total / info.duration > v["silence_total_ratio"]:
            status = WARN
            notes.append("무음 비율 %.0f%%" % (100 * total / info.duration))
        head = [x for x in sil if x[0] < 0.2 and x[1] - x[0] >= v["silence_start_warn"]]
        if head:
            status = WARN
            notes.append("시작 무음 %.1fs" % (head[0][1] - head[0][0]))
        out.append(_r("무음 구간", status, "; ".join(notes) or "문제 없음 (총 %.1fs)" % total))
    return out


def checks_from_video(stderr, info, v):
    out = []
    black = media.parse_black(stderr)
    hook = v["hook_seconds"]
    head = [b for b in black if b[0] <= 0.1]
    if head and head[0][1] >= hook:
        out.append(_r("첫 프레임 훅", FAIL, "처음 %.1fs가 검은 화면 (0~%.1fs)" % (hook, head[0][1])))
    elif head:
        out.append(_r("첫 프레임 훅", WARN, "시작 블랙 0~%.2fs" % head[0][1]))
    else:
        out.append(_r("첫 프레임 훅", OK, "처음 %.1fs 블랙 없음" % hook))
    rest = [b for b in black if b[0] > 0.1 and b[1] - b[0] >= v["black_min"]]
    if rest:
        out.append(_r("블랙 프레임", WARN, "%d곳: %s" % (len(rest), ", ".join("%.1f~%.1fs" % b for b in rest[:4]))))
    else:
        out.append(_r("블랙 프레임", OK, "중간/끝 블랙 없음"))
    fr = media.parse_freeze(stderr, info.duration)
    ftotal = sum(e - s for s, e in fr)
    fhead = [f for f in fr if f[0] <= 0.1 and f[1] - f[0] >= hook]
    msgs = []
    if fhead:
        msgs.append("처음 %.1fs 정지 화면" % (fhead[0][1] - fhead[0][0]))
    if info.duration and ftotal / info.duration > v["freeze_total_ratio"]:
        msgs.append("정지 화면 비율 %.0f%% (줌/팬 효과 없는 정지 이미지?)" % (100 * ftotal / info.duration))
    elif fr:
        pass
    if msgs:
        out.append(_r("정지 화면", WARN, "; ".join(msgs)))
    else:
        out.append(_r("정지 화면", OK, "%d곳, 총 %.1fs" % (len(fr), ftotal) if fr else "정지 구간 없음"))
    return out


def run_video_checks(path, v):
    """모든 영상 검사. ffprobe/ffmpeg 부재 시 해당 검사만 SKIP."""
    results = [check_size(path, v)]
    if not media.ffprobe_path():
        results.append(_r("ffprobe 기반 검사", SKIP, "ffprobe 미설치 - 길이/화면비/해상도/fps/코덱/오디오 검사 건너뜀"))
        results.append(_r("ffmpeg 분석", SKIP, "ffmpeg 미설치 - 라우드니스/무음/블랙/정지 검사 건너뜀"))
        return results, None
    try:
        info = media.probe(path)
    except Exception as e:  # noqa: BLE001
        results.append(_r("ffprobe", FAIL, "영상을 읽을 수 없음: %s" % e))
        return results, None
    results.extend(checks_from_info(info, v))
    if not info.has_video:
        return results, info
    if not media.ffmpeg_path():
        results.append(_r("ffmpeg 분석", SKIP, "ffmpeg 미설치 - 라우드니스/무음/블랙/정지 검사 건너뜀"))
        return results, info
    t = v.get("ffmpeg_timeout", 600)
    try:
        if info.has_audio:
            results.extend(checks_from_audio(media.audio_analysis(path, v, t), info, v))
        results.extend(checks_from_video(media.video_analysis(path, v, t), info, v))
    except Exception as e:  # noqa: BLE001
        results.append(_r("ffmpeg 분석", WARN, "분석 실패: %s" % e))
    return results, info
