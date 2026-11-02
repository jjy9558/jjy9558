import struct
import zlib
from pathlib import Path


def png_bytes(seed: int = 0, size: int = 8) -> bytes:
    """런타임에 만드는 작은 단색 PNG."""
    def chunk(tag, body):
        c = struct.pack(">I", len(body)) + tag + body
        return c + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)
    row = b"\x00" + bytes([(seed * 40) % 256, 90, 160]) * size
    raw = zlib.compress(row * size)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def write_pngs(folder: Path, n: int = 3) -> list[Path]:
    out = []
    for i in range(n):
        p = Path(folder) / ("thumb%d.png" % (i + 1))
        p.write_bytes(png_bytes(i))
        out.append(p)
    return out


FIX = Path(__file__).resolve().parent / "fixtures"
