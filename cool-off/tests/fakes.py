"""테스트용 가짜 네트워크 / 시계 도우미."""

import datetime as dt
import urllib.error


class FakeResponse:
    def __init__(self, status=200):
        self.status = status
        self.closed = False

    def close(self):
        self.closed = True


class FakeOpener:
    """urllib.request.urlopen 대신 넣는 가짜. 호출 기록을 남기고, 실패를 흉내 낼 수 있습니다."""

    def __init__(self, error=None, status=200):
        self.error = error
        self.status = status
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append({
            "url": request.full_url,
            "method": request.get_method(),
            "body": request.data.decode("utf-8") if request.data else "",
            "headers": dict(request.header_items()),
            "timeout": timeout,
        })
        if self.error is not None:
            raise self.error
        return FakeResponse(self.status)


def network_down():
    return urllib.error.URLError("Name or service not known")


def at(year, month, day, hour=10, minute=0):
    return dt.datetime(year, month, day, hour, minute)
