"""테스트용 합성 오디오 (16 kHz 모노, float [-1,1]). 외부 파일 없음."""
import math
import random

SR = 16000


def silence(sec):
    return [0.0] * int(SR * sec)


def tone(freq, sec, amp=0.3, attack=0.005, release=0.02, harmonics=()):
    n = int(SR * sec)
    out = []
    for i in range(n):
        t = i / SR
        env = 1.0
        if t < attack:
            env = t / attack
        rem = sec - t
        if rem < release:
            env = min(env, rem / release)
        v = math.sin(2 * math.pi * freq * t)
        for h, a in harmonics:
            v += a * math.sin(2 * math.pi * freq * h * t)
        out.append(amp * env * v)
    return out


def concat(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def white(sec, rms=0.01, seed=1):
    r = random.Random(seed)
    # 균일분포 RMS = a/sqrt(3)
    a = rms * math.sqrt(3)
    return [r.uniform(-a, a) for _ in range(int(SR * sec))]


def pink(sec, rms=0.01, seed=2):
    """Paul Kellet 근사 핑크 노이즈, RMS 를 rms 로 정규화."""
    r = random.Random(seed)
    b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0
    out = []
    for _ in range(int(SR * sec)):
        w = r.uniform(-1, 1)
        b0 = 0.99886 * b0 + w * 0.0555179
        b1 = 0.99332 * b1 + w * 0.0750759
        b2 = 0.96900 * b2 + w * 0.1538520
        b3 = 0.86650 * b3 + w * 0.3104856
        b4 = 0.55000 * b4 + w * 0.5329522
        b5 = -0.7616 * b5 - w * 0.0168980
        out.append(b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362)
        b6 = w * 0.115926
    return set_rms(out, rms)


def rms_of(x):
    return math.sqrt(sum(v * v for v in x) / max(1, len(x)))


def set_rms(x, rms):
    cur = rms_of(x)
    g = rms / cur if cur > 0 else 0.0
    return [v * g for v in x]


def mix(a, b, offset=0):
    """b 를 a 의 offset 샘플 위치에 더한다 (a 가 짧으면 확장)."""
    n = max(len(a), offset + len(b))
    out = list(a) + [0.0] * (n - len(a))
    for i, v in enumerate(b):
        out[offset + i] += v
    return out


def add_noise(sig, kind, snr_db, seed=3):
    """sig 의 '소리 나는 구간' RMS 대비 snr_db 로 배경 잡음을 깐다."""
    active = [v for v in sig if abs(v) > 1e-4]
    sig_rms = rms_of(active) if active else 0.1
    noise_rms = sig_rms / (10 ** (snr_db / 20.0))
    sec = len(sig) / SR
    nz = white(sec, noise_rms, seed) if kind == "white" else pink(sec, noise_rms, seed)
    return [s + n for s, n in zip(sig, nz)]


def pad(sig, before=1.5, after=1.5):
    return concat(silence(before), sig, silence(after))


def doorbell(f1=660.0, f2=550.0, d1=0.5, d2=0.7, amp=0.3):
    """딩-동: 두 음."""
    return concat(tone(f1, d1, amp, release=0.05), tone(f2, d2, amp, release=0.2))


def washer_melody(amp=0.3):
    notes = [(523.25, 0.22), (659.25, 0.22), (783.99, 0.22), (1046.5, 0.45)]
    return concat(*[concat(tone(f, d, amp, release=0.03), silence(0.04)) for f, d in notes])


def rice_cooker(amp=0.3):
    """삑-삑-삑 (2.7 kHz)"""
    return concat(*[concat(tone(2700, 0.18, amp), silence(0.14)) for _ in range(3)])


def microwave_ding(amp=0.3):
    return tone(2000, 0.9, amp, release=0.4, harmonics=((2, 0.2),))


def kettle_whistle(amp=0.25, sec=2.0, seed=5):
    """주전자: 3.2 kHz 근처 약간 흔들리는 휘파람 + 숨소리 잡음."""
    n = int(SR * sec)
    r = random.Random(seed)
    out = []
    ph = 0.0
    for i in range(n):
        f = 3200 + 40 * math.sin(2 * math.pi * 3 * i / SR)
        ph += 2 * math.pi * f / SR
        out.append(amp * math.sin(ph) + r.uniform(-1, 1) * 0.03)
    return out


def knock(amp=0.8, thump_hz=140.0, tau=0.02, seed=7):
    """문 두드림: 짧게 감쇠하는 잡음+저음 (약 120 ms)."""
    r = random.Random(seed)
    n = int(SR * 0.12)
    out = []
    for i in range(n):
        t = i / SR
        env = math.exp(-t / tau)
        out.append(amp * env * (0.6 * math.sin(2 * math.pi * thump_hz * t) + 0.5 * r.uniform(-1, 1)))
    return out


def knocks(count=3, interval=0.3, amp=0.8, seed=7):
    parts = []
    for i in range(count):
        k = knock(amp, seed=seed + i)
        parts.append(k + silence(max(0.0, interval - len(k) / SR)))
    return concat(*parts)


def speechlike(sec=3.0, seed=11):
    """말소리 비슷한 신호: 유성음(f0 110~190 Hz 변화, 배음)을 음절(4 Hz)로 변조."""
    n = int(SR * sec)
    out = []
    ph = 0.0
    for i in range(n):
        t = i / SR
        f0 = 150 + 40 * math.sin(2 * math.pi * 0.7 * t) + 15 * math.sin(2 * math.pi * 2.3 * t)
        ph += 2 * math.pi * f0 / SR
        v = 0.0
        for h in range(1, 20):
            fh = f0 * h
            if fh > 3500:
                break
            # 포먼트 두 개 (500, 1500 Hz) 근처 강조
            g = math.exp(-((fh - 500) / 250.0) ** 2) + 0.6 * math.exp(-((fh - 1500) / 400.0) ** 2) + 0.05
            v += g * math.sin(h * ph)
        env = 0.5 * (1 + math.sin(2 * math.pi * 4 * t - 1.2)) ** 0.7
        out.append(0.12 * env * v)
    return out
