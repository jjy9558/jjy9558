"""JPEG EXIF를 표준 라이브러리만으로 최소한 읽고(GPS 등), APP1을 빼고 다시 쓴다.

지원 범위(의도적으로 작게):
- JPEG 세그먼트를 SOS 전까지만 훑는다. APP1 안의 ``Exif\\0\\0`` TIFF(II/MM)에서 IFD0, Exif IFD, GPS IFD를 읽는다.
- XMP APP1(``http://ns.adobe.com/xap/1.0/``)에 GPS 문자열이 있는지만 본다.
- PNG/WebP/HEIC 등은 EXIF를 읽지 않는다(``supported=False``). 크기만 PNG/GIF/JPEG에서 읽는다.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

EXIF_HEADER = b"Exif\x00\x00"
XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"

# TIFF 자료형 -> 바이트 크기
_TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


class JpegError(ValueError):
    pass


@dataclass
class ExifInfo:
    is_jpeg: bool = False
    supported: bool = False          # EXIF를 읽을 수 있는 형식인지
    has_exif: bool = False
    has_xmp: bool = False
    gps_ifd: bool = False            # GPS IFD 포인터가 있음 (좌표가 비어 있어도)
    lat: float | None = None
    lon: float | None = None
    xmp_gps: bool = False
    make: str = ""
    model: str = ""
    datetime: str = ""
    serial: str = ""
    orientation: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def has_gps(self) -> bool:
        return self.gps_ifd or self.xmp_gps or self.lat is not None


# ---------------------------------------------------------------- 크기/형식
def sniff_format(raw: bytes) -> str:
    if raw[:2] == b"\xff\xd8":
        return "jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if raw[:3] == b"GIF":
        return "gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    if raw[4:8] == b"ftyp":
        return "heic"
    return "unknown"


def image_size(raw: bytes) -> tuple[int, int] | None:
    fmt = sniff_format(raw)
    try:
        if fmt == "png" and len(raw) >= 24:
            return struct.unpack(">II", raw[16:24])
        if fmt == "gif" and len(raw) >= 10:
            return struct.unpack("<HH", raw[6:10])
        if fmt == "jpeg":
            for marker, seg_start, _e, _m in _segments(raw):
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", raw[seg_start + 1:seg_start + 5])
                    return w, h
    except (struct.error, JpegError):
        return None
    return None


# ---------------------------------------------------------------- 세그먼트
def _segments(raw: bytes):
    """(marker, 데이터 시작, 데이터 끝, 마커 시작) — SOS 헤더까지. 데이터는 길이 2바이트 뒤부터."""
    if raw[:2] != b"\xff\xd8":
        raise JpegError("JPEG가 아닙니다")
    i, n = 2, len(raw)
    while i < n:
        if raw[i] != 0xFF:
            raise JpegError("JPEG 세그먼트 구조가 깨졌습니다 (offset %d)" % i)
        while i < n and raw[i] == 0xFF:  # 채움 바이트
            i += 1
        if i >= n:
            break
        marker = raw[i]
        i += 1
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xD9:
            return
        if i + 2 > n:
            raise JpegError("JPEG가 중간에 잘렸습니다")
        length = struct.unpack(">H", raw[i:i + 2])[0]
        if length < 2 or i + length > n:
            raise JpegError("JPEG 세그먼트 길이가 이상합니다")
        yield marker, i + 2, i + length, i - 2
        if marker == 0xDA:  # SOS: 이후는 압축 데이터
            return
        i += length


# ---------------------------------------------------------------- TIFF/EXIF
def _read_ifd(tiff: bytes, offset: int, endian: str, depth: int = 0) -> dict[int, tuple[int, int, int]]:
    """tag -> (type, count, value_offset_or_inline_pos). 값 위치는 TIFF 안 절대 오프셋."""
    out: dict[int, tuple[int, int, int]] = {}
    if depth > 3 or offset < 8 or offset + 2 > len(tiff):
        return out
    count = struct.unpack(endian + "H", tiff[offset:offset + 2])[0]
    count = min(count, 512)
    for k in range(count):
        p = offset + 2 + k * 12
        if p + 12 > len(tiff):
            break
        tag, typ, cnt = struct.unpack(endian + "HHI", tiff[p:p + 8])
        size = _TYPE_SIZE.get(typ, 0) * cnt
        pos = p + 8 if size <= 4 else struct.unpack(endian + "I", tiff[p + 8:p + 12])[0]
        out[tag] = (typ, cnt, pos)
    return out


def _values(tiff: bytes, entry: tuple[int, int, int], endian: str) -> list:
    typ, cnt, pos = entry
    size = _TYPE_SIZE.get(typ, 0)
    if not size or cnt > 4096 or pos + size * cnt > len(tiff):
        return []
    raw = tiff[pos:pos + size * cnt]
    if typ == 2:
        return [raw.split(b"\x00", 1)[0].decode("latin-1", "replace").strip()]
    if typ in (3, 8):
        return list(struct.unpack(endian + "%dH" % cnt, raw))
    if typ in (4, 9):
        return list(struct.unpack(endian + "%dI" % cnt, raw))
    if typ in (5, 10):
        vals = struct.unpack(endian + "%dI" % (2 * cnt), raw)
        return [(vals[i] / vals[i + 1]) if vals[i + 1] else 0.0 for i in range(0, len(vals), 2)]
    if typ in (1, 7):
        return list(raw)
    return []


def _dms(vals: list, ref: str) -> float | None:
    if len(vals) < 3:
        return None
    deg = vals[0] + vals[1] / 60.0 + vals[2] / 3600.0
    return -deg if ref.upper() in ("S", "W") else deg


def _parse_tiff(tiff: bytes, info: ExifInfo) -> None:
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        info.notes.append("EXIF TIFF 헤더가 이상합니다")
        return
    endian = "<" if tiff[:2] == b"II" else ">"
    if struct.unpack(endian + "H", tiff[2:4])[0] != 42:
        info.notes.append("EXIF TIFF 매직 넘버가 이상합니다")
        return
    ifd0_off = struct.unpack(endian + "I", tiff[4:8])[0]
    ifd0 = _read_ifd(tiff, ifd0_off, endian)

    def text(ifd, tag):
        v = _values(tiff, ifd[tag], endian) if tag in ifd else []
        return v[0] if v and isinstance(v[0], str) else ""

    info.make = text(ifd0, 0x010F)
    info.model = text(ifd0, 0x0110)
    info.datetime = text(ifd0, 0x0132)
    if 0x0112 in ifd0:
        v = _values(tiff, ifd0[0x0112], endian)
        info.orientation = int(v[0]) if v else None
    if 0x8769 in ifd0:
        v = _values(tiff, ifd0[0x8769], endian)
        if v:
            exif_ifd = _read_ifd(tiff, int(v[0]), endian, 1)
            info.serial = text(exif_ifd, 0xA431)
            if not info.datetime:
                info.datetime = text(exif_ifd, 0x9003)
    if 0x8825 in ifd0:
        info.gps_ifd = True
        v = _values(tiff, ifd0[0x8825], endian)
        if v:
            gps = _read_ifd(tiff, int(v[0]), endian, 1)
            lat_ref, lon_ref = text(gps, 1), text(gps, 3)
            if 2 in gps and 4 in gps:
                lat = _dms(_values(tiff, gps[2], endian), lat_ref or "N")
                lon = _dms(_values(tiff, gps[4], endian), lon_ref or "E")
                if lat is not None and lon is not None:
                    info.lat, info.lon = round(lat, 6), round(lon, 6)


def parse_exif(raw: bytes) -> ExifInfo:
    """바이트에서 EXIF 요약을 만든다. JPEG가 아니면 ``supported=False``."""
    info = ExifInfo()
    fmt = sniff_format(raw)
    if fmt != "jpeg":
        info.notes.append("JPEG가 아니라서 EXIF를 읽지 못했습니다(%s). 위치 정보가 들었는지 이 도구로는 알 수 없습니다" % fmt)
        return info
    info.is_jpeg = True
    info.supported = True
    try:
        for marker, s, e, _m in _segments(raw):
            if marker != 0xE1:
                continue
            data = raw[s:e]
            if data.startswith(EXIF_HEADER):
                info.has_exif = True
                try:
                    _parse_tiff(data[len(EXIF_HEADER):], info)
                except (struct.error, ValueError, IndexError):
                    info.notes.append("EXIF를 끝까지 읽지 못했습니다(손상). GPS 여부가 불확실합니다")
            elif data.startswith(XMP_HEADER):
                info.has_xmp = True
                low = data.lower()
                if b"gpslatitude" in low or b"gpslongitude" in low:
                    info.xmp_gps = True
    except JpegError as exc:
        info.notes.append("JPEG 구조 오류: %s" % exc)
    return info


# ---------------------------------------------------------------- 쓰기
def _orientation_app1(orientation: int) -> bytes:
    """Orientation 한 줄만 든 최소 EXIF APP1 세그먼트(마커 포함)."""
    tiff = b"MM\x00\x2a\x00\x00\x00\x08"                 # 빅엔디안, IFD0 오프셋 8
    tiff += struct.pack(">H", 1)                          # 항목 1개
    tiff += struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0)
    tiff += b"\x00\x00\x00\x00"                           # 다음 IFD 없음
    payload = EXIF_HEADER + tiff
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


def strip_app1(raw: bytes, keep_orientation: bool = True) -> bytes:
    """모든 APP1(EXIF, XMP)을 뺀 JPEG 바이트를 돌려준다. 입력은 바꾸지 않는다.

    ``keep_orientation``이면 회전 값(1이 아닐 때)만 든 최소 APP1을 SOI 바로 뒤에 넣어
    사진이 눕거나 뒤집혀 보이지 않게 한다. GPS·촬영 시각·기기 정보는 들어가지 않는다.
    """
    info = parse_exif(raw)
    if not info.is_jpeg:
        raise JpegError("JPEG만 제거할 수 있습니다")
    if info.notes and any("구조 오류" in n for n in info.notes):
        raise JpegError("JPEG 구조가 손상되어 안전하게 다시 쓸 수 없습니다")
    out = bytearray(b"\xff\xd8")
    if keep_orientation and info.orientation not in (None, 0, 1) and 1 <= (info.orientation or 0) <= 8:
        out += _orientation_app1(info.orientation)
    pos = 2
    for marker, _s, e, m_start in _segments(raw):
        if marker == 0xE1:
            out += raw[pos:m_start]
            pos = e
    out += raw[pos:]
    return bytes(out)
