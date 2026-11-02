"""엔진: 16 kHz 모노 샘플 스트림 -> 레벨/소음 이벤트, 템플릿 인식, 노크, 삐/멜로디."""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Sequence

from . import dsp
from .clips import ClipSaver
from .config import Calibration
from .events import Detection, NoiseEvent
from .features import FeatureStream
from .generic import ToneGroupDetector
from .knock import KnockDetector
from .matcher import TemplateMatcher
from .noise import NoiseLogger
from .store import Store
from .templates import Template

SR = 16000
MAX_DRIFT_S = 2.5   # 도착 시각이 스트림 시계보다 이만큼 앞서면 수신이 끊겼던 것으로 보고 공백 처리


class Engine:
    def __init__(self, cfg: Dict[str, Any], templates: Iterable[Template] = (), store: Optional[Store] = None,
                 notifier=None, clips_dir: Optional[str] = None, start_epoch: Optional[float] = None,
                 clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.store = store
        self.notifier = notifier
        self.clock = clock
        self.cal = Calibration.from_cfg(cfg)
        self.abs_start: Optional[float] = start_epoch
        self.pos = 0                      # 지금까지 들어온(공백 포함) 샘플 수
        self._t0 = 0.0                    # 현재 처리기들이 시작한 스트림 시각(초)
        self.meter = dsp.LevelMeter(SR)
        self._lvl_t = 0.0                 # 다음 100 ms 프레임의 스트림 시각
        self.feat = FeatureStream(SR)
        nz = cfg["noise"]
        self.noise = None  # type: Optional[NoiseLogger]
        self._nz_cfg = nz
        self.knock = None  # type: Optional[KnockDetector]
        self.tone = None   # type: Optional[ToneGroupDetector]
        self.matchers: List[TemplateMatcher] = []
        self.detections: Deque[Detection] = deque(maxlen=200)
        self.noise_events: List[NoiseEvent] = []
        self._tmpl_hits: Deque = deque(maxlen=50)
        self.clip_saver = None  # type: Optional[ClipSaver]
        cc = cfg.get("clips", {})
        if cc.get("enabled") and clips_dir:
            self.clip_saver = ClipSaver(clips_dir, cc, SR, clock)
        self.stats = {"frames": 0, "sum_power_a": 0.0, "max_a": dsp.DB_FLOOR, "sum_power_z": 0.0}
        self.last_level_dbfs_a = dsp.DB_FLOOR
        self.set_templates(templates)
        self._build_detectors()

    # ------------------------------------------------------------------ 구성
    def _build_detectors(self):
        cfg = self.cfg
        if self._nz_cfg.get("enabled", True):
            self.noise = NoiseLogger(
                self.cal.from_db(self._nz_cfg["threshold_db"]), self._nz_cfg["hysteresis_db"],
                self._nz_cfg["min_duration_ms"] / 1000.0, self._nz_cfg["merge_gap_ms"] / 1000.0,
                on_event=self._on_noise_event, on_minute=self._on_minute, abs_start=self.abs_start or 0.0)
        kc = cfg["detectors"]["knock"]
        if kc.get("enabled"):
            self.knock = KnockDetector(SR, min_hits=int(kc.get("min_hits", 3)), cooldown=float(kc.get("cooldown_s", 10)))
        bc = cfg["detectors"]["beep"]
        if bc.get("enabled"):
            self.tone = ToneGroupDetector(cooldown=float(bc.get("cooldown_s", 60)))

    def set_templates(self, templates: Iterable[Template]):
        self.matchers = [TemplateMatcher(t) for t in templates if t.enabled and t.samples]

    def epoch(self, stream_t: float) -> float:
        return (self.abs_start or 0.0) + stream_t

    # ------------------------------------------------------------------ 입력
    def feed(self, samples: Sequence[float], wall: Optional[float] = None) -> List[Detection]:
        """samples: 16 kHz 모노 float. wall: 이 청크가 도착한 벽시계 시각(없으면 clock())."""
        if not len(samples):
            return []
        n = len(samples)
        if self.abs_start is None:
            self.abs_start = (wall if wall is not None else self.clock()) - n / float(SR)
            if self.noise:
                self.noise.abs_start = self.abs_start
        elif wall is not None:
            drift = wall - (self.abs_start + (self.pos + n) / float(SR))
            if drift > MAX_DRIFT_S:
                self.skip(drift)
        out: List[Detection] = []
        self.pos += n
        if self.clip_saver:
            self.clip_saver.push(samples)
        for a_db, z_db in self.meter.feed(samples):
            t = self._lvl_t
            self._lvl_t += 0.1
            self._on_level(t, a_db, z_db)
        for f in self.feat.feed(samples):
            out.extend(self._on_frame(f))
        if self.knock:
            for d in self.knock.feed(samples):
                out.append(self._shift(d))
        if self.clip_saver:
            self.clip_saver.service()
        for d in out:
            self._emit(d)
        return out

    def _shift(self, d: Detection) -> Detection:
        d.t_start += self._t0
        d.t_end += self._t0
        return d

    def _on_level(self, t: float, a_db: float, z_db: float):
        st = self.stats
        st["frames"] += 1
        st["sum_power_a"] += 10 ** (a_db / 10.0)
        st["sum_power_z"] += 10 ** (z_db / 10.0)
        if a_db > st["max_a"]:
            st["max_a"] = a_db
        self.last_level_dbfs_a = a_db
        if self.noise:
            self.noise.push(t, a_db)

    def _on_frame(self, f) -> List[Detection]:
        f.t += self._t0
        out = []
        if self.cfg.get("recognition", {}).get("enabled", True):
            for m in self.matchers:
                d = m.update(f)
                if d is not None:
                    self._tmpl_hits.append((d.t_start, d.t_end))
                    out.append(d)
        if self.tone:
            for d in self.tone.feed(f):
                if self._covered_by_template(d):
                    continue
                out.append(d)
        return out

    def _covered_by_template(self, d: Detection) -> bool:
        for (a, b) in self._tmpl_hits:
            if a - 1.5 <= d.t_end and b + 1.5 >= d.t_start:
                return True
        return False

    # ------------------------------------------------------------------ 공백/종료
    def skip(self, seconds: float):
        """수신이 끊긴 구간: 진행 중 이벤트를 마감하고 처리기 상태를 초기화한 뒤 시간만 흘려보낸다."""
        self._finish_processors()
        n = int(seconds * SR)
        self.pos += n
        if self.clip_saver:
            self.clip_saver.ring.skip(n)
        self._t0 = self.pos / float(SR)
        self._lvl_t = self._t0
        self.meter.reset()
        self.feat.reset()
        for m in self.matchers:
            m.reset()
        if self.knock:
            self.knock.reset()
        if self.tone:
            self.tone.reset()

    def _finish_processors(self):
        out: List[Detection] = []
        for f in self.feat.flush():
            out.extend(self._on_frame(f))
        for m in self.matchers:
            d = m.flush()
            if d is not None:
                self._tmpl_hits.append((d.t_start, d.t_end))
                out.append(d)
        if self.tone:
            for d in self.tone.flush():
                if not self._covered_by_template(d):
                    out.append(d)
        if self.knock:
            for d in self.knock.flush():
                out.append(self._shift(d))
        if self.noise:
            self.noise.flush()
        for d in out:
            self._emit(d)
        return out

    def flush(self) -> List[Detection]:
        out = self._finish_processors()
        if self.clip_saver:
            self.clip_saver.flush()
        if self.store:
            self.store.commit()
        return out

    # ------------------------------------------------------------------ 출력
    def _emit(self, d: Detection):
        d.t_epoch = self.epoch(d.t_start)
        kind_cfg = self.cfg["detectors"].get(d.kind if d.kind != "melody" else "beep", {})
        notify = d.notify if d.kind == "template" else bool(kind_cfg.get("notify", d.notify))
        self.detections.append(d)
        if self.store:
            self.store.add_detection(d.t_epoch, d.kind, d.name, d.score, d.message)
        if notify and self.notifier:
            self.notifier.notify(d.name, d.message, 3, ["bell"])

    def _on_noise_event(self, ev: NoiseEvent):
        self.noise_events.append(ev)
        if not self.store:
            return
        ep = self.epoch(ev.start)
        eid = self.store.add_event(ep, self.epoch(ev.end), ev.peak_dbfs, ev.leq_dbfs, ev.kind, ev.n_segments)
        if self.clip_saver:
            self.clip_saver.request(int(ev.start * SR), int(ev.end * SR), ep,
                                    lambda path, eid=eid: self.store.set_clip(eid, path))

    def _on_minute(self, minute: int, sum_power: float, n: int, mx: float):
        if self.store:
            self.store.add_minute(minute, sum_power, n, mx)

    # ------------------------------------------------------------------ 상태
    def level_db(self) -> float:
        return self.cal.to_db(self.last_level_dbfs_a)

    def summary(self) -> Dict[str, Any]:
        st = self.stats
        fr = max(1, st["frames"])
        leq_a = dsp.power_to_db(st["sum_power_a"] / fr)
        return {
            "duration_s": round(st["frames"] * 0.1, 1),
            "leq_dbfs_a": round(leq_a, 2), "max_dbfs_a": round(st["max_a"], 2),
            "leq_db": round(self.cal.to_db(leq_a), 1), "max_db": round(self.cal.to_db(st["max_a"]), 1),
            "unweighted_leq_dbfs": round(dsp.power_to_db(st["sum_power_z"] / fr), 2),
        }


def analyze_samples(x: Sequence[float], cfg: Dict[str, Any], templates: Iterable[Template] = (),
                    store: Optional[Store] = None, start_epoch: float = 0.0, chunk_s: float = 1.0,
                    clips_dir: Optional[str] = None) -> Dict[str, Any]:
    """파일/버퍼 전체를 스트리밍 경로로 분석. 반환: 요약 + 소음 이벤트 + 인식 목록."""
    eng = Engine(cfg, templates, store=store, clips_dir=clips_dir, start_epoch=start_epoch)
    size = int(chunk_s * SR)
    dets: List[Detection] = []
    for i in range(0, len(x), size):
        dets.extend(eng.feed(x[i:i + size]))
    dets.extend(eng.flush())
    return {"summary": eng.summary(), "noise_events": eng.noise_events, "detections": dets, "engine": eng}
