import json
from pathlib import Path


def write_jsonl(path: Path, recs: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")


class B:
    """합성 세션 빌더."""

    def __init__(self, sid="s1"):
        self.sid, self.recs, self.n, self.t = sid, [], 0, 0

    def _ts(self):
        self.t += 1
        return f"2026-01-01T00:{self.t // 60:02d}:{self.t % 60:02d}.000Z"

    def user(self, text, sidechain=False):
        self.n += 1
        self.recs.append({"type": "user", "isSidechain": sidechain, "sessionId": self.sid, "uuid": f"u{self.n}", "timestamp": self._ts(),
                          "message": {"role": "user", "content": text}})

    def call(self, name, inp, result="ok", error=False, sidechain=False):
        self.n += 1
        tid = f"t{self.sid}{self.n}"
        self.recs.append({"type": "assistant", "isSidechain": sidechain, "sessionId": self.sid, "uuid": f"a{self.n}", "timestamp": self._ts(),
                          "message": {"id": f"m{self.sid}{self.n}", "role": "assistant", "model": "m",
                                      "usage": {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5},
                                      "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
        self.recs.append({"type": "user", "isSidechain": sidechain, "sessionId": self.sid, "uuid": f"r{self.n}", "timestamp": self._ts(),
                          "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": result, "is_error": error}]}})

    def save(self, path):
        write_jsonl(Path(path), self.recs)
