"""알림 전송: ntfy(JSON 발행 API) 와 s21-sidekick(POST /api/say). 별도 스레드 큐로 오디오 처리를 막지 않는다.

- ntfy: 서버 루트에 JSON `{"topic","title","message","priority","tags"}` 를 POST (UTF-8 한글 안전).
  토큰은 설정 파일이 아니라 환경변수(`token_env`)에서 읽는다. 공개 ntfy.sh 의 토픽은 이름을 아는 사람 누구나
  구독할 수 있으니 추측하기 어려운 토픽명을 쓰거나 직접 호스팅한 서버를 쓰자.
- sidekick: `POST {url}/api/say` + `X-Sidekick-Token` 헤더, 본문 `{"text","module"}`. (s21-sidekick 은 분당 12회 제한)
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional


class Notifier:
    def __init__(self, cfg: Dict[str, Any], urlopen: Optional[Callable] = None, env: Optional[Dict[str, str]] = None,
                 printer: Optional[Callable[[str], None]] = None, sync: bool = False):
        self.cfg = cfg
        self.urlopen = urlopen or urllib.request.urlopen
        self.env = env if env is not None else os.environ
        self.printer = printer or (lambda s: print(s, file=sys.stderr, flush=True))
        self.sync = sync
        self.errors: List[str] = []
        self.sent: List[Dict[str, Any]] = []
        self._q: "queue.Queue" = queue.Queue(maxsize=100)
        self._thread: Optional[threading.Thread] = None
        if not sync:
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    def notify(self, title: str, message: str, priority: int = 3, tags: Optional[List[str]] = None):
        item = (title, message, priority, tags or [])
        if self.sync:
            self._deliver(*item)
        else:
            try:
                self._q.put_nowait(item)
            except queue.Full:
                self.errors.append("알림 큐가 가득 참")

    def close(self, wait: float = 5.0):
        if self._thread is not None:
            self._q.put(None)
            self._thread.join(wait)

    def _worker(self):
        while True:
            item = self._q.get()
            if item is None:
                return
            try:
                self._deliver(*item)
            except Exception as e:  # 알림 실패가 엔진을 죽이면 안 된다
                self.errors.append(str(e))

    # ------------------------------------------------------------------ 전달
    def _deliver(self, title, message, priority, tags):
        n = self.cfg.get("notify", {})
        self.sent.append({"title": title, "message": message})
        if n.get("console", True):
            self.printer("[알림] %s: %s" % (title, message))
        nt = n.get("ntfy", {})
        if nt.get("enabled") and nt.get("topic"):
            try:
                self._ntfy(nt, title, message, priority, tags)
            except Exception as e:
                self.errors.append("ntfy: %s" % e)
        sk = n.get("sidekick", {})
        if sk.get("enabled"):
            try:
                self._sidekick(sk, message)
            except Exception as e:
                self.errors.append("sidekick: %s" % e)

    def _post_json(self, url: str, payload: dict, headers: Dict[str, str]):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        h = {"Content-Type": "application/json; charset=utf-8"}
        h.update(headers)
        req = urllib.request.Request(url, data=body, headers=h, method="POST")
        with self.urlopen(req, timeout=5) as resp:
            resp.read()

    def _ntfy(self, nt, title, message, priority, tags):
        headers = {}
        tok = self.env.get(nt.get("token_env") or "")
        if tok:
            headers["Authorization"] = "Bearer " + tok
        payload = {"topic": nt["topic"], "title": title, "message": message,
                   "priority": int(nt.get("priority", priority)), "tags": tags}
        self._post_json(nt.get("server", "https://ntfy.sh").rstrip("/") + "/", payload, headers)

    def _sidekick(self, sk, message):
        headers = {}
        tok = self.env.get(sk.get("token_env") or "")
        if tok:
            headers["X-Sidekick-Token"] = tok
        self._post_json(sk.get("url", "http://127.0.0.1:8765").rstrip("/") + "/api/say",
                        {"text": message[:280], "module": sk.get("module", "sound-watch")}, headers)
