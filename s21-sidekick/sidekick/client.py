"""PC 쪽 작은 클라이언트: notify CLI와 Claude Code 훅이 사이드킥 서버에 붙을 때 쓴다.

설정 파일 찾는 순서: --config > 환경변수 SIDEKICK_CONFIG > 이 저장소 루트의 config.json.
(현재 폴더의 config.json은 일부러 보지 않는다: 훅은 남의 저장소 안에서 돌기 때문에,
 그 저장소가 심어 둔 config.json이 위험 명령 목록을 바꾸지 못하게.)
환경변수 SIDEKICK_URL / SIDEKICK_TOKEN이 있으면 설정 파일 값보다 우선.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .config import build_config

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ServerUnavailable(RuntimeError):
    pass


class ServerError(RuntimeError):
    def __init__(self, code, body):
        self.code = code
        self.body = body if isinstance(body, dict) else {}
        super().__init__("HTTP %s: %s" % (code, self.body.get("error") or body))


def resolve_config_path(arg=None, environ=None):
    env = os.environ if environ is None else environ
    return arg or env.get("SIDEKICK_CONFIG") or os.path.join(REPO_ROOT, "config.json")


def load_client_config(path):
    """검증(토큰/비상코드 필수) 없이 기본값 병합만. 파일이 없으면 기본값."""
    raw = {}
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    return build_config(raw)


def server_url(cfg, environ=None, override=None):
    env = os.environ if environ is None else environ
    url = override or env.get("SIDEKICK_URL")
    if url:
        return url.rstrip("/")
    host = cfg["server"].get("host") or "127.0.0.1"
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return "http://%s:%d" % (host, int(cfg["server"]["port"]))


def server_token(cfg, environ=None):
    env = os.environ if environ is None else environ
    return env.get("SIDEKICK_TOKEN") or cfg["server"].get("token") or ""


class SidekickClient:
    def __init__(self, base_url, token, timeout=10):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, path, body=None, timeout=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data,
                                     method="POST" if data is not None else "GET")
        req.add_header("X-Sidekick-Token", self.token)
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
            raise ServerError(e.code, payload) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ServerUnavailable("사이드킥 서버(%s)에 연결할 수 없습니다: %s" % (self.base_url, e)) from None

    # ---- 결재 ----
    def request_approval(self, payload):
        return self.call("/api/approval/request", payload)

    def wait_approval(self, rid, wait):
        return self.call("/api/approval/wait?id=%s&wait=%d" % (urllib.parse.quote(str(rid), safe=""), int(wait)), timeout=wait + 10)
