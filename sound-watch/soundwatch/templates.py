"""소리 템플릿: 등록(enroll) 시 특징열 추출, JSON 저장/로드."""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from . import audio, features
from .features import Frame, HOP

DEFAULT_THRESHOLD = 0.35     # 프레임 평균 비용(0 완벽 ~ 1 불일치) 이 이하면 일치
DEFAULT_COOLDOWN_S = 30.0
MAX_TEMPLATE_S = 20.0
MIN_ACTIVE_FRAMES = 3


class EnrollError(Exception):
    pass


@dataclass
class Sample:
    """한 번 녹음한 소리의 프레임 특징열 (활성 구간 + 앞뒤 1프레임 여백)."""
    act: List[int]
    strong: List[int]
    pitch: List[Optional[float]]
    vec: List[Optional[List[float]]]
    source: str = ""
    created: str = ""

    def __len__(self):
        return len(self.act)

    def n_active(self) -> int:
        return sum(self.act)

    def to_json(self):
        return {
            "act": self.act, "strong": self.strong,
            "pitch": [None if p is None else round(p, 3) for p in self.pitch],
            "vec": [None if v is None else [round(x, 4) for x in v] for v in self.vec],
            "source": self.source, "created": self.created,
        }

    @staticmethod
    def from_json(d) -> "Sample":
        return Sample(list(d["act"]), list(d["strong"]), list(d["pitch"]),
                      [None if v is None else list(v) for v in d["vec"]],
                      d.get("source", ""), d.get("created", ""))

    @staticmethod
    def from_frames(frames: Sequence[Frame], source: str = "") -> "Sample":
        return Sample(
            [1 if f.active else 0 for f in frames],
            [1 if (f.active and f.peak_excess >= features.STRONG_DB) else 0 for f in frames],
            [f.pitch for f in frames],
            [f.vec for f in frames],
            source, time.strftime("%Y-%m-%d %H:%M:%S"),
        )


@dataclass
class Template:
    name: str
    samples: List[Sample] = field(default_factory=list)
    say: str = ""
    cooldown: float = DEFAULT_COOLDOWN_S
    threshold: float = DEFAULT_THRESHOLD
    enabled: bool = True

    def message(self) -> str:
        return self.say or "%s 소리가 났어요" % self.name

    def to_json(self):
        return {"name": self.name, "say": self.say, "cooldown": self.cooldown,
                "threshold": self.threshold, "enabled": self.enabled,
                "samples": [s.to_json() for s in self.samples]}

    @staticmethod
    def from_json(d) -> "Template":
        return Template(d["name"], [Sample.from_json(s) for s in d.get("samples", [])],
                        d.get("say", ""), float(d.get("cooldown", DEFAULT_COOLDOWN_S)),
                        float(d.get("threshold", DEFAULT_THRESHOLD)), bool(d.get("enabled", True)))


def _groups(frames: Sequence[Frame], max_gap: int = 30):
    """활성 프레임을 침묵(max_gap 프레임=1.5s) 기준으로 묶는다 -> [(start, end)]."""
    groups = []
    start = last = None
    for i, f in enumerate(frames):
        if f.active:
            if start is None:
                start = last = i
            elif i - last > max_gap:
                groups.append((start, last))
                start = last = i
            else:
                last = i
    if start is not None:
        groups.append((start, last))
    return groups


def sample_from_samples(x: Sequence[float], source: str = "") -> (Sample, str):
    """16 kHz 모노 샘플 -> (Sample, 안내 메시지). 소리를 못 찾으면 EnrollError."""
    frames = features.extract_clip(x)
    if not frames:
        raise EnrollError("너무 짧은 녹음입니다 (최소 약 1초)")
    groups = _groups(frames)
    if not groups:
        raise EnrollError("배경보다 뚜렷한 소리를 찾지 못했습니다. 소리를 더 가까이/크게 녹음하고 앞뒤에 1초쯤 무음을 남겨 주세요")
    note = ""
    if len(groups) > 1:
        best = max(groups, key=lambda g: sum(f.peak_excess for f in frames[g[0]:g[1] + 1]))
        note = "소리 덩어리가 %d개 발견되어 가장 뚜렷한 것(%.1f~%.1f초)을 사용했습니다" % (
            len(groups), best[0] * HOP / 16000.0, (best[1] + 1) * HOP / 16000.0)
        g = best
    else:
        g = groups[0]
    a = max(0, g[0] - 1)
    b = min(len(frames) - 1, g[1] + 1)
    seg = frames[a:b + 1]
    s = Sample.from_frames(seg, source)
    if s.n_active() < MIN_ACTIVE_FRAMES:
        raise EnrollError("소리가 너무 짧습니다 (활성 프레임 %d개). 노크 같은 아주 짧은 소리는 노크 감지기를 쓰세요" % s.n_active())
    if len(seg) * HOP / 16000.0 > MAX_TEMPLATE_S:
        raise EnrollError("소리가 %d초를 넘습니다. 더 짧은 부분만 녹음해 주세요" % MAX_TEMPLATE_S)
    return s, note


class TemplateStore:
    """templates.json 읽기/쓰기."""

    def __init__(self, path: str):
        self.path = path
        self.templates: Dict[str, Template] = {}
        self.mtime = 0.0

    def load(self):
        self.templates = {}
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for d in data.get("templates", []):
                t = Template.from_json(d)
                self.templates[t.name] = t
            self.mtime = os.path.getmtime(self.path)
        return self

    def changed_on_disk(self) -> bool:
        try:
            return os.path.getmtime(self.path) != self.mtime
        except OSError:
            return False

    def save(self):
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "templates": [t.to_json() for t in self.templates.values()]},
                          f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        self.mtime = os.path.getmtime(self.path)

    def get(self, name: str) -> Optional[Template]:
        return self.templates.get(name)

    def remove(self, name: str) -> bool:
        if name in self.templates:
            del self.templates[name]
            self.save()
            return True
        return False

    def enroll(self, name: str, wav_paths: Sequence[str], say: str = "", cooldown: Optional[float] = None,
               threshold: Optional[float] = None, replace: bool = False) -> (Template, List[str]):
        """WAV 파일(들)로 등록. 반환: (템플릿, 안내 메시지 목록)."""
        name = name.strip()
        if not name:
            raise EnrollError("이름이 비었습니다")
        msgs: List[str] = []
        new_samples = []
        for p in wav_paths:
            x = audio.load_16k_mono(p)
            s, note = sample_from_samples(x, os.path.basename(p))
            if note:
                msgs.append("%s: %s" % (os.path.basename(p), note))
            new_samples.append(s)
        t = self.templates.get(name)
        if t is None or replace:
            t = Template(name)
        t.samples.extend(new_samples)
        if say:
            t.say = say
        if cooldown is not None:
            t.cooldown = float(cooldown)
        if threshold is not None:
            t.threshold = float(threshold)
        else:
            t.threshold = auto_threshold(t.samples)
        self.templates[name] = t
        self.save()
        return t, msgs


def auto_threshold(samples: Sequence[Sample]) -> float:
    """샘플이 2개 이상이면 샘플끼리의 DTW 거리로 임계값을 정한다 (0.30 ~ 0.50)."""
    if len(samples) < 2:
        return DEFAULT_THRESHOLD
    from .matcher import dtw_distance
    worst = 0.0
    for i, a in enumerate(samples):
        for j, b in enumerate(samples):
            if i != j:
                worst = max(worst, dtw_distance(a, b))
    return max(0.30, min(0.50, worst * 1.5 + 0.12))
