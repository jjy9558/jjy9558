import math
import random
import unittest
from operator import mul

import helpers  # noqa: F401
import synth
from soundwatch import dsp


def naive_dft(x):
    n = len(x)
    return [sum(x[t] * complex(math.cos(2 * math.pi * k * t / n), -math.sin(2 * math.pi * k * t / n)) for t in range(n))
            for k in range(n)]


def measured_gain_db(freq, fs=16000, amp=0.1, sec=1.0):
    aw = dsp.AWeighting(fs)
    n = int(fs * sec)
    x = [amp * math.sin(2 * math.pi * freq * i / fs) for i in range(n)]
    y = aw.process(x)
    tail = y[n // 2:]
    return 20 * math.log10(dsp.rms(tail) / (amp / math.sqrt(2)))


class FFTTest(unittest.TestCase):
    def tearDown(self):
        dsp.USE_NUMPY = dsp.HAVE_NUMPY

    def test_fft_matches_naive_dft(self):
        r = random.Random(1)
        for n in (1, 2, 4, 8, 16, 64, 256):
            x = [complex(r.uniform(-1, 1), r.uniform(-1, 1)) for _ in range(n)]
            a, b = dsp.fft(x), naive_dft(x)
            self.assertLess(max(abs(p - q) for p, q in zip(a, b)), 1e-9, n)

    def test_rfft_pure_and_numpy_match_naive(self):
        r = random.Random(2)
        for n in (4, 8, 64, 512):
            x = [r.uniform(-1, 1) for _ in range(n)]
            ref = naive_dft(x)[: n // 2 + 1]
            dsp.USE_NUMPY = False
            self.assertLess(max(abs(p - q) for p, q in zip(dsp.rfft(x), ref)), 1e-8)
            if dsp.HAVE_NUMPY:
                dsp.USE_NUMPY = True
                self.assertLess(max(abs(p - q) for p, q in zip(dsp.rfft(x), ref)), 1e-8)

    def test_non_power_of_two_rejected(self):
        with self.assertRaises(ValueError):
            dsp.fft([0.0] * 6)
        with self.assertRaises(ValueError):
            dsp.rfft([0.0] * 12)

    def test_power_spectrum_peak_at_tone_bin(self):
        dsp.USE_NUMPY = False
        n = 1024
        x = [math.sin(2 * math.pi * 1000 * i / 16000) for i in range(n)]
        pw = dsp.rfft_power(list(map(mul, x, dsp.hann(n))))
        self.assertEqual(max(range(len(pw)), key=pw.__getitem__), 64)  # 1000 Hz / 15.625


class LevelMathTest(unittest.TestCase):
    def test_full_scale_sine_is_minus_3_dbfs(self):
        x = synth.tone(1000, 1.0, amp=1.0, attack=0, release=0)
        self.assertAlmostEqual(dsp.dbfs(dsp.rms(x)), -3.01, delta=0.02)

    def test_amplitude_scaling_is_20log(self):
        a = dsp.dbfs(dsp.rms(synth.tone(500, 0.5, 0.5, 0, 0)))
        b = dsp.dbfs(dsp.rms(synth.tone(500, 0.5, 0.05, 0, 0)))
        self.assertAlmostEqual(a - b, 20.0, delta=0.01)

    def test_silence_hits_floor_and_frame_levels(self):
        self.assertEqual(dsp.dbfs(0.0), dsp.DB_FLOOR)
        self.assertEqual(dsp.power_to_db(0.0), dsp.DB_FLOOR)
        lv = dsp.frame_levels_db(synth.silence(0.5) + synth.white(0.5, 0.1), 1600)
        self.assertEqual(len(lv), 10)
        self.assertEqual(lv[0], dsp.DB_FLOOR)
        self.assertAlmostEqual(lv[-1], -20.0, delta=0.6)

    def test_energy_average_of_levels(self):
        # 같은 길이의 60 dB 와 50 dB -> 에너지 평균 약 57.4 dB (산술평균 55 가 아님)
        self.assertAlmostEqual(dsp.db_sum_energy([60.0, 50.0]), 57.4, delta=0.05)


class AWeightingTest(unittest.TestCase):
    def test_reference_curve_known_values(self):
        for f, exp in ((100, -19.1), (1000, 0.0), (2000, 1.2), (500, -3.2)):
            self.assertAlmostEqual(dsp.a_weighting_analog_db(f), exp, delta=0.1)

    def test_digital_response_within_tolerance_of_analog(self):
        for f in (50, 100, 200, 500, 1000, 2000, 4000, 6000):
            self.assertAlmostEqual(dsp.a_weighting_digital_db(f), dsp.a_weighting_analog_db(f), delta=0.5, msg=f)

    def test_filtering_a_sine_gives_expected_gain(self):
        for f, exp, tol in ((100, -19.1, 0.6), (500, -3.2, 0.4), (1000, 0.0, 0.2), (2000, 1.2, 0.5), (4000, 1.0, 0.8)):
            self.assertAlmostEqual(measured_gain_db(f), exp, delta=tol, msg=f)

    def test_chunked_filtering_equals_whole(self):
        x = synth.white(0.5, 0.1, 5)
        whole = dsp.AWeighting().process(x)
        aw = dsp.AWeighting()
        parts = []
        for i in range(0, len(x), 777):
            parts += aw.process(x[i:i + 777])
        self.assertLess(max(abs(a - b) for a, b in zip(whole, parts)), 1e-12)

    def test_level_meter_1k_sine_reads_its_dbfs(self):
        m = dsp.LevelMeter()
        fr = m.feed(synth.tone(1000, 2.0, 0.1, 0, 0))
        self.assertEqual(len(fr), 20)
        a, z = fr[-1]
        self.assertAlmostEqual(a, -23.01, delta=0.1)
        self.assertAlmostEqual(z, -23.01, delta=0.1)

    def test_low_frequency_is_attenuated_in_a_but_not_z(self):
        m = dsp.LevelMeter()
        fr = m.feed(synth.tone(63, 2.0, 0.1, 0, 0))
        a, z = fr[-1]
        self.assertAlmostEqual(z - a, 26.2, delta=0.7)


if __name__ == "__main__":
    unittest.main()
