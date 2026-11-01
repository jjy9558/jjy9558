"""PC -> 폰 '이거 말해' 이벤트 큐 (롱폴링)."""
import threading
import time


class EventQueue:
    def __init__(self, keep=100):
        self._items = []
        self._next = 1
        self._keep = keep
        self._cond = threading.Condition()

    def push(self, kind, text, **extra):
        with self._cond:
            ev = dict(extra, id=self._next, kind=kind, text=text, ts=time.time())
            self._next += 1
            self._items.append(ev)
            del self._items[:-self._keep]
            self._cond.notify_all()
            return ev

    def say(self, text, **extra):
        return self.push("say", text, **extra) if text else None

    def since(self, last_id, wait=0.0):
        deadline = time.monotonic() + max(0.0, wait)
        with self._cond:
            while True:
                items = [e for e in self._items if e["id"] > last_id]
                left = deadline - time.monotonic()
                if items or left <= 0:
                    return items
                self._cond.wait(left)

    @property
    def last_id(self):
        with self._cond:
            return self._next - 1
