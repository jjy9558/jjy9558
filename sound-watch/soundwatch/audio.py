"""오디오 입력 추상화: WAV 읽기(stdlib wave), 모노/16 kHz 변환, PCM 청크 디코딩."""
from __future__ import annotations

import struct
import sys
import wave
from array import array
from itertools import accumulate
from typing import Iterator, List, Sequence, Tuple

TARGET_SR = 16000


class AudioError(Exception):
    pass


def read_wav(path: str) -> Tuple[List[float], int]:
    """WAV -> (모노 float 샘플 [-1,1], 샘플레이트). 8/16/24/32비트 정수 PCM 지원."""
    try:
        wf = wave.open(path, "rb")
    except (wave.Error, EOFError) as e:
        raise AudioError("WAV를 열 수 없습니다 (%s): %s — 부동소수/압축 WAV는 16비트 PCM으로 변환해 주세요" % (path, e))
    with wf:
        nch, width, sr, nframes = wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes()
        raw = wf.readframes(nframes)
    return decode_pcm(raw, width, nch), sr


def decode_pcm(raw: bytes, width: int, nch: int = 1) -> List[float]:
    if width == 2:
        a = array("h")
        a.frombytes(raw[: len(raw) // 2 * 2])
        if sys.byteorder == "big":
            a.byteswap()
        vals = [v / 32768.0 for v in a]
    elif width == 1:
        vals = [(v - 128) / 128.0 for v in raw]
    elif width == 3:
        n = len(raw) // 3
        vals = []
        for i in range(n):
            b = raw[3 * i:3 * i + 3]
            v = b[0] | (b[1] << 8) | (b[2] << 16)
            if v & 0x800000:
                v -= 0x1000000
            vals.append(v / 8388608.0)
    elif width == 4:
        n = len(raw) // 4
        vals = [v / 2147483648.0 for v in struct.unpack("<%di" % n, raw[: n * 4])]
    else:
        raise AudioError("지원하지 않는 샘플 폭: %d바이트" % width)
    if nch > 1:
        n = len(vals) // nch
        vals = [sum(vals[i * nch:(i + 1) * nch]) / nch for i in range(n)]
    return vals


def int16_bytes_to_floats(raw: bytes) -> List[float]:
    """리틀엔디언 Int16 PCM -> float 리스트 (홀수 바이트는 AudioError)."""
    if len(raw) % 2:
        raise AudioError("PCM 길이가 홀수 바이트입니다")
    a = array("h")
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    return [v / 32768.0 for v in a]


def floats_to_int16(x: Sequence[float]) -> array:
    a = array("h", (max(-32768, min(32767, int(round(v * 32767.0)))) for v in x))
    return a


def write_wav(path: str, samples: Sequence[float], sr: int = TARGET_SR):
    a = floats_to_int16(samples)
    if sys.byteorder == "big":
        a.byteswap()
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(a.tobytes())


def resample(x: Sequence[float], sr_in: int, sr_out: int = TARGET_SR) -> List[float]:
    """안티앨리어싱 박스 필터(누적합 적분) + 선형 보간 리샘플. 순수 파이썬.

    박스 필터라 고역이 약간 눌린다(출력 나이퀴스트 부근 수 dB). 정밀 측정에는 16 kHz 로 직접 녹음한 파일 권장.
    """
    if sr_in == sr_out:
        return list(x)
    n_in = len(x)
    if n_in == 0:
        return []
    ratio = sr_in / float(sr_out)
    if ratio < 1.0:  # 업샘플: 선형 보간
        n_out = int(n_in / ratio)
        out = []
        for k in range(n_out):
            p = k * ratio
            i = int(p)
            f = p - i
            a = x[i]
            b = x[i + 1] if i + 1 < n_in else a
            out.append(a + (b - a) * f)
        return out
    cum = [0.0]
    cum.extend(accumulate(x))
    n_out = int(n_in / ratio)
    out = []

    def integ(p: float) -> float:
        i = int(p)
        if i >= n_in:
            return cum[n_in]
        return cum[i] + (cum[i + 1] - cum[i]) * (p - i)

    half = ratio / 2.0
    for k in range(n_out):
        c = k * ratio + half
        lo = max(0.0, c - half)
        hi = min(float(n_in), c + half)
        out.append((integ(hi) - integ(lo)) / (hi - lo))
    return out


def iter_chunks(x: Sequence[float], size: int) -> Iterator[Sequence[float]]:
    for i in range(0, len(x), size):
        yield x[i:i + size]


def load_16k_mono(path: str) -> List[float]:
    x, sr = read_wav(path)
    return resample(x, sr, TARGET_SR)
