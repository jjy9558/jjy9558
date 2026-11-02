"""선택 기능: 로컬 이벤트 수신기. POST /api/event {"type":"sigh","n":1} + 토큰.
s21-sidekick(또는 폰 페이지)이 이 주소로 보내면 카운터가 올라간다. 토큰 없으면 시작 거부."""
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class EventServer:
    def __init__(self, host, port, token, kinds, on_event):
        if not token:
            raise ValueError("이벤트 수신기에는 토큰이 필요합니다")
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                tok = self.headers.get("X-Sidekick-Token") or ""
                auth = self.headers.get("Authorization") or ""
                if not tok and auth.startswith("Bearer "):
                    tok = auth[7:]
                if not hmac.compare_digest(tok.strip().encode(), token.encode()):
                    return self._send(401, {"error": "토큰이 없거나 틀렸습니다"})
                if self.path != "/api/event":
                    return self._send(404, {"error": "not found"})
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > 4096:
                        return self._send(413, {"error": "too large"})
                    obj = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                    kind, n = obj.get("type"), obj.get("n", 1)
                    if kind not in kinds:
                        return self._send(400, {"error": "알 수 없는 type", "allowed": list(kinds)})
                    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 10000:
                        return self._send(400, {"error": "n은 1~10000 정수"})
                    outer.on_event(kind, n)
                except (ValueError, AttributeError) as e:
                    return self._send(400, {"error": str(e)})
                self._send(200, {"ok": True, "type": kind})

        self.on_event = on_event
        self.httpd = ThreadingHTTPServer((host, port), H)
        self.port = self.httpd.server_address[1]
        self.thread = Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
