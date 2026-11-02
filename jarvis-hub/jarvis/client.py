"""허브 HTTP 클라이언트 (워커, CLI가 사용)."""
import json
import urllib.error
import urllib.request


class HubUnavailable(RuntimeError):
    pass


class HubError(RuntimeError):
    def __init__(self, code, body):
        self.code = code
        self.body = body if isinstance(body, dict) else {}
        super().__init__("HTTP %s: %s" % (code, self.body.get("error") or body))


class HubClient:
    def __init__(self, base_url, device, token, timeout=15):
        self.base_url = base_url.rstrip("/")
        self.device = device
        self.token = token
        self.timeout = timeout

    def call(self, path, body=None, timeout=None, method=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data,
                                     method=method or ("POST" if data is not None else "GET"))
        req.add_header("X-Jarvis-Device", self.device)
        req.add_header("X-Jarvis-Token", self.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8") or "{}")
            except ValueError:
                payload = {}
            raise HubError(e.code, payload) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise HubUnavailable("허브(%s)에 연결할 수 없습니다: %s" % (self.base_url, e)) from None

    def submit(self, type, payload=None, caps=(), priority=0, **extra):
        body = dict(extra, type=type, payload=payload or {}, caps=list(caps), priority=priority)
        return self.call("/api/jobs", body)["job"]

    def lease(self, caps, types, wait=20, info=None):
        return self.call("/api/lease", {"caps": list(caps), "types": list(types), "wait": wait, "info": info or {}},
                         timeout=wait + 15)["job"]

    def heartbeat(self, caps=None, types=None, jobs=(), info=None):
        body = {"jobs": list(jobs)}
        if caps is not None:
            body["caps"] = list(caps)
        if types is not None:
            body["types"] = list(types)
        if info:
            body["info"] = info
        return self.call("/api/heartbeat", body)

    def complete(self, job_id, result):
        return self.call("/api/jobs/%d/complete" % int(job_id), {"result": result})["job"]

    def fail(self, job_id, error, retry=True):
        return self.call("/api/jobs/%d/fail" % int(job_id), {"error": str(error), "retry": bool(retry)})["job"]

    def job(self, job_id):
        return self.call("/api/jobs/%d" % int(job_id))["job"]

    def jobs(self, status=None, limit=50):
        q = "?limit=%d" % limit + ("&status=%s" % status if status else "")
        return self.call("/api/jobs" + q)["jobs"]

    def approve(self, job_id):
        return self.call("/api/jobs/%d/approve" % int(job_id), {})["job"]

    def reject(self, job_id):
        return self.call("/api/jobs/%d/reject" % int(job_id), {})["job"]

    def say(self, text, direct=False, file=None):
        body = {"text": text, "direct": direct}
        if file:
            body["file"] = file
        return self.call("/api/say", body)

    def status(self):
        return self.call("/api/status")

    def events(self, since=0, wait=0):
        return self.call("/api/events?since=%d&wait=%d" % (since, wait), timeout=wait + 15)
