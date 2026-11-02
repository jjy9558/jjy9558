import datetime as dt
import math
import os
import tempfile
import unittest

import helpers  # noqa: F401
import synth
from soundwatch import audio, config, dsp
from soundwatch.clips import ClipSaver, Ring, purge_old_clips, speech_likelihood
from soundwatch.engine import Engine, analyze_samples
from soundwatch.noise import NoiseLogger
from soundwatch.store import Store


def feed_levels(lg, levels, t0=0.0):
    for i, lv in enumerate(levels):
        lg.push(t0 + i * 0.1, lv)
    lg.flush()


def logger(thr=-40.0, **kw):
    evs = []
    kw.setdefault("hysteresis_db", 2.0)
    kw.setdefault("min_duration_s", 0.3)
    kw.setdefault("merge_gap_s", 2.0)
    return NoiseLogger(thr, on_event=evs.append, **kw), evs


Q, L = -70.0, -30.0  # 조용 / 시끄러움 (dBFS A)


class NoiseLoggerTest(unittest.TestCase):
    def test_threshold_and_min_duration(self):
        lg, evs = logger()
        feed_levels(lg, [Q] * 10 + [L] * 2 + [Q] * 30 + [L] * 3 + [Q] * 30)  # 0.2 s 는 무시, 0.3 s 는 기록
        self.assertEqual(len(evs), 1)
        self.assertAlmostEqual(evs[0].start, 4.2, delta=0.01)
        self.assertAlmostEqual(evs[0].duration, 0.3, delta=0.01)
        self.assertAlmostEqual(evs[0].peak_dbfs, L, delta=0.01)

    def test_close_events_are_merged_and_far_ones_are_not(self):
        lg, evs = logger()
        feed_levels(lg, [L] * 5 + [Q] * 10 + [L] * 5 + [Q] * 40 + [L] * 5 + [Q] * 30)  # 1.0 s 간격 -> 병합, 4 s -> 분리
        self.assertEqual(len(evs), 2)
        self.assertEqual(evs[0].n_segments, 2)
        self.assertAlmostEqual(evs[0].start, 0.0, delta=0.01)
        self.assertAlmostEqual(evs[0].end, 2.0, delta=0.01)
        self.assertAlmostEqual(evs[1].start, 6.0, delta=0.01)

    def test_hysteresis_keeps_event_open(self):
        lg, evs = logger(thr=-40.0, hysteresis_db=3.0)
        # 임계값 바로 아래(-41)로 내려와도 -43 위면 같은 구간
        feed_levels(lg, [Q] * 5 + [-35] * 3 + [-41] * 3 + [-35] * 3 + [Q] * 40)
        self.assertEqual(len(evs), 1)
        self.assertAlmostEqual(evs[0].duration, 0.9, delta=0.01)
        lg2, evs2 = logger(thr=-40.0, hysteresis_db=0.0)
        feed_levels(lg2, [Q] * 5 + [-35] * 3 + [-41] * 3 + [-35] * 3 + [Q] * 40)
        self.assertEqual(len(evs2[0:1]), 1)
        self.assertEqual(evs2[0].n_segments, 2)  # 히스테리시스가 없으면 두 구간(병합은 됨)

    def test_leq_is_energy_mean_and_peak_is_max(self):
        lg, evs = logger()
        feed_levels(lg, [Q] * 5 + [-30] * 5 + [-40] * 5 + [Q] * 40)
        e = evs[0]
        self.assertAlmostEqual(e.peak_dbfs, -30.0, delta=0.01)
        self.assertAlmostEqual(e.leq_dbfs, dsp.db_sum_energy([-30] * 5 + [-40] * 5), delta=0.01)

    def test_impact_vs_sustained_label(self):
        lg, evs = logger()
        feed_levels(lg, [Q] * 5 + [-20] * 1 + [-45] * 0 + [-38] * 4 + [Q] * 40 + [-30] * 100 + [Q] * 30)
        self.assertEqual([e.kind for e in evs], ["impact", "sustained"])

    def test_minute_aggregation(self):
        rows = []
        lg = NoiseLogger(-40.0, on_minute=lambda m, sp, n, mx: rows.append((m, n, mx)), abs_start=30.0)
        for i in range(1500):  # 150 s, 시작이 분의 30초 지점
            lg.push(i * 0.1, -60.0 if i != 700 else -35.0)
        lg.flush()
        self.assertEqual([r[0] for r in rows], [0, 1, 2])
        self.assertEqual(sum(r[1] for r in rows), 1500)
        self.assertEqual(rows[1][1], 600)
        self.assertAlmostEqual(max(r[2] for r in rows), -35.0)


class EngineLevelTest(unittest.TestCase):
    def cfg(self, **noise):
        c = config.load_config("/nonexistent")
        c["noise"].update(noise)
        c["detectors"]["knock"]["enabled"] = False
        c["detectors"]["beep"]["enabled"] = False
        return c

    def test_sine_levels_and_calibration_offset(self):
        # 1 kHz, 진폭 0.1 -> -23.01 dBFS(A) -> 표시 96.99 dB (공칭 120)
        x = synth.concat(synth.silence(2), synth.tone(1000, 2.0, 0.1, 0, 0), synth.silence(3))
        c = self.cfg(threshold_db=90.0)
        r = analyze_samples(x, c, [], start_epoch=0.0)
        self.assertAlmostEqual(r["summary"]["max_db"], 96.99, delta=0.15)
        self.assertEqual(len(r["noise_events"]), 1)
        self.assertAlmostEqual(r["noise_events"][0].duration, 2.0, delta=0.25)
        c2 = self.cfg(threshold_db=90.0)
        config.set_calibration(c2, 3.5)
        r2 = analyze_samples(x, c2, [], start_epoch=0.0)
        self.assertAlmostEqual(r2["summary"]["max_db"] - r["summary"]["max_db"], 3.5, delta=0.01)
        # 보정 후 같은 소리의 표시값만 달라지고 내부 dBFS 는 동일
        self.assertAlmostEqual(r2["summary"]["max_dbfs_a"], r["summary"]["max_dbfs_a"], delta=0.001)

    def test_low_frequency_rumble_reads_lower_than_midband_at_same_amplitude(self):
        c = self.cfg(threshold_db=200.0)
        lo = analyze_samples(synth.tone(63, 3.0, 0.1, 0, 0), c, [], start_epoch=0.0)["summary"]["max_db"]
        mid = analyze_samples(synth.tone(1000, 3.0, 0.1, 0, 0), c, [], start_epoch=0.0)["summary"]["max_db"]
        self.assertAlmostEqual(mid - lo, 26.2, delta=0.8)

    def test_event_detection_threshold_follows_calibration(self):
        x = synth.concat(synth.white(2, 0.002, 1), synth.white(2, 0.05, 2), synth.white(3, 0.002, 3))
        c = self.cfg(threshold_db=75.0)
        base = analyze_samples(x, c, [], start_epoch=0.0)
        self.assertEqual(len(base["noise_events"]), 1)
        # 보정을 -30 dB 로 하면 같은 소리가 기준 아래로 내려가 기록되지 않는다
        c2 = self.cfg(threshold_db=75.0)
        config.set_calibration(c2, -30.0)
        self.assertEqual(len(analyze_samples(x, c2, [], start_epoch=0.0)["noise_events"]), 0)

    def test_events_and_minutes_are_stored_with_epoch_times(self):
        st = Store(":memory:")
        self.addCleanup(st.close)
        c = self.cfg(threshold_db=60.0)
        x = synth.concat(synth.white(3, 0.0003, 1), synth.white(1.5, 0.05, 2), synth.white(3, 0.0003, 3))
        start = dt.datetime(2026, 11, 1, 23, 30, 0, tzinfo=dt.timezone(dt.timedelta(hours=9))).timestamp()
        analyze_samples(x, c, [], store=st, start_epoch=start)
        rows = st.events_between(start, start + 100)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["start"] - start, 3.0, delta=0.2)
        mins = st.minutes_between(start, start + 100)
        self.assertEqual(sum(m["n"] for m in mins), 75)


class ClipTest(unittest.TestCase):
    def clip_cfg(self, **kw):
        c = config.load_config("/nonexistent")
        c["noise"]["threshold_db"] = 70.0
        c["detectors"]["knock"]["enabled"] = False
        c["detectors"]["beep"]["enabled"] = False
        c["clips"].update(dict(enabled=True, lowpass_hz=0, skip_speech=False), **kw)
        return c

    def sig(self):
        # 8 s 조용 + 1 s 저음 쿵 + 8 s 조용
        thump = synth.tone(80, 1.0, 0.3, 0.01, 0.05)
        return synth.concat(synth.white(8, 0.0005, 1), thump, synth.white(8, 0.0005, 2))

    def test_clips_off_by_default_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            c = config.load_config("/nonexistent")
            c["noise"]["threshold_db"] = 70.0
            self.assertFalse(c["clips"]["enabled"])
            st = Store(":memory:")
            self.addCleanup(st.close)
            eng = Engine(c, store=st, clips_dir=os.path.join(td, "clips"), start_epoch=1_700_000_000.0)
            x = self.sig()
            for i in range(0, len(x), 16000):
                eng.feed(x[i:i + 16000])
            eng.flush()
            self.assertEqual(len(st.events_between(0, 2e9)), 1)
            self.assertFalse(os.path.exists(os.path.join(td, "clips")))
            self.assertIsNone(st.events_between(0, 2e9)[0]["clip"])

    def test_clip_saved_with_margins_and_linked_to_event(self):
        with tempfile.TemporaryDirectory() as td:
            c = self.clip_cfg()
            st = Store(":memory:")
            self.addCleanup(st.close)
            eng = Engine(c, store=st, clips_dir=os.path.join(td, "clips"), start_epoch=1_700_000_000.0)
            x = self.sig()
            for i in range(0, len(x), 16000):
                eng.feed(x[i:i + 16000])
            eng.flush()
            ev = st.events_between(0, 2e9)[0]
            self.assertTrue(ev["clip"])
            path = os.path.join(td, "clips", ev["clip"])
            y, sr = audio.read_wav(path)
            self.assertEqual(sr, 16000)
            secs = len(y) / 16000.0
            self.assertAlmostEqual(secs, 3 + ev["duration"] + 3, delta=0.8)
            # 클립 가운데에 큰 소리가 있다
            mid = dsp.rms(y[int(2.8 * 16000):int(3.8 * 16000)])
            edge = dsp.rms(y[:int(2 * 16000)])
            self.assertGreater(mid, 20 * edge)

    def test_privacy_lowpass_removes_high_frequencies(self):
        with tempfile.TemporaryDirectory() as td:
            c = self.clip_cfg(lowpass_hz=500)
            st = Store(":memory:")
            self.addCleanup(st.close)
            eng = Engine(c, store=st, clips_dir=os.path.join(td, "clips"), start_epoch=1_700_000_000.0)
            hi = synth.mix(synth.tone(80, 1.0, 0.3, 0.01, 0.05), synth.tone(3000, 1.0, 0.3, 0.01, 0.05))
            x = synth.concat(synth.white(8, 0.0005, 1), hi, synth.white(8, 0.0005, 2))
            for i in range(0, len(x), 16000):
                eng.feed(x[i:i + 16000])
            eng.flush()
            ev = st.events_between(0, 2e9)[0]
            y, _ = audio.read_wav(os.path.join(td, "clips", ev["clip"]))
            seg = y[int(3.3 * 16000):int(3.8 * 16000)]
            n = 4096
            pw = dsp.rfft_power([v * w for v, w in zip(seg[:n], dsp.hann(n))])
            lo = max(pw[int(60 * n / 16000):int(120 * n / 16000)])
            hi_p = max(pw[int(2800 * n / 16000):int(3200 * n / 16000)])
            self.assertGreater(lo / max(hi_p, 1e-30), 1e4)  # 40 dB 이상 차이

    def test_speech_like_clip_is_skipped(self):
        self.assertGreaterEqual(speech_likelihood(synth.speechlike(4)), 0.25)
        for x in (synth.tone(80, 2.0, 0.3), synth.white(3, 0.1), synth.knocks(4), synth.tone(120, 3.0, 0.3), synth.rice_cooker()):
            self.assertLess(speech_likelihood(x), 0.25)
        with tempfile.TemporaryDirectory() as td:
            c = self.clip_cfg(skip_speech=True)
            st = Store(":memory:")
            self.addCleanup(st.close)
            eng = Engine(c, store=st, clips_dir=os.path.join(td, "clips"), start_epoch=1_700_000_000.0)
            x = synth.concat(synth.white(8, 0.0005, 1), synth.speechlike(4), synth.white(8, 0.0005, 2))
            for i in range(0, len(x), 16000):
                eng.feed(x[i:i + 16000])
            eng.flush()
            evs = st.events_between(0, 2e9)
            self.assertGreaterEqual(len(evs), 1)
            self.assertTrue(all(e["clip"] is None for e in evs))
            self.assertFalse(os.path.exists(os.path.join(td, "clips")) and os.listdir(os.path.join(td, "clips")))

    def test_retention_purges_old_clips_only(self):
        with tempfile.TemporaryDirectory() as td:
            old, new, other = (os.path.join(td, n) for n in ("old.wav", "new.wav", "note.txt"))
            for p in (old, new, other):
                with open(p, "wb") as f:
                    f.write(b"x")
            now = 1_800_000_000.0
            os.utime(old, (now - 20 * 86400, now - 20 * 86400))
            os.utime(new, (now - 2 * 86400, now - 2 * 86400))
            os.utime(other, (now - 90 * 86400, now - 90 * 86400))
            removed = purge_old_clips(td, 14, now=now)
            self.assertEqual(removed, ["old.wav"])
            self.assertTrue(os.path.exists(new) and os.path.exists(other))

    def test_ring_buffer_bounds(self):
        r = Ring(1.0)
        r.push([0.5] * 16000)
        r.push([0.25] * 16000)
        self.assertEqual(r.end, 32000)
        self.assertEqual(len(r.get(0, 32000)), 16000)  # 오래된 건 밀려남
        self.assertAlmostEqual(r.get(31000, 32000)[0], 0.25, delta=1e-3)
        r.skip(8000)
        self.assertEqual(r.end, 40000)
        self.assertEqual(r.get(39000, 40000)[0], 0.0)


if __name__ == "__main__":
    unittest.main()
