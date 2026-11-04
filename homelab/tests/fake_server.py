"""테스트용 아주 작은 서버: python fake_server.py <포트> — /api/ping 은 X-Fake-Token 이 맞아야 200."""
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        want = os.environ.get("FAKE_TOKEN", "")
        if self.path == "/api/ping" and want and self.headers.get("X-Fake-Token") != want:
            self.send_response(401)
            self.end_headers()
            return
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1])
    print("fake server 시작 %d" % port, flush=True)
    if len(sys.argv) > 2 and sys.argv[2] == "spam":
        for i in range(400):
            print("줄 %04d " % i + "x" * 60, flush=True)
    if len(sys.argv) > 2 and sys.argv[2] == "die":
        print("일부러 죽음", flush=True)
        sys.exit(3)
    HTTPServer(("127.0.0.1", port), H).serve_forever()
