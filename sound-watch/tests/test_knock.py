import unittest

import helpers  # noqa: F401
import synth
from soundwatch.knock import KnockDetector


def run(sig, chunk=16000, **kw):
    k = KnockDetector(**kw)
    out = []
    for i in range(0, len(sig), chunk):
        out += k.feed(sig[i:i + chunk])
    out += k.flush()
    return out


def bg(kind, sec, rms=0.01):
    return synth.white(sec, rms, 9) if kind == "white" else synth.pink(sec, rms, 9)


class KnockTest(unittest.TestCase):
    def test_three_knocks_detected_with_count_and_time(self):
        for chunk in (16000, 1000, 333):
            out = run(synth.pad(synth.knocks(3), 2, 2), chunk)
            self.assertEqual(len(out), 1, chunk)
            self.assertEqual(out[0].info["hits"], 3)
            self.assertAlmostEqual(out[0].t_start, 2.0, delta=0.15)
            self.assertAlmostEqual(out[0].info["interval"], 0.3, delta=0.05)

    def test_knocks_over_noise_background(self):
        for kind in ("white", "pink"):
            sig = synth.mix(bg(kind, 6), synth.knocks(4, 0.35, 0.5), offset=2 * 16000)
            out = run(sig)
            self.assertEqual([d.info["hits"] for d in out], [4], kind)

    def test_min_hits_respected(self):
        self.assertEqual(run(synth.pad(synth.knocks(2), 2, 2)), [])
        self.assertEqual(len(run(synth.pad(synth.knocks(2), 2, 2), min_hits=2)), 1)
        self.assertEqual(run(synth.pad(synth.knocks(1), 2, 2), min_hits=2), [])

    def test_irregular_rhythm_rejected(self):
        sig = synth.pad(synth.concat(synth.knock(), synth.silence(0.15), synth.knock(), synth.silence(0.85), synth.knock()), 2, 2)
        self.assertEqual(run(sig), [])

    def test_sounds_that_are_not_knocks(self):
        short_beeps = synth.concat(*[synth.concat(synth.tone(2000, 0.08, 0.5), synth.silence(0.22)) for _ in range(4)])
        cases = {
            "rice_cooker": synth.rice_cooker(),
            "short_beeps": short_beeps,
            "doorbell": synth.doorbell(),
            "speech": synth.speechlike(5),
            "kettle": synth.kettle_whistle(),
        }
        for name, s in cases.items():
            self.assertEqual(run(synth.pad(s, 2, 2)), [], name)
        self.assertEqual(run(bg("white", 10)), [])
        self.assertEqual(run(bg("pink", 10)), [])

    def test_cooldown_between_groups(self):
        a = synth.pad(synth.knocks(3), 2, 2.0)
        sig = synth.concat(a, synth.knocks(3), synth.silence(2))
        self.assertEqual(len(run(sig, cooldown=10.0)), 1)
        sig2 = synth.concat(a, synth.silence(12), synth.knocks(3), synth.silence(2))
        self.assertEqual(len(run(sig2, cooldown=10.0)), 2)


if __name__ == "__main__":
    unittest.main()
