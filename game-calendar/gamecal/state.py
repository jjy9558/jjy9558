import json
import os


def load_state(path):
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                s = json.load(f)
            s.setdefault("seen", {})
            s.setdefault("entries", {})
            s.setdefault("alerts_sent", {})
            return s
        except (OSError, ValueError):
            pass
    return {"seen": {}, "entries": {}, "alerts_sent": {}}


def save_state(path, state):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
