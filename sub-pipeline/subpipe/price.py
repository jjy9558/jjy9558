"""Duration-based quote (분당 단가)."""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import wave
from collections import OrderedDict
from typing import Dict, Optional

from . import srt

DEFAULT_TIERS = OrderedDict([
    ("basic", ("AI번역+감수", 5000)),
    ("entertainment", ("예능·드라마", 7000)),
    ("specialist", ("의학·IT 전문", 10000)),
])


class PriceError(RuntimeError):
    pass


def media_duration_seconds(path: str) -> float:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".srt", ".vtt"):
        cues = srt.load(path)
        if not cues:
            raise PriceError("자막에서 시간을 읽을 수 없습니다: " + path)
        return max(c.end for c in cues) / 1000.0
    if ext == ".wav":
        try:
            with wave.open(path, "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except wave.Error:
            pass
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise PriceError("영상 길이를 읽으려면 ffprobe(ffmpeg)가 필요합니다. 또는 --minutes 로 직접 지정하세요.")
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", path],
                             capture_output=True, text=True, check=True).stdout.strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError) as e:
        raise PriceError("ffprobe로 길이를 읽지 못했습니다: %s" % path) from e


def billable_minutes(seconds: float, rounding: str = "ceil") -> float:
    m = seconds / 60.0
    if rounding == "ceil":
        return float(math.ceil(m - 1e-9))
    if rounding == "half":
        return math.ceil(m * 2 - 1e-9) / 2.0
    if rounding == "none":
        return m
    raise ValueError("rounding must be ceil|half|none")


def quote(seconds: float, rate: int, rounding: str = "ceil") -> Dict[str, float]:
    bm = billable_minutes(seconds, rounding)
    return {"seconds": seconds, "minutes": seconds / 60.0, "billable_minutes": bm,
            "rate": rate, "amount": int(round(bm * rate))}


def parse_tiers(spec: Optional[str]) -> "OrderedDict[str, tuple]":
    """'basic=5000,entertainment=7000,specialist=10000' overrides default rates."""
    tiers = OrderedDict(DEFAULT_TIERS)
    if not spec:
        return tiers
    for part in spec.split(","):
        k, _, v = part.partition("=")
        k = k.strip()
        if k not in tiers or not v.strip().isdigit():
            raise PriceError("잘못된 단가 지정: %r (예: basic=5000,entertainment=7000)" % part)
        tiers[k] = (tiers[k][0], int(v))
    return tiers


def won(n: float) -> str:
    return "{:,}원".format(int(n))
