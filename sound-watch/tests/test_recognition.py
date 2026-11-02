import os
import tempfile
import unittest

import helpers  # noqa: F401
import synth
from soundwatch import audio, config, dsp, features as F
from soundwatch.engine import Engine, analyze_samples
from soundwatch.generic import ToneGroupDetector
from soundwatch.templates import EnrollError, Template, TemplateStore, sample_from_samples, auto_threshold

SR = 16000


def make_template(name, sig, **kw):
    s, _ = sample_from_samples(synth.pad(sig, 1.5, 1.5))
    return Template(name, [s], **kw)


def cfg_quiet():
    c = config.load_config("/nonexistent")
    c["noise"]["enabled"] = False                 # 인식만 본다
    c["detectors"]["knock"]["enabled"] = False
    c["detectors"]["beep"]["enabled"] = False
    return c


def detect(sig, templates, cfg=None, chunk_s=1.0):
    r = analyze_samples(sig, cfg or cfg_quiet(), templates, start_epoch=0.0, chunk_s=chunk_s)
    return [d for d in r["detections"] if d.kind == "template"]


class FeatureTest(unittest.TestCase):
    def test_dominant_frequency_and_tonality(self):
        fr = F.extract_clip(synth.pad(synth.tone(1234.0, 1.0, 0.3), 1.0, 1.0))
        tonal = [f for f in fr if f.tonal]
        self.assertGreater(len(tonal), 10)
        for f in tonal[2:-2]:
            self.assertAlmostEqual(f.freq, 1234.0, delta=4.0)
            self.assertLess(f.flatness, 0.05)

    def test_flatness_noise_vs_tone(self):
        ext = F.SliceExtractor()
        noise = ext.feed(synth.white(1.0, 0.1))
        ext2 = F.SliceExtractor()
        tone = ext2.feed(synth.tone(800, 1.0, 0.3, 0, 0))
        self.assertGreater(sum(s.flatness for s in noise) / len(noise), 0.4)
        self.assertLess(sum(s.flatness for s in tone) / len(tone), 0.01)

    def test_band_energy_peaks_in_the_right_band(self):
        ext = F.SliceExtractor()
        sl = ext.feed(synth.tone(3000, 0.5, 0.3, 0, 0))[3]
        fb = F.filterbank()
        centers = []
        for items, w in fb:
            centers.append(sum(k * wt for k, wt in items) / w * SR / F.WIN)
        best = max(range(len(sl.band_db)), key=sl.band_db.__getitem__)
        self.assertLess(abs(centers[best] - 3000), 300)

    def test_noise_only_has_no_active_frames(self):
        for nz in (synth.white(20, 0.02), synth.pink(20, 0.02)):
            fs = F.FeatureStream()
            fr = fs.feed(nz) + fs.flush()
            self.assertGreater(len(fr), 300)
            self.assertEqual(sum(f.active for f in fr), 0)


class RecognitionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doorbell = make_template("초인종", synth.doorbell())
        cls.washer = make_template("세탁기", synth.washer_melody())
        cls.cooker = make_template("밥솥", synth.rice_cooker())
        cls.micro = make_template("전자레인지", synth.microwave_ding())
        cls.kettle = make_template("주전자", synth.kettle_whistle())
        cls.all = [cls.doorbell, cls.washer, cls.cooker, cls.micro, cls.kettle]
        cls.sounds = {"초인종": synth.doorbell(), "세탁기": synth.washer_melody(), "밥솥": synth.rice_cooker(),
                      "전자레인지": synth.microwave_ding(), "주전자": synth.kettle_whistle()}

    def test_true_positives_under_noise(self):
        # 각 소리가 백색/핑크 잡음 SNR 10, 0 dB 에서도 자기 이름으로만 한 번 감지된다
        for kind in ("white", "pink"):
            for snr in (10, 0):
                for name, sig in self.sounds.items():
                    x = synth.add_noise(synth.pad(sig, 3, 3), kind, snr, seed=4)
                    got = [d.name for d in detect(x, self.all)]
                    self.assertEqual(got, [name], "%s snr=%s %s" % (kind, snr, name))

    def test_detection_time_is_near_the_sound(self):
        x = synth.add_noise(synth.pad(synth.doorbell(), 3, 3), "white", 10)
        d = detect(x, self.all)[0]
        self.assertAlmostEqual(d.t_start, 3.0, delta=0.4)
        self.assertLess(d.score, 0.35)

    def test_volume_independent(self):
        quiet = [v * 0.05 for v in synth.pad(synth.doorbell(), 3, 3)]
        x = synth.add_noise(quiet, "pink", 10)
        self.assertEqual([d.name for d in detect(x, self.all)], ["초인종"])

    def test_chunk_size_does_not_matter(self):
        x = synth.add_noise(synth.pad(synth.washer_melody(), 3, 3), "white", 10)
        a = [d.name for d in detect(x, self.all, chunk_s=1.0)]
        b = [d.name for d in detect(x, self.all, chunk_s=0.23)]
        self.assertEqual(a, ["세탁기"])
        self.assertEqual(a, b)

    def test_false_positive_resistance(self):
        neg = {
            "silence": synth.silence(12),
            "white": synth.white(25, 0.03, 9),
            "pink": synth.pink(25, 0.03, 9),
            "single_tone_660": synth.pad(synth.tone(660, 1.2), 3, 3),
            "single_tone_long": synth.pad(synth.tone(660, 2.5), 3, 3),
            "reversed_doorbell": synth.pad(synth.doorbell(550, 660), 3, 3),
            "knocks": synth.pad(synth.knocks(4), 3, 3),
            "speech": synth.pad(synth.speechlike(5), 3, 3),
            "other_melody": synth.pad(synth.concat(*[synth.tone(f, 0.22) for f in (440, 392, 349, 294)]), 3, 3),
            "different_pitch_2tone": synth.pad(synth.doorbell(880, 700), 3, 3),
        }
        for name, x in neg.items():
            got = detect(x, self.all)
            self.assertEqual(got, [], "%s -> %s" % (name, [d.name for d in got]))
        # 잡음이 깔려도 마찬가지
        noisy = synth.add_noise(synth.pad(synth.tone(660, 1.2), 3, 3), "white", 5)
        self.assertEqual(detect(noisy, self.all), [])

    def test_cooldown_suppresses_repeats(self):
        t = make_template("초인종", synth.doorbell(), cooldown=10.0)
        one = synth.pad(synth.doorbell(), 3, 0)
        x = synth.concat(one, synth.silence(2.0), synth.doorbell(), synth.silence(3))      # 3 s 간격: 쿨다운 안
        self.assertEqual(len(detect(x, [t])), 1)
        y = synth.concat(one, synth.silence(14.0), synth.doorbell(), synth.silence(3))     # 쿨다운 밖
        self.assertEqual(len(detect(y, [t])), 2)

    def test_pure_python_path_end_to_end(self):
        dsp.USE_NUMPY = False
        try:
            x = synth.add_noise(synth.pad(synth.doorbell(), 3, 2), "pink", 10)
            self.assertEqual([d.name for d in detect(x, [self.doorbell, self.washer])], ["초인종"])
        finally:
            dsp.USE_NUMPY = dsp.HAVE_NUMPY

    def test_stream_gap_resets_without_false_detection(self):
        c = cfg_quiet()
        eng = Engine(c, [self.doorbell], start_epoch=1000.0)
        x = synth.pad(synth.doorbell(), 3, 3)
        got = []
        for i in range(0, SR * 3, SR):
            got += eng.feed(x[i:i + SR])
        eng.skip(30.0)  # 수신 끊김
        for i in range(SR * 3, len(x), SR):
            got += eng.feed(x[i:i + SR])
        got += eng.flush()
        self.assertEqual([d.kind for d in got if d.kind == "template"], ["template"])
        self.assertGreater(eng.epoch(0) + eng.pos / SR, 1030)

    def test_multiple_samples_lower_threshold_and_still_detect(self):
        a, _ = sample_from_samples(synth.add_noise(synth.pad(synth.doorbell(), 1.5, 1.5), "white", 15, seed=1))
        b, _ = sample_from_samples(synth.add_noise(synth.pad(synth.doorbell(0.0 + 662.0, 548.0, 0.46, 0.72), 1.5, 1.5), "pink", 12, seed=2))
        thr = auto_threshold([a, b])
        self.assertTrue(0.30 <= thr <= 0.50)
        t = Template("초인종", [a, b], threshold=thr)
        x = synth.add_noise(synth.pad(synth.doorbell(), 3, 3), "white", 5)
        self.assertEqual([d.name for d in detect(x, [t])], ["초인종"])


class EnrollTest(unittest.TestCase):
    def test_enroll_store_roundtrip_and_errors(self):
        with tempfile.TemporaryDirectory() as td:
            w = os.path.join(td, "bell.wav")
            audio.write_wav(w, synth.add_noise(synth.pad(synth.doorbell(), 1.5, 1.5), "white", 15))
            ts = TemplateStore(os.path.join(td, "templates.json"))
            t, msgs = ts.enroll("초인종", [w], say="초인종이 울렸어요", cooldown=7)
            self.assertEqual(t.cooldown, 7.0)
            ts2 = TemplateStore(os.path.join(td, "templates.json")).load()
            self.assertEqual(list(ts2.templates), ["초인종"])
            self.assertEqual(ts2.templates["초인종"].message(), "초인종이 울렸어요")
            self.assertEqual(len(ts2.templates["초인종"].samples[0]), len(t.samples[0]))
            x = synth.add_noise(synth.pad(synth.doorbell(), 3, 3), "pink", 10)
            self.assertEqual([d.name for d in detect(x, list(ts2.templates.values()))], ["초인종"])
            self.assertTrue(ts2.remove("초인종"))
            self.assertEqual(TemplateStore(ts.path).load().templates, {})

            silent = os.path.join(td, "silent.wav")
            audio.write_wav(silent, synth.silence(3))
            with self.assertRaises(EnrollError):
                ts.enroll("무음", [silent])
            short = os.path.join(td, "short.wav")
            audio.write_wav(short, synth.white(0.3, 0.1))
            with self.assertRaises(EnrollError):
                ts.enroll("짧음", [short])

    def test_two_sound_groups_pick_the_louder(self):
        x = synth.concat(synth.silence(1.5), synth.tone(1000, 0.4, 0.05), synth.silence(3.0),
                         synth.tone(2000, 0.6, 0.4), synth.silence(1.5))
        s, note = sample_from_samples(x)
        self.assertIn("덩어리", note)
        pitches = [p for p in s.pitch if p is not None]
        self.assertAlmostEqual(F.semitone_to_hz(sum(pitches) / len(pitches)), 2000, delta=40)


class GenericDetectorTest(unittest.TestCase):
    def run_tone(self, sig, cooldown=5.0):
        fs = F.FeatureStream()
        fr = fs.feed(sig) + fs.flush()
        d = ToneGroupDetector(cooldown=cooldown)
        out = []
        for f in fr:
            out += d.feed(f)
        out += d.flush()
        return out

    def test_beep_and_melody(self):
        beep = self.run_tone(synth.pad(synth.rice_cooker(), 2, 3))
        self.assertEqual([(d.kind, d.info["notes"]) for d in beep], [("beep", 3)])
        mel = self.run_tone(synth.pad(synth.washer_melody(), 2, 3))
        self.assertEqual([d.kind for d in mel], ["melody"])
        self.assertEqual(mel[0].info["notes"], 4)

    def test_no_events_for_noise_speech_or_knocks(self):
        for x in (synth.white(10, 0.02), synth.pad(synth.speechlike(5), 2, 3), synth.pad(synth.knocks(4), 2, 3)):
            self.assertEqual(self.run_tone(x), [])

    def test_generic_beep_is_suppressed_when_a_template_matches(self):
        c = cfg_quiet()
        c["detectors"]["beep"]["enabled"] = True
        t = make_template("밥솥", synth.rice_cooker())
        x = synth.add_noise(synth.pad(synth.rice_cooker(), 3, 3), "white", 15)
        r = analyze_samples(x, c, [t], start_epoch=0.0)
        self.assertEqual([d.kind for d in r["detections"]], ["template"])
        r2 = analyze_samples(x, c, [], start_epoch=0.0)
        self.assertEqual([d.kind for d in r2["detections"]], ["beep"])
        self.assertFalse(r2["detections"][0].notify)

    def test_beep_cooldown(self):
        sig = synth.concat(synth.pad(synth.microwave_ding(), 2, 3), synth.microwave_ding(), synth.silence(3))
        self.assertEqual(len(self.run_tone(sig, cooldown=60.0)), 1)
        sig2 = synth.concat(synth.pad(synth.microwave_ding(), 2, 3), synth.silence(8), synth.microwave_ding(), synth.silence(3))
        self.assertEqual(len(self.run_tone(sig2, cooldown=5.0)), 2)


if __name__ == "__main__":
    unittest.main()
