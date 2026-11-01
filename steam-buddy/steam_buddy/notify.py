"""알림 보내기. ntfy(https://ntfy.sh) 주제로 보내면 폰 앱에서 바로 받을 수 있다."""

import base64
import urllib.request


def send_ntfy(topic, title, message, server="https://ntfy.sh", opener=urllib.request.urlopen):
    req = urllib.request.Request(
        f"{server.rstrip('/')}/{topic}",
        data=message.encode("utf-8"),
        method="POST",
        # HTTP 헤더는 latin-1만 되므로 한글 제목은 RFC 2047 형식으로 감싼다
        headers={"Title": _encode_header(title), "Tags": "video_game,alarm_clock"},
    )
    with opener(req, timeout=15) as resp:
        return resp.status


def _encode_header(text):
    try:
        text.encode("latin-1")
        return text
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="
