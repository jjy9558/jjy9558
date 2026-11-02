"""테스트용 가짜 opener (네트워크 없음)."""
import io
import json
import urllib.error


class FakeResponse:
    def __init__(self, body=b"", status=200):
        self._body = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """open(req, timeout)을 기록하고 정해 둔 응답을 돌려준다.

    replies: FakeResponse, dict/bytes(200 응답), 예외 인스턴스, 또는 (status, body) 튜플(HTTPError).
    """

    def __init__(self, *replies):
        self.replies = list(replies) or [FakeResponse({})]
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append((req, timeout))
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, BaseException):
            raise r
        if isinstance(r, tuple):
            status, body = r
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(data))
        if isinstance(r, FakeResponse):
            return r
        return FakeResponse(r)

    # 편의
    @property
    def last(self):
        return self.requests[-1][0]

    def last_json(self):
        return json.loads(self.last.data.decode("utf-8"))

    def header(self, name):
        # urllib.request.Request는 헤더 이름을 capitalize() 해서 저장한다
        return self.last.get_header(name.capitalize())


def chat_reply(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}
