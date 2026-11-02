"""워커: 허브에서 자기 caps/타입에 맞는 작업을 빌려 와 핸들러로 실행하고 결과를 보고한다.

메인 PC(5080):  python -m jarvis worker --config worker.json --caps gpu,llm,windows
미니 PC:        python -m jarvis worker --config worker.json --caps lan,speaker   (wake, notify.say)
"""
import logging
import platform
import threading
import time

from . import __version__
from .client import HubError, HubUnavailable
from .handlers import HandlerError, PermanentError

log = logging.getLogger("jarvis.worker")


class Worker:
    def __init__(self, cfg, client, handlers, stop=None, sleep=time.sleep):
        self.cfg = cfg
        self.client = client
        self.handlers = handlers
        self.stop = stop or threading.Event()
        self.sleep = sleep
        self._active = set()
        self._lock = threading.Lock()
        self.processed = 0

    @property
    def caps(self):
        return list(self.cfg["caps"])

    @property
    def types(self):
        return sorted(self.handlers)

    def info(self):
        return {"version": __version__, "host": platform.node(), "os": platform.system(),
                "concurrency": self.cfg.get("concurrency", 1)}

    # ---- 작업 하나 ----
    def execute(self, job):
        """핸들러 실행 -> complete/fail 보고. 결과 상태 문자열을 반환."""
        handler = self.handlers.get(job["type"])
        with self._lock:
            self._active.add(job["id"])
        try:
            if handler is None:
                self.client.fail(job["id"], "이 워커에 %s 핸들러가 없습니다" % job["type"], retry=False)
                return "failed"
            try:
                result = handler.run(job)
            except PermanentError as e:
                log.warning("작업 #%s 실패(재시도 없음): %s", job["id"], e)
                self.client.fail(job["id"], str(e), retry=False)
                return "failed"
            except HandlerError as e:
                log.warning("작업 #%s 실패(재시도 가능): %s", job["id"], e)
                self.client.fail(job["id"], str(e), retry=True)
                return "retry"
            except Exception as e:  # 예상 못 한 오류도 재시도 대상
                log.exception("작업 #%s 처리 중 오류", job["id"])
                self.client.fail(job["id"], "%s: %s" % (type(e).__name__, e), retry=True)
                return "retry"
            self.client.complete(job["id"], result if isinstance(result, dict) else {"value": result})
            return "done"
        finally:
            with self._lock:
                self._active.discard(job["id"])
            self.processed += 1

    def run_once(self, wait=None):
        wait = self.cfg.get("lease_wait_seconds", 20) if wait is None else wait
        job = self.client.lease(self.caps, self.types, wait=wait, info=self.info())
        if not job:
            return None
        log.info("작업 #%s (%s) 시작", job["id"], job["type"])
        return self.execute(job)

    # ---- 루프 ----
    def heartbeat_loop(self):
        every = self.cfg.get("heartbeat_seconds", 10)
        while not self.stop.wait(every):
            with self._lock:
                active = list(self._active)
            try:
                self.client.heartbeat(self.caps, self.types, active, self.info())
            except (HubError, HubUnavailable) as e:
                log.warning("하트비트 실패: %s", e)

    def lease_loop(self):
        backoff = self.cfg.get("retry_backoff_seconds", 5)
        while not self.stop.is_set():
            try:
                self.run_once()
            except HubUnavailable as e:
                log.warning("%s (%ss 후 재시도)", e, backoff)
                self.stop.wait(backoff)
            except HubError as e:
                log.error("허브 오류: %s", e)
                if e.code in (401, 403):
                    log.error("인증/권한 문제라 멈춥니다. hub.json의 devices 설정을 확인하세요.")
                    self.stop.set()
                    return
                self.stop.wait(backoff)

    def run_forever(self):
        if not self.handlers:
            log.error("켜진 핸들러가 없습니다. worker.json의 handlers를 확인하세요.")
            return 2
        log.info("워커 %s 시작: caps=%s types=%s", self.cfg["device"], self.caps, self.types)
        threads = [threading.Thread(target=self.heartbeat_loop, daemon=True, name="heartbeat")]
        for i in range(max(1, int(self.cfg.get("concurrency", 1)))):
            threads.append(threading.Thread(target=self.lease_loop, daemon=True, name="lease-%d" % i))
        for t in threads:
            t.start()
        try:
            while not self.stop.is_set():
                self.stop.wait(1)
        except KeyboardInterrupt:
            self.stop.set()
        return 0
