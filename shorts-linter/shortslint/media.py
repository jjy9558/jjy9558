"""ffprobe / ffmpeg 호출과 출력 파서. 모든 호출은 인자 리스트(shell=False)."""
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple


def ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


def ffprobe_path() -> Optional[str]:
    return shutil.which("ffprobe")


def run(cmd: List[str], timeout: int = 600) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout, shell=False)
    return (p.returncode, p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


# ---------------------------------------------------------------- ffprobe
@dataclass
class VideoInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    vcodec: str = ""
    acodec: str = ""
    has_video: bool = False
    has_audio: bool = False
    container: str = ""
    size: int = 0
    bitrate: int = 0
    pix_fmt: str = ""


def _frac(s) -> float:
    try:
        if "/" in str(s):
            a, b = str(s).split("/")
            return float(a) / float(b) if float(b) else 0.0
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def parse_probe(data: dict) -> VideoInfo:
    info = VideoInfo()
    fmt = data.get("format", {}) or {}
    info.container = fmt.get("format_name", "")
    info.size = int(_num(fmt.get("size")))
    info.bitrate = int(_num(fmt.get("bit_rate")))
    info.duration = _num(fmt.get("duration"))
    for s in data.get("streams", []) or []:
        t = s.get("codec_type")
        if t == "video" and not info.has_video and \
                (s.get("disposition") or {}).get("attached_pic", 0) != 1:
            info.has_video = True
            info.vcodec = s.get("codec_name", "")
            w, h = int(_num(s.get("width"))), int(_num(s.get("height")))
            rot = _num((s.get("tags") or {}).get("rotate"))
            for sd in s.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = _num(sd["rotation"])
            if int(abs(rot)) % 180 == 90:
                w, h = h, w
            info.width, info.height = w, h
            info.fps = _frac(s.get("avg_frame_rate")) or _frac(s.get("r_frame_rate"))
            info.pix_fmt = s.get("pix_fmt", "")
            if not info.duration:
                info.duration = _num(s.get("duration"))
        elif t == "audio" and not info.has_audio:
            info.has_audio = True
            info.acodec = s.get("codec_name", "")
    return info


def probe(path: str, timeout: int = 60) -> VideoInfo:
    cmd = [ffprobe_path() or "ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    rc, out, err = run(cmd, timeout)
    if rc != 0:
        raise RuntimeError("ffprobe 실패: %s" % err.strip()[:200])
    return parse_probe(json.loads(out))


# ---------------------------------------------------------------- 필터 출력 파서
_LOUDNORM_RE = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def parse_loudnorm(stderr: str) -> Optional[dict]:
    """loudnorm print_format=json 블록 -> {input_i, input_tp, input_lra, ...} (float)."""
    ms = _LOUDNORM_RE.findall(stderr)
    if not ms:
        return None
    try:
        raw = json.loads(ms[-1])
    except ValueError:
        return None
    out = {}
    for k, v in raw.items():
        try:
            out[k] = float(v)
        except (ValueError, TypeError):
            out[k] = v
    return out


def parse_silences(stderr: str, total: float = 0.0) -> List[Tuple[float, float]]:
    """-> [(start, end)]. 끝나지 않은 무음은 total까지로 간주."""
    res, start = [], None
    for line in stderr.splitlines():
        m = re.search(r"silence_start:\s*(-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*(-?[\d.]+)", line)
        if m:
            end = float(m.group(1))
            res.append((start if start is not None else 0.0, end))
            start = None
    if start is not None and total > start:
        res.append((start, total))
    return res


def parse_black(stderr: str) -> List[Tuple[float, float]]:
    res = []
    for m in re.finditer(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)", stderr):
        res.append((float(m.group(1)), float(m.group(2))))
    return res


def parse_freeze(stderr: str, total: float = 0.0) -> List[Tuple[float, float]]:
    res, start = [], None
    for line in stderr.splitlines():
        m = re.search(r"freeze_start:\s*(-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"freeze_end:\s*(-?[\d.]+)", line)
        if m:
            res.append((start if start is not None else 0.0, float(m.group(1))))
            start = None
    if start is not None and total > start:
        res.append((start, total))
    return res


# ---------------------------------------------------------------- ffmpeg 분석
def audio_analysis(path: str, v: dict, timeout: int = 600) -> str:
    af = ("silencedetect=noise=%sdB:d=%s,loudnorm=I=%s:TP=%s:LRA=11:print_format=json"
          % (v["silence_noise_db"], v["silence_min"], v["target_lufs"], v["max_true_peak"]))
    cmd = [ffmpeg_path() or "ffmpeg", "-hide_banner", "-nostats", "-i", path,
           "-vn", "-af", af, "-f", "null", "-"]
    return run(cmd, timeout)[2]


def video_analysis(path: str, v: dict, timeout: int = 600) -> str:
    vf = ("blackdetect=d=%s:pix_th=%s,freezedetect=n=%sdB:d=%s"
          % (min(v["black_min"], 0.3), v["black_pix_th"], v["freeze_noise_db"], v["freeze_min"]))
    cmd = [ffmpeg_path() or "ffmpeg", "-hide_banner", "-nostats", "-i", path,
           "-an", "-vf", vf, "-f", "null", "-"]
    return run(cmd, timeout)[2]
