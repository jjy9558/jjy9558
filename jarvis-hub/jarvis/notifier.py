"""완료 알림: ntfy(선택). JSON 게시 방식이라 한국어 제목도 헤더 인코딩 문제 없음."""
import json
import logging
import threading
import urllib.request

log = logging.getLogger("jarvis.notify")


def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


class Ntfy:
    def __init__(self, cfg, opener=None, spawn=_spawn):
        self.cfg = cfg or {}
        self.opener = opener or urllib.request.urlopen
        self.spawn = spawn

    @property
    def enabled(self):
        return bool(self.cfg.get("enabled") and self.cfg.get("topic") and self.cfg.get("server"))

    def build_request(self, title, message, priority=None, tags=None):
        body = {"topic": self.cfg["topic"], "title": title[:200], "message": message[:3000],
                "priority": int(priority or self.cfg.get("priority", 3))}
        if tags:
            body["tags"] = list(tags)
        req = urllib.request.Request(self.cfg["server"].rstrip("/") + "/",
                                     data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        if self.cfg.get("token"):
            req.add_header("Authorization", "Bearer " + self.cfg["token"])
        return req

    def push(self, title, message, priority=None, tags=None):
        if not self.enabled:
            return False
        req = self.build_request(title, message, priority, tags)
        self.spawn(self._send, req)
        return True

    def _send(self, req):
        try:
            with self.opener(req, timeout=self.cfg.get("timeout", 5)) as r:
                r.read()
        except Exception as e:  # 알림 실패가 허브를 멈추면 안 된다
            log.warning("ntfy 전송 실패: %s", e)
