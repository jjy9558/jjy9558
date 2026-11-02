"""테스트 공용: 합성 JPEG(EXIF/GPS 포함), 가짜 LLM opener, 출력 캡처."""
import io
import json
import struct
from contextlib import contextmanager

from sellstudio.cli import main


def _rat(n, d=1):
    return struct.pack("<II", n, d)


def build_tiff(gps=True, orientation=6, lat=(37, 30, 0), lon=(127, 2, 30)):
    """리틀엔디안 TIFF: IFD0(Make, Model, Orientation, DateTime, GPS 포인터) + GPS IFD."""
    make, model, dt = b"TestCam\x00", b"Model X\x00", b"2026:01:02 03:04:05\x00"
    n0 = 5 if gps else 4
    ifd0_size = 2 + n0 * 12 + 4
    data_off = 8 + ifd0_size
    blobs = []

    def place(blob):
        off = data_off + sum(len(b) for b in blobs)
        blobs.append(blob)
        return off

    make_o, model_o, dt_o = place(make), place(model), place(dt)
    gps_off = data_off + sum(len(b) for b in blobs)
    entries = [
        (0x010F, 2, len(make), make_o),
        (0x0110, 2, len(model), model_o),
        (0x0112, 3, 1, orientation),
        (0x0132, 2, len(dt), dt_o),
    ]
    if gps:
        entries.append((0x8825, 4, 1, gps_off))
    ifd0 = struct.pack("<H", len(entries))
    for tag, typ, cnt, val in entries:
        if typ == 3:
            ifd0 += struct.pack("<HHIHH", tag, typ, cnt, val, 0)
        else:
            ifd0 += struct.pack("<HHII", tag, typ, cnt, val)
    ifd0 += b"\x00\x00\x00\x00"
    body = b"".join(blobs)
    tiff = b"II*\x00" + struct.pack("<I", 8) + ifd0 + body
    if gps:
        # GPS IFD: LatRef, Lat, LonRef, Lon
        base = len(tiff) + 2 + 4 * 12 + 4
        lat_b = b"".join(_rat(x) for x in lat)
        lon_b = b"".join(_rat(x) for x in lon)
        g = struct.pack("<H", 4)
        g += struct.pack("<HHI4s", 1, 2, 2, b"N\x00\x00\x00")
        g += struct.pack("<HHII", 2, 5, 3, base)
        g += struct.pack("<HHI4s", 3, 2, 2, b"E\x00\x00\x00")
        g += struct.pack("<HHII", 4, 5, 3, base + len(lat_b))
        g += b"\x00\x00\x00\x00" + lat_b + lon_b
        tiff += g
    return tiff


def make_jpeg(gps=True, orientation=6, exif=True, width=64, height=48, xmp_gps=False, payload=b"\x11\x22\x33"):
    out = b"\xff\xd8"
    if exif:
        seg = b"Exif\x00\x00" + build_tiff(gps=gps, orientation=orientation)
        out += b"\xff\xe1" + struct.pack(">H", len(seg) + 2) + seg
    if xmp_gps:
        x = b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta exif:GPSLatitude='37,30N'/>"
        out += b"\xff\xe1" + struct.pack(">H", len(x) + 2) + x
    jfif = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    out += b"\xff\xe0" + struct.pack(">H", len(jfif) + 2) + jfif
    sof = struct.pack(">BHHB", 8, height, width, 1) + b"\x01\x11\x00"
    out += b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof
    sos = b"\x01\x01\x00\x00\x3f\x00"
    out += b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos
    return out + payload + b"\xff\xd9"


class FakeResp:
    status = 200

    def __init__(self, payload):
        self._b = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._b

    def getcode(self):
        return 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """vision 요청에 줄 JSON 응답을 차례로 돌려준다. 값이 Exception 이면 던진다."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        r = self.replies.pop(0) if self.replies else self.replies_default()
        if isinstance(r, Exception):
            raise r
        text = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        return FakeResp({"choices": [{"message": {"content": text}}]})

    def replies_default(self):
        return {"caption": "기본"}

    def last_json(self):
        return json.loads(self.requests[-1].data.decode("utf-8"))


def run(argv, stdin=None):
    """(코드, 표준출력 텍스트, 표준에러 텍스트)"""
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, out=out, err=err)
    return code, out.getvalue(), err.getvalue()
