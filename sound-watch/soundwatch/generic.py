"""범용 삐/멜로디 감지기: 음정이 안정적인 순음 구간(음)을 모아 묶는다.

- 음: 음정 있는(tonal) 프레임이 연속(1프레임 끊김 허용)하고 인접 프레임 음정차 <= 0.7 반음, 길이 >= 100 ms
- 묶음: 음 사이 간격 <= 0.8 s, 마지막 음 뒤 1.0 s 침묵이면 마감
- 'beep'(같은 높이의 삐 소리) / 'melody'(서로 다른 높이 음이 2종 이상). 300 Hz 미만(냉장고 웅웅 등)과
  피크 집중도 0.5 미만(말소리·잡음 섞임)은 무시.
"""
from __future__ import annotations

from typing import List, Optional

from .events import Detection
from .features import Frame, HOP, SR, semitone_to_hz

FRAME_S = HOP / float(SR)
MIN_NOTE_FRAMES = 2
MAX_STEP_SEMITONE = 0.7
GROUP_GAP_S = 0.8
CLOSE_AFTER_S = 1.0
MAX_GROUP_S = 15.0
MIN_FREQ_HZ = 300.0
MIN_PEAK_FRAC = 0.5   # 말소리/잡음 섞인 소리를 거르기 위해 깨끗한 순음만


class ToneGroupDetector:
    def __init__(self, cooldown: float = 60.0, min_total_s: float = 0.15):
        self.cooldown = cooldown
        self.min_total_s = min_total_s
        self.reset()

    def reset(self):
        self._note = None      # {t0, t1, pitches[]}
        self._miss = 0
        self._notes: List[dict] = []
        self._last_t = -1e18
        self._last_emit = -1e18

    def feed(self, f: Frame) -> List[Detection]:
        out: List[Detection] = []
        ok = f.tonal and f.pitch is not None and f.freq >= MIN_FREQ_HZ and f.peak_frac >= MIN_PEAK_FRAC
        n = self._note
        if ok:
            if n is not None and abs(f.pitch - n["pitches"][-1]) <= MAX_STEP_SEMITONE:
                n["t1"] = f.t
                n["pitches"].append(f.pitch)
                self._miss = 0
            else:
                self._close_note()
                self._note = {"t0": f.t, "t1": f.t, "pitches": [f.pitch]}
                self._miss = 0
        else:
            if n is not None:
                self._miss += 1
                if self._miss > 1:
                    self._close_note()
        # 마감 조건
        if self._notes and not self._note:
            last_end = self._notes[-1]["t1"]
            if f.t - last_end >= CLOSE_AFTER_S:
                d = self._emit()
                if d is not None:
                    out.append(d)
        elif self._notes and self._note and self._note["t0"] - self._notes[0]["t0"] > MAX_GROUP_S:
            self._close_note()
            d = self._emit()
            if d is not None:
                out.append(d)
        return out

    def _close_note(self):
        n = self._note
        self._note = None
        self._miss = 0
        if n is None:
            return
        if len(n["pitches"]) < MIN_NOTE_FRAMES:
            return
        ps = sorted(n["pitches"])
        n["pitch"] = ps[len(ps) // 2]
        self._notes.append(n)

    def _emit(self) -> Optional[Detection]:
        notes = self._notes
        self._notes = []
        if not notes:
            return None
        # 간격이 벌어진 곳에서 첫 묶음만 사용 (남은 건 버림: 드문 경우)
        grp = [notes[0]]
        for n in notes[1:]:
            if n["t0"] - grp[-1]["t1"] <= GROUP_GAP_S:
                grp.append(n)
            else:
                break
        total = sum(n["t1"] - n["t0"] + FRAME_S for n in grp)
        if total < self.min_total_s:
            return None
        t0, t1 = grp[0]["t0"], grp[-1]["t1"]
        if t0 < self._last_emit + self.cooldown:
            return None
        pitches = [n["pitch"] for n in grp]
        distinct = []
        for p in pitches:
            if all(abs(p - q) > 1.0 for q in distinct):
                distinct.append(p)
        kind = "melody" if len(distinct) >= 2 else "beep"
        freqs = [int(round(semitone_to_hz(p))) for p in pitches]
        self._last_emit = t0
        name = "멜로디" if kind == "melody" else "삐 소리"
        return Detection(kind, name, t0, t1, 0.0,
                         "%s가 났어요 (%s Hz)" % (name, ", ".join(str(x) for x in freqs[:6])),
                         {"notes": len(grp), "freqs_hz": freqs[:16], "total_s": round(total, 2)},
                         notify=False)

    def flush(self) -> List[Detection]:
        self._close_note()
        d = self._emit()
        return [d] if d else []
