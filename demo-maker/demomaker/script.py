"""데모 스크립트(JSON) 읽기와 검증."""

import json
import os

STEP_TYPES = ("cmd", "pause", "comment", "setup")


class ScriptError(ValueError):
    pass


class Script:
    def __init__(self, data, base_dir="."):
        if not isinstance(data, dict):
            raise ScriptError("최상위는 JSON 객체여야 해요.")
        self.base_dir = os.path.abspath(base_dir)
        self.title = str(data.get("title") or "demo")
        self.cwd = data.get("cwd")
        self.project = data.get("project")
        self.env = data.get("env") or {}
        self.width = _num(data, "width", 80, 20, 300, int)
        self.height = _num(data, "height", 24, 3, 200, int)       # 화면에 보이는 최대 줄 수
        self.line_delay = _num(data, "line_delay", 0.05, 0, 5, float)
        self.timeout = _num(data, "timeout", 30, 1, 600, float)   # 명령 하나당 제한(초)
        if not isinstance(self.env, dict) or not all(isinstance(k, str) for k in self.env):
            raise ScriptError("env 는 {이름: 값} 객체여야 해요.")
        self.env = {k: str(v) for k, v in self.env.items()}
        steps = data.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ScriptError("steps 가 비어 있어요.")
        self.steps = [self._check_step(i, s) for i, s in enumerate(steps, 1)]

    @staticmethod
    def _check_step(i, s):
        if not isinstance(s, dict):
            raise ScriptError("step %d: 객체가 아니에요." % i)
        t = s.get("type")
        if t not in STEP_TYPES:
            raise ScriptError("step %d: type 은 %s 중 하나여야 해요 (받은 값: %r)." % (i, "/".join(STEP_TYPES), t))
        s = dict(s)
        if t == "cmd":
            if not isinstance(s.get("run"), str) or not s["run"].strip():
                raise ScriptError("step %d: cmd 에는 run 문자열이 필요해요." % i)
            _num(s, "typing_speed", 0.05, 0, 2, float, "step %d" % i)
            _num(s, "timeout", 1, 0.1, 600, float, "step %d" % i)
        elif t == "comment":
            if not isinstance(s.get("text"), str):
                raise ScriptError("step %d: comment 에는 text 가 필요해요." % i)
            _num(s, "typing_speed", 0.03, 0, 2, float, "step %d" % i)
        elif t == "pause":
            _num({"seconds": s.get("seconds", 1)}, "seconds", 1, 0, 60, float, "step %d" % i)
        elif t == "setup":
            files = s.get("files", {})
            if not isinstance(files, dict):
                raise ScriptError("step %d: files 는 {경로: 내용} 객체여야 해요." % i)
            for rel in files:
                norm = os.path.normpath(rel)
                if os.path.isabs(rel) or rel.startswith(("/", "\\")) or norm.startswith("..") or ":" in rel:
                    raise ScriptError("step %d: setup 파일 경로는 샌드박스 안의 상대 경로여야 해요: %s" % (i, rel))
        return s

    @classmethod
    def load(cls, path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise ScriptError("%s 를 읽을 수 없어요: %s" % (path, e))
        return cls(data, os.path.dirname(os.path.abspath(path)))


def _num(d, key, default, lo, hi, typ, where="script"):
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ScriptError("%s: %s 는 숫자여야 해요 (받은 값: %r)." % (where, key, v))
    if not lo <= v <= hi:
        raise ScriptError("%s: %s 는 %s~%s 사이여야 해요 (받은 값: %r)." % (where, key, lo, hi, v))
    return typ(v)
