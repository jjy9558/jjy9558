"""허브(미니PC 상주): 기기 인증 + 작업 큐 + 기기 상태 + 알림 + 대시보드 HTTP API."""
import fnmatch
import hmac
import json
import logging
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__
from .config import TYPE_RE, ConfigError, check_tags
from .notifier import Ntfy
from .router import Router
from .store import FINAL, Store, StoreError

log = logging.getLogger("jarvis.hub")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _size(obj):
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


class Hub:
    def __init__(self, cfg, store=None, clock=time.time, ntfy=None, today=None):
        self.cfg = cfg
        self.clock = clock
        self.store = store or Store(cfg["db"], clock=clock, keep_events=cfg["queue"]["keep_events"])
        self.ntfy = ntfy if ntfy is not None else Ntfy(cfg["notify"]["ntfy"])
        self.today = today  # 테스트용: () -> date
        self.router = Router(self)
        self._stop = threading.Event()

    # ---- 인증 / 기기 ----
    def authenticate(self, device_id, token):
        dev = self.cfg["devices"].get(device_id or "")
        if not dev or not token:
            return None
        if not hmac.compare_digest(str(token).encode("utf-8"), dev["token"].encode("utf-8")):
            return None
        return dev

    def seen(self, device_id, caps=None, types=None, info=None):
        if self.store.touch(device_id, caps, types, info):
            dev = self.cfg["devices"].get(device_id, {})
            self.store.add_event("device.online", "%s 연결됨" % dev.get("label", device_id), device=device_id)

    def device_list(self):
        rows = {d["id"]: d for d in self.store.devices()}
        cutoff = self.clock() - self.cfg["presence"]["offline_after_seconds"]
        out = []
        for did, dev in self.cfg["devices"].items():
            r = rows.get(did) or {}
            caps = r.get("caps") or dev.get("caps") or []
            out.append({"id": did, "label": dev["label"], "worker": bool(dev["worker"]),
                        "online": bool(r.get("last_seen") and r["last_seen"] >= cutoff),
                        "last_seen": r.get("last_seen"), "caps": caps, "types": r.get("types") or [],
                        "allowed_caps": dev.get("caps"),
                        "info": r.get("info") or {}})
        return out

    def wake_devices(self):
        """WOL 대상(메인 PC) 역할 기기 = wake.for_caps 중 하나라도 가진 기기."""
        want = set(self.cfg["wake"]["for_caps"])
        return [d for d in self.device_list()
                if d["worker"] and want & set(d["allowed_caps"] if d["allowed_caps"] is not None else d["caps"])]

    def wake_label(self):
        devs = self.wake_devices()
        return devs[0]["label"] if devs else self.cfg["wake"]["label"]

    def wake_target_online(self):
        return any(d["online"] for d in self.wake_devices())

    def can_run_online(self, caps, type):
        for d in self.device_list():
            if d["online"] and d["worker"] and set(caps) <= set(d["caps"]) and type in d["types"]:
                return True
        return False

    def _dev(self, device_id):
        return self.cfg["devices"][device_id]

    # ---- 작업 ----
    def submit(self, device_id, body):
        q = self.cfg["queue"]
        dev = self._dev(device_id)
        if not isinstance(body, dict):
            raise ApiError(400, "JSON 객체가 필요합니다")
        jtype = body.get("type")
        if not isinstance(jtype, str) or len(jtype) > 64 or not TYPE_RE.match(jtype):
            raise ApiError(400, "type 형식 오류 (예: llm.chat)")
        if not any(fnmatch.fnmatchcase(jtype, pat) for pat in dev.get("submit") or []):
            raise ApiError(403, "이 기기(%s)는 %s 작업을 넣을 수 없습니다" % (device_id, jtype))
        payload = body.get("payload", {})
        if not isinstance(payload, dict):
            raise ApiError(400, "payload는 객체여야 합니다")
        if _size(payload) > q["max_payload_kb"] * 1024:
            raise ApiError(413, "payload가 너무 큽니다 (최대 %dKB)" % q["max_payload_kb"])
        try:
            caps = check_tags(body.get("caps", []))
        except ConfigError as e:
            raise ApiError(400, str(e))
        try:
            priority = max(-100, min(100, int(body.get("priority", 0))))
            max_attempts = max(1, min(q["max_attempts_limit"], int(body.get("max_attempts", q["default_max_attempts"]))))
            lease = max(5.0, min(float(q["max_lease_seconds"]), float(body.get("lease_seconds", q["default_lease_seconds"]))))
        except (TypeError, ValueError):
            raise ApiError(400, "priority/max_attempts/lease_seconds는 숫자여야 합니다")
        title = str(body.get("title") or "")[:120]
        if self.store.count_active() >= q["max_active_jobs"]:
            raise ApiError(429, "대기 중인 작업이 너무 많습니다")
        needs = jtype in self.cfg["approval"]["required_types"] and not dev.get("trusted")
        job = self.store.submit(jtype, payload, caps, priority, device_id, max_attempts, lease,
                                notify=bool(body.get("notify", False)), title=title, needs_approval=needs)
        self.store.add_event("job.submitted", "작업 #%d %s" % (job["id"], title or jtype),
                             job=job["id"], type=jtype, status=job["status"])
        if not needs:
            self.maybe_wake()
        return job

    def internal_submit(self, jtype, payload, caps=(), priority=0, notify=False, title="", created_by="hub"):
        job = self.store.submit(jtype, payload, caps, priority, created_by,
                                self.cfg["queue"]["default_max_attempts"], 60, notify=notify, title=title)
        self.store.add_event("job.submitted", "작업 #%d %s" % (job["id"], title or jtype),
                             job=job["id"], type=jtype, status=job["status"])
        return job

    def lease(self, device_id, body):
        dev = self._dev(device_id)
        if not dev["worker"]:
            raise ApiError(403, "워커로 등록된 기기가 아닙니다 (devices.%s.worker)" % device_id)
        caps, types = self._caps_types(device_id, dev, body)
        wait = max(0.0, min(float(body.get("wait", 0) or 0), float(self.cfg["queue"]["max_lease_wait_seconds"])))
        self.seen(device_id, caps, types, body.get("info") if isinstance(body.get("info"), dict) else None)
        job = self.store.wait_lease(device_id, caps, types, wait)
        if job:
            self.store.add_event("job.leased", "작업 #%d → %s" % (job["id"], dev["label"]),
                                 job=job["id"], device=device_id, attempt=job["attempts"])
        return job

    def _caps_types(self, device_id, dev, body):
        try:
            caps = check_tags(body.get("caps", []))
        except ConfigError as e:
            raise ApiError(400, str(e))
        if "caps" in dev and not set(caps) <= set(dev["caps"]):
            raise ApiError(403, "허용되지 않은 caps: %s" % ", ".join(sorted(set(caps) - set(dev["caps"]))))
        types = body.get("types", [])
        if not isinstance(types, list) or not all(isinstance(t, str) and TYPE_RE.match(t) for t in types):
            raise ApiError(400, "types 형식 오류")
        return caps, types[:50]

    def heartbeat(self, device_id, body):
        dev = self._dev(device_id)
        if dev["worker"] and ("caps" in body or "types" in body):
            caps, types = self._caps_types(device_id, dev, body)
            self.seen(device_id, caps, types, body.get("info") if isinstance(body.get("info"), dict) else None)
        else:
            self.seen(device_id)
        jobs = body.get("jobs") or []
        if not isinstance(jobs, list) or not all(isinstance(j, int) for j in jobs):
            raise ApiError(400, "jobs는 작업 id 목록이어야 합니다")
        return {"ok": True, "extended": self.store.extend(device_id, jobs[:100])}

    def complete(self, device_id, job_id, body):
        result = body.get("result", {})
        if _size(result) > self.cfg["queue"]["max_result_kb"] * 1024:
            raise ApiError(413, "result가 너무 큽니다 (최대 %dKB)" % self.cfg["queue"]["max_result_kb"])
        job = self.store.complete(job_id, device_id, result)
        self._finished(job)
        return job

    def fail(self, device_id, job_id, body):
        job, final = self.store.fail(job_id, device_id, str(body.get("error") or "실패")[:2000],
                                     retry=bool(body.get("retry", True)),
                                     retry_delay=self.cfg["queue"]["retry_delay_seconds"])
        if final:
            self._finished(job)
        else:
            self.store.add_event("job.retry", "작업 #%d 재시도 예정" % job["id"], job=job["id"], error=job["error"])
        return job

    def approve(self, device_id, job_id):
        if not self._dev(device_id).get("approve"):
            raise ApiError(403, "이 기기는 승인 권한이 없습니다")
        job = self.store.set_status(job_id, ("waiting_approval",), "queued")
        self.store.add_event("job.approved", "작업 #%d 승인" % job["id"], job=job["id"], by=device_id)
        self.maybe_wake()
        return job

    def reject(self, device_id, job_id):
        job = self.store.get(job_id)
        dev = self._dev(device_id)
        if not dev.get("approve") and job["created_by"] != device_id:
            raise ApiError(403, "이 기기는 이 작업을 취소할 수 없습니다")
        job = self.store.set_status(job_id, ("waiting_approval", "queued"), "cancelled",
                                    error="%s 가 취소함" % device_id)
        self.store.add_event("job.cancelled", "작업 #%d 취소" % job["id"], job=job["id"], by=device_id)
        return job

    # ---- 완료 알림 ----
    def notify_text(self, job):
        name = job["title"] or job["type"]
        if job["status"] == "done":
            r = job["result"] if isinstance(job["result"], dict) else {}
            detail = r.get("summary") or r.get("text") or ""
            detail = str(detail).strip().replace("\n", " ")
            if len(detail) > 200:
                detail = detail[:200] + "…"
            return "작업 %d번 %s 끝났어요.%s" % (job["id"], name, (" " + detail) if detail else "")
        if job["status"] == "failed":
            return "작업 %d번 %s 실패했어요. %s" % (job["id"], name, (job["error"] or "")[:150])
        return "작업 %d번 %s %s." % (job["id"], name, job["status"])

    def _finished(self, job):
        kind = "job.done" if job["status"] == "done" else "job.failed"
        text = self.notify_text(job)
        self.store.add_event(kind, text, job=job["id"], type=job["type"])
        if not job["notify"] or job["type"] == "notify.say":
            return
        n = self.cfg["notify"]
        if n["say_events"]:
            self.store.add_event("say", text, job=job["id"])
        if n["say_job"]:
            self.internal_submit("notify.say", {"text": text, "module": "jarvis"}, caps=n["say_caps"],
                                 priority=50, title="알림")
        self.ntfy.push("자비스: " + (job["title"] or job["type"]), text,
                       priority=4 if job["status"] == "failed" else None,
                       tags=["white_check_mark"] if job["status"] == "done" else ["warning"])

    # ---- Wake-on-LAN ----
    def request_wake(self, force=False, created_by="hub", notify=False):
        """wake 작업을 넣는다. 이미 진행 중이거나(항상) 쿨다운 중이면(force 아니면) None."""
        w = self.cfg["wake"]
        if not w["enabled"] or self.store.has_active("wake"):
            return None
        now = self.clock()
        last = self.store.meta_get("wake_last", 0)
        if not force and now - last < w["cooldown_seconds"]:
            return None
        self.store.meta_set("wake_last", now)
        label = self.wake_label()
        job = self.internal_submit("wake", {"mac": w["mac"], "broadcast": w["broadcast"], "port": w["port"],
                                            "label": label}, priority=90, notify=notify,
                                   title="%s 켜기" % label, created_by=created_by)
        self.store.add_event("wake.requested", "%s 켜기 신호 요청" % label, job=job["id"])
        return job

    def maybe_wake(self):
        w = self.cfg["wake"]
        if not w["enabled"]:
            return None
        want = set(w["for_caps"])
        for job in self.store.list(status="queued", limit=200):
            if want & set(job["caps"]) and not self.can_run_online(job["caps"], job["type"]):
                return self.request_wake()
        return None

    # ---- 주기 작업 ----
    def tick(self):
        for job, final in self.store.reap(self.cfg["queue"]["retry_delay_seconds"]):
            if final:
                self._finished(job)
            else:
                self.store.add_event("job.retry", "작업 #%d 임대 만료, 다시 대기" % job["id"], job=job["id"])
        self.store.expire_queued("notify.say", self.cfg["notify"]["say_ttl_seconds"])
        for did in self.store.mark_offline(self.cfg["presence"]["offline_after_seconds"]):
            label = self.cfg["devices"].get(did, {}).get("label", did)
            self.store.add_event("device.offline", "%s 연결 끊김" % label, device=did)
        self.maybe_wake()

    def run_ticker(self):
        while not self._stop.wait(self.cfg["tick_seconds"]):
            try:
                self.tick()
            except Exception:
                log.exception("tick 실패")

    def stop(self):
        self._stop.set()

    def status(self):
        jobs = self.store.list(limit=30)
        return {"version": __version__, "now": self.clock(), "counts": self.store.counts(),
                "devices": self.device_list(), "jobs": [_brief(j) for j in jobs],
                "events": self.store.recent_events(20),
                "wake": {"enabled": self.cfg["wake"]["enabled"], "label": self.wake_label(),
                         "target_online": self.wake_target_online()}}


def _brief(job):
    j = dict(job)
    if isinstance(j.get("result"), dict):
        r = dict(j["result"])
        for k in ("text", "stdout", "stderr"):
            if isinstance(r.get(k), str) and len(r[k]) > 600:
                r[k] = r[k][:600] + "…"
        j["result"] = r
    j["payload"] = {k: (v[:200] + "…" if isinstance(v, str) and len(v) > 200 else v)
                    for k, v in (job.get("payload") or {}).items() if k != "messages"}
    return j


# ---------------- HTTP ----------------
_JOB_RE = re.compile(r"^/api/jobs/(\d{1,12})(?:/(complete|fail|approve|reject|cancel))?$")


class Handler(BaseHTTPRequestHandler):
    server_version = "JarvisHub/" + __version__
    protocol_version = "HTTP/1.1"

    @property
    def hub(self):
        return self.server.hub

    def log_message(self, fmt, *args):
        if not getattr(self.server, "quiet", False):
            log.info("%s %s", self.address_string(), fmt % args)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _json(self):
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(411, "Content-Length가 필요합니다")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "Content-Length 오류")
        limit = self.hub.cfg["server"]["max_body_kb"] * 1024
        if n > limit:
            self.close_connection = True
            # 본문을 읽지 않고 닫으면 Windows 클라이언트가 응답 대신 연결 리셋(10053/10054)을 받는다.
            # 적당한 크기까지는 버리면서 읽어 정상적으로 413 을 돌려준다.
            left = min(n, 8 * 1024 * 1024)
            while left > 0:
                chunk = self.rfile.read(min(65536, left))
                if not chunk:
                    break
                left -= len(chunk)
            raise ApiError(413, "요청이 너무 큽니다 (최대 %dKB)" % self.hub.cfg["server"]["max_body_kb"])
        raw = self.rfile.read(n) if n > 0 else b""
        try:
            obj = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "JSON 형식이 아닙니다")
        if not isinstance(obj, dict):
            raise ApiError(400, "JSON 객체가 필요합니다")
        return obj

    def _auth(self):
        did = (self.headers.get("X-Jarvis-Device") or "").strip()
        tok = (self.headers.get("X-Jarvis-Token") or "").strip()
        auth = self.headers.get("Authorization") or ""
        if (not did or not tok) and auth.startswith("Bearer ") and ":" in auth:
            did, tok = auth[7:].strip().split(":", 1)
        if not self.hub.authenticate(did, tok):
            raise ApiError(401, "등록되지 않은 기기이거나 토큰이 틀렸습니다")
        return did

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        url = urlparse(self.path)
        path, q = url.path, parse_qs(url.query)
        try:
            if method == "GET" and path in ("/", "/index.html"):
                with open(os.path.join(STATIC_DIR, "dashboard.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if method == "GET" and path == "/api/ping":
                return self._send(200, {"ok": True, "version": __version__})
            if not path.startswith("/api/"):
                raise ApiError(404, "not found")
            did = self._auth()
            body = self._json() if method == "POST" else {}
            if path not in ("/api/lease", "/api/heartbeat"):
                self.hub.seen(did)
            code, out = self._route(method, path, q, did, body)
            self._send(code, out)
        except ApiError as e:
            self._send(e.code, {"error": e.message})
        except StoreError as e:
            self._send(e.code, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log.exception("처리 실패")
            self._send(500, {"error": "서버 오류: %s" % type(e).__name__})

    def _route(self, method, path, q, did, body):
        hub = self.hub
        if method == "POST" and path == "/api/jobs":
            return 201, {"job": hub.submit(did, body)}
        if method == "GET" and path == "/api/jobs":
            status = (q.get("status") or [None])[0]
            limit = max(1, min(200, int((q.get("limit") or ["50"])[0])))
            return 200, {"jobs": hub.store.list(status=status, limit=limit)}
        if method == "POST" and path == "/api/lease":
            return 200, {"job": hub.lease(did, body)}
        if method == "POST" and path == "/api/heartbeat":
            return 200, hub.heartbeat(did, body)
        if method == "GET" and path == "/api/devices":
            return 200, {"devices": hub.device_list()}
        if method == "GET" and path == "/api/events":
            since = int((q.get("since") or ["0"])[0])
            wait = max(0.0, min(float((q.get("wait") or ["0"])[0]), 30.0))
            return 200, {"events": hub.store.events_since(since, wait), "last_id": hub.store.last_event_id()}
        if method == "GET" and path == "/api/status":
            return 200, hub.status()
        if method == "POST" and path == "/api/say":
            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ApiError(400, "text가 비었습니다")
            return 200, hub.router.handle(did, text, direct=bool(body.get("direct")), file=body.get("file"))
        m = _JOB_RE.match(path)
        if m:
            jid, action = int(m.group(1)), m.group(2)
            if method == "GET" and not action:
                return 200, {"job": hub.store.get(jid)}
            if method == "POST" and action == "complete":
                return 200, {"job": hub.complete(did, jid, body)}
            if method == "POST" and action == "fail":
                return 200, {"job": hub.fail(did, jid, body)}
            if method == "POST" and action == "approve":
                return 200, {"job": hub.approve(did, jid)}
            if method == "POST" and action in ("reject", "cancel"):
                return 200, {"job": hub.reject(did, jid)}
        raise ApiError(404, "not found")


def make_server(hub, host=None, port=None, quiet=False):
    host = host if host is not None else hub.cfg["server"]["host"]
    port = port if port is not None else hub.cfg["server"]["port"]
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.hub = hub
    srv.quiet = quiet
    return srv


__all__ = ["Hub", "ApiError", "make_server", "FINAL"]
