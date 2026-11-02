import os
import struct
import zlib


def make_png(path, w=2, h=2, color=(200, 30, 30)):
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def make_jpeg(path, w=3, h=5, filler=b""):
    """헤더 구조만 갖춘 최소 JPEG (APP0 + SOF0 + EOI). 디코딩 불가, 크기 파싱용."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    data = b"\xff\xd8" + app0 + sof + filler + b"\xff\xd9"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


class FakeLLM:
    """chat() 호출을 기록하고 이미지/텍스트 요청에 따라 고정 응답."""
    model = "fake"

    def __init__(self, caption_reply=None, text_reply="오늘은 즐거운 하루였다."):
        self.calls = []
        self.caption_reply = caption_reply or (
            '{"caption": "보스 앞에서 한 컷", "mood": "긴장감", "tags": ["보스","전투"], "notable": true}')
        self.text_reply = text_reply

    def chat(self, messages, **kw):
        self.calls.append(messages)
        content = messages[0]["content"]
        if isinstance(content, list):
            return self.caption_reply
        return self.text_reply
