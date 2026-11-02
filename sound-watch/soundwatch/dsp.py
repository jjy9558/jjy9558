"""순수 파이썬 DSP: dBFS, A-가중(이중선형 변환 IIR), 라딕스-2 FFT(numpy 빠른 경로 선택), 스펙트럼 특징.

- 샘플은 [-1, 1] 범위 float 리스트로 다룬다. 풀스케일 사인파의 RMS가 -3.01 dBFS 이다.
- numpy가 있으면 FFT만 빠른 경로를 쓴다(`USE_NUMPY`). 환경변수 SOUNDWATCH_NO_NUMPY=1 이면 끈다.
"""
from __future__ import annotations

import cmath
import math
import os
from operator import mul
from typing import List, Sequence, Tuple

try:  # 선택 의존성
    if os.environ.get("SOUNDWATCH_NO_NUMPY") == "1":
        raise ImportError("numpy disabled by env")
    import numpy as _np  # type: ignore
except Exception:  # pragma: no cover - numpy 없는 환경
    _np = None

HAVE_NUMPY = _np is not None
USE_NUMPY = HAVE_NUMPY  # 테스트에서 토글 가능

DB_FLOOR = -120.0
_EPS_POWER = 10 ** (DB_FLOOR / 10.0)


# ----------------------------------------------------------------------------- 레벨 수학
def power_to_db(p: float) -> float:
    """평균 제곱(파워) -> dB. 바닥 DB_FLOOR 로 클램프."""
    if p <= _EPS_POWER:
        return DB_FLOOR
    return 10.0 * math.log10(p)


def rms(x: Sequence[float]) -> float:
    n = len(x)
    if n == 0:
        return 0.0
    return math.sqrt(sum(map(mul, x, x)) / n)


def dbfs(rms_value: float) -> float:
    """RMS(풀스케일=1.0) -> dBFS. 풀스케일 사인(RMS 0.7071)은 -3.01 dBFS."""
    if rms_value <= 0:
        return DB_FLOOR
    return max(DB_FLOOR, 20.0 * math.log10(rms_value))


def frame_levels_db(x: Sequence[float], frame: int) -> List[float]:
    """frame 샘플마다 RMS dBFS (마지막 모자란 프레임은 버림)."""
    out = []
    for i in range(0, len(x) - frame + 1, frame):
        seg = x[i:i + frame]
        out.append(power_to_db(sum(map(mul, seg, seg)) / frame))
    return out


def db_sum_energy(levels_db: Sequence[float]) -> float:
    """레벨(dB) 목록의 에너지 평균(등가레벨, Leq)."""
    if not levels_db:
        return DB_FLOOR
    p = sum(10 ** (v / 10.0) for v in levels_db) / len(levels_db)
    return power_to_db(p)


# ----------------------------------------------------------------------------- FFT
_TW_CACHE = {}  # type: dict


def _twiddles(n: int) -> List[complex]:
    """exp(-2*pi*i*k/n), k < n/2"""
    tw = _TW_CACHE.get(n)
    if tw is None:
        tw = [cmath.exp(-2j * math.pi * k / n) for k in range(n // 2)]
        _TW_CACHE[n] = tw
    return tw


def _is_pow2(n: int) -> bool:
    return n >= 1 and (n & (n - 1)) == 0


def _fft_rec(x: List[complex]) -> List[complex]:
    n = len(x)
    if n == 1:
        return x
    if n == 2:
        a, b = x
        return [a + b, a - b]
    if n == 4:
        a, b, c, d = x
        s0 = a + c
        s1 = a - c
        t0 = b + d
        t1 = (b - d) * (-1j)
        return [s0 + t0, s1 + t1, s0 - t0, s1 - t1]
    e = _fft_rec(x[0::2])
    o = _fft_rec(x[1::2])
    t = [w * v for w, v in zip(_twiddles(n), o)]
    return [a + b for a, b in zip(e, t)] + [a - b for a, b in zip(e, t)]


def fft(x: Sequence[complex]) -> List[complex]:
    """복소 FFT (길이는 2의 거듭제곱). 자체 라딕스-2 구현."""
    n = len(x)
    if not _is_pow2(n):
        raise ValueError("FFT 길이는 2의 거듭제곱이어야 합니다: %d" % n)
    return _fft_rec([complex(v) for v in x])


def _rfft_py(x: Sequence[float]) -> List[complex]:
    n = len(x)
    if n < 4:
        return fft(x)[: n // 2 + 1]
    h = n // 2
    z = list(map(complex, x[0::2], x[1::2]))
    zz = _fft_rec(z)
    tw = _TW_CACHE.get(("full", n))
    if tw is None:
        tw = [cmath.exp(-2j * math.pi * k / n) for k in range(h + 1)]
        _TW_CACHE[("full", n)] = tw
    out = []
    for k in range(h + 1):
        zk = zz[k % h]
        zc = zz[(h - k) % h].conjugate()
        fe = (zk + zc) * 0.5
        fo = (zk - zc) * (-0.5j)  # (zk - zc) / (2j)
        out.append(fe + tw[k] * fo)
    return out


def rfft(x: Sequence[float]) -> List[complex]:
    """실수 입력 FFT, 길이 n/2+1 (DC..나이퀴스트)."""
    n = len(x)
    if not _is_pow2(n):
        raise ValueError("FFT 길이는 2의 거듭제곱이어야 합니다: %d" % n)
    if USE_NUMPY and _np is not None:
        return _np.fft.rfft(_np.asarray(x, dtype=float)).tolist()
    return _rfft_py(x)


def rfft_power(x: Sequence[float]) -> List[float]:
    """|rfft|^2 (파워 스펙트럼, 정규화 없음)."""
    n = len(x)
    if USE_NUMPY and _np is not None and _is_pow2(n):
        sp = _np.fft.rfft(_np.asarray(x, dtype=float))
        return (sp.real * sp.real + sp.imag * sp.imag).tolist()
    return [c.real * c.real + c.imag * c.imag for c in rfft(x)]


_HANN = {}  # type: dict


def hann(n: int) -> List[float]:
    w = _HANN.get(n)
    if w is None:
        w = [0.5 - 0.5 * math.cos(2.0 * math.pi * k / n) for k in range(n)]
        _HANN[n] = w
    return w


# ----------------------------------------------------------------------------- A-가중
A_POLES_HZ = (20.598997, 107.65265, 737.86223, 12194.217)


def a_weighting_analog_db(f: float) -> float:
    """IEC 61672 A-가중 해석식(아날로그 기준값). 1 kHz 에서 약 0 dB."""
    if f <= 0:
        return -200.0
    f2 = f * f
    ra = (12194.217 ** 2 * f2 * f2) / (
        (f2 + 20.598997 ** 2)
        * math.sqrt((f2 + 107.65265 ** 2) * (f2 + 737.86223 ** 2))
        * (f2 + 12194.217 ** 2)
    )
    return 20.0 * math.log10(ra) + 2.0


def _bilinear(b: Tuple[float, float, float], a: Tuple[float, float, float], fs: float):
    """s 영역 2차 구간 (b2 s^2+b1 s+b0)/(a2 s^2+a1 s+a0) -> z 영역 (b0,b1,b2,a1,a2)."""
    b2, b1, b0 = b
    a2, a1, a0 = a
    c = 2.0 * fs
    c2 = c * c
    n0 = b2 * c2 + b1 * c + b0
    n1 = -2.0 * b2 * c2 + 2.0 * b0
    n2 = b2 * c2 - b1 * c + b0
    d0 = a2 * c2 + a1 * c + a0
    d1 = -2.0 * a2 * c2 + 2.0 * a0
    d2 = a2 * c2 - a1 * c + a0
    return (n0 / d0, n1 / d0, n2 / d0, d1 / d0, d2 / d0)


def _biquad_response(coef, f: float, fs: float) -> complex:
    b0, b1, b2, a1, a2 = coef
    w = 2.0 * math.pi * f / fs
    z1 = cmath.exp(-1j * w)
    z2 = z1 * z1
    return (b0 + b1 * z1 + b2 * z2) / (1.0 + a1 * z1 + a2 * z2)


def design_a_weighting(fs: float = 16000.0):
    """A-가중 IIR: 2차 구간 3개(이중선형 변환으로 20.6/107.7/737.9 Hz 극 + 1차 보정 극), 1 kHz 0 dB 정규화.

    12.2 kHz 이중극은 fs=16 kHz 에서 이중선형 변환의 주파수 왜곡이 커서(6 kHz 에서 -4 dB 등)
    쓰지 않고, 그 효과(고역의 완만한 감쇠)를 극 p=0.11 인 1차 저역통과로 대신한다.
    fs=16 kHz 에서 20 Hz-7 kHz 범위 해석식 대비 최대 오차 약 0.15 dB (7 kHz 이상은 보장 안 함).
    다른 fs 에서는 p 를 다시 맞춰야 하므로 16 kHz 전용으로 취급한다.
    """
    w1, w2, w3 = [2.0 * math.pi * f for f in A_POLES_HZ[:3]]
    sec1 = _bilinear((1.0, 0.0, 0.0), (1.0, 2 * w1, w1 * w1), fs)
    sec2 = _bilinear((1.0, 0.0, 0.0), (1.0, w2 + w3, w2 * w3), fs)
    p = _HF_POLE
    sec3 = (1.0 - p, 0.0, 0.0, -p, 0.0)
    secs = [sec1, sec2, sec3]
    h = 1.0 + 0j
    for s in secs:
        h *= _biquad_response(s, 1000.0, fs)
    g = 1.0 / abs(h)
    s0 = secs[0]
    secs[0] = (s0[0] * g, s0[1] * g, s0[2] * g, s0[3], s0[4])
    return secs


_HF_POLE = 0.11


def a_weighting_digital_db(f: float, fs: float = 16000.0) -> float:
    """설계된 디지털 필터의 주파수 응답(dB)."""
    h = 1.0 + 0j
    for s in design_a_weighting(fs):
        h *= _biquad_response(s, f, fs)
    return 20.0 * math.log10(max(abs(h), 1e-30))


class AWeighting:
    """상태를 유지하는 스트리밍 A-가중 필터 (전치 직접형 II 3구간 전개)."""

    def __init__(self, fs: float = 16000.0):
        self.fs = fs
        self.sections = design_a_weighting(fs)
        self.reset()

    def reset(self):
        self.state = [0.0] * 6

    def process(self, xs: Sequence[float]) -> List[float]:
        (b0a, b1a, b2a, a1a, a2a), (b0b, b1b, b2b, a1b, a2b), (b0c, b1c, b2c, a1c, a2c) = self.sections
        z1a, z2a, z1b, z2b, z1c, z2c = self.state
        out = []
        append = out.append
        for x in xs:
            y = b0a * x + z1a
            z1a = b1a * x - a1a * y + z2a
            z2a = b2a * x - a2a * y
            x = y
            y = b0b * x + z1b
            z1b = b1b * x - a1b * y + z2b
            z2b = b2b * x - a2b * y
            x = y
            y = b0c * x + z1c
            z1c = b1c * x - a1c * y + z2c
            z2c = b2c * x - a2c * y
            append(y)
        self.state = [z1a, z2a, z1b, z2b, z1c, z2c]
        return out


class Biquad:
    """일반 2차 IIR (RBJ 저역통과 등). 클립 프라이버시용 저역통과에 사용."""

    def __init__(self, coef):
        self.coef = coef
        self.z1 = 0.0
        self.z2 = 0.0

    @staticmethod
    def lowpass(fc: float, fs: float, q: float = 0.7071067811865476) -> "Biquad":
        w0 = 2.0 * math.pi * fc / fs
        alpha = math.sin(w0) / (2.0 * q)
        cw = math.cos(w0)
        a0 = 1.0 + alpha
        b0 = (1.0 - cw) / 2.0 / a0
        b1 = (1.0 - cw) / a0
        b2 = b0
        a1 = -2.0 * cw / a0
        a2 = (1.0 - alpha) / a0
        return Biquad((b0, b1, b2, a1, a2))

    def process(self, xs: Sequence[float]) -> List[float]:
        b0, b1, b2, a1, a2 = self.coef
        z1, z2 = self.z1, self.z2
        out = []
        for x in xs:
            y = b0 * x + z1
            z1 = b1 * x - a1 * y + z2
            z2 = b2 * x - a2 * y
            out.append(y)
        self.z1, self.z2 = z1, z2
        return out


def lowpass4(xs: Sequence[float], fc: float, fs: float) -> List[float]:
    """4차 버터워스 저역통과 (2구간)."""
    s1 = Biquad.lowpass(fc, fs, 0.5411961001461969)
    s2 = Biquad.lowpass(fc, fs, 1.3065629648763766)
    return s2.process(s1.process(xs))


class LevelMeter:
    """스트리밍 레벨 미터: 100 ms(기본) 프레임마다 (A-가중 dBFS, 비가중 dBFS).

    시간 가중은 'Fast(125 ms 지수)'가 아니라 100 ms 직사각형 평균 에너지(LAeq,100ms 상당)이다.
    """

    def __init__(self, fs: int = 16000, frame_ms: int = 100):
        self.fs = fs
        self.frame = int(fs * frame_ms / 1000)
        self.aw = AWeighting(fs)
        self._raw: List[float] = []
        self._fil: List[float] = []

    def reset(self):
        self.aw.reset()
        self._raw = []
        self._fil = []

    def feed(self, samples: Sequence[float]) -> List[Tuple[float, float]]:
        fil = self.aw.process(samples)
        self._raw.extend(samples)
        self._fil.extend(fil)
        f = self.frame
        out = []
        n = len(self._raw) // f
        for i in range(n):
            r = self._raw[i * f:(i + 1) * f]
            a = self._fil[i * f:(i + 1) * f]
            out.append((power_to_db(sum(map(mul, a, a)) / f), power_to_db(sum(map(mul, r, r)) / f)))
        if n:
            del self._raw[:n * f]
            del self._fil[:n * f]
        return out
