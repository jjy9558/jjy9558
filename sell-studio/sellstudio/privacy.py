"""사진 개인정보 점검: JPEG EXIF GPS, 파일명/캡션의 송장·주소·영수증 단서. 제거본은 사본에만 쓴다.

한계: 사진 속 글자(택배 송장, 주소)는 픽셀을 읽어야 알 수 있다. VLM 캡션이 있으면 거기서 단서를 찾고,
없으면 눈으로 확인해야 한다.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import item as itemmod
from . import jpegmeta
from . import lintcheck

NAME_CLUES = re.compile(r"송장|운송장|택배|배송|invoice|label|waybill|영수증|receipt|주소|address|신분증|면허|통장|카드|card|"
                        r"주민|여권|passport|전화|연락처", re.I)


@dataclass
class PhotoReport:
    path: str
    label: str = ""
    fmt: str = ""
    gps: bool = False
    lat: float | None = None
    lon: float | None = None
    xmp_gps: bool = False
    camera: str = ""
    taken: str = ""
    serial: bool = False
    clues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def warn(self) -> bool:
        return self.gps or bool(self.clues)


def inspect_file(path: Path, label: str = "", original_name: str = "", captions: dict | None = None) -> PhotoReport:
    raw = Path(path).read_bytes()
    info = jpegmeta.parse_exif(raw)
    r = PhotoReport(path=str(path), label=label or Path(path).name, fmt=jpegmeta.sniff_format(raw))
    r.gps = info.has_gps
    r.lat, r.lon, r.xmp_gps = info.lat, info.lon, info.xmp_gps
    r.camera = " ".join(x for x in (info.make, info.model) if x)
    r.taken = info.datetime
    r.serial = bool(info.serial)
    r.notes = list(info.notes)
    for src, text in (("파일명", original_name or Path(path).name),):
        if NAME_CLUES.search(text):
            r.clues.append("%s에 개인정보 단서 낱말: '%s'" % (src, text))
    if captions:
        blob = " ".join([captions.get("caption", "")] + list(captions.get("visible_text") or []))
        if NAME_CLUES.search(blob):
            r.clues.append("캡션에 개인정보 단서 낱말이 있습니다")
        for flag in captions.get("privacy_flags") or []:
            r.clues.append("VLM 표시: %s" % flag)
        hits = lintcheck.lint_text(blob, {"pii_rules": ["phone", "account", "email", "rrn", "card"]})
        if hits:
            r.clues.append("캡션/보이는 글자에 전화번호·계좌 등으로 보이는 값이 있습니다")
    return r


def inspect_item(root: Path, data: dict) -> list[PhotoReport]:
    reports = []
    for ph in data.get("photos", []):
        p = Path(root) / "photos" / ph["file"]
        if p.is_file():
            reports.append(inspect_file(p, ph["file"], ph.get("original_name", ""), ph))
    return reports


def inspect_paths(paths: list[str]) -> list[PhotoReport]:
    files, _warns = itemmod.collect_images(paths)
    return [inspect_file(f) for f in files]


def render_reports(reports: list[PhotoReport]) -> list[str]:
    if not reports:
        return ["점검할 사진이 없습니다"]
    out = []
    n_gps = sum(r.gps for r in reports)
    for r in reports:
        status = "경고" if r.warn else "이상 없음"
        line = "- %s [%s] %s" % (r.label, r.fmt, status)
        out.append(line)
        if r.gps:
            where = ("위치 %.5f, %.5f" % (r.lat, r.lon)) if r.lat is not None else "GPS 항목 있음(좌표는 못 읽음)"
            out.append("    GPS: %s%s  -> 올리면 촬영 장소가 노출됩니다" % (where, " (XMP)" if r.xmp_gps and r.lat is None else ""))
        if r.camera or r.taken:
            out.append("    기기/시각 정보: %s %s" % (r.camera, r.taken))
        if r.serial:
            out.append("    기기 일련번호가 EXIF에 있습니다")
        out += ["    단서: " + c for c in r.clues]
        out += ["    참고: " + n for n in r.notes]
    out.append("")
    out.append("요약: %d장 중 GPS 포함 %d장, 단서 있는 사진 %d장" % (len(reports), n_gps, sum(bool(r.clues) for r in reports)))
    if n_gps:
        out.append("  -> `--strip` 으로 위치 정보를 뺀 사본을 만들 수 있습니다 (원본은 바뀌지 않습니다)")
    out.append("주의: 사진 속에 찍힌 송장·주소·이름은 이 점검으로 알 수 없습니다. 올리기 전에 눈으로 확인하고 가리세요.")
    return out


def _atomic_write(dst: Path, payload: bytes) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(dst.parent), prefix=".strip.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def strip_copies(sources: list[Path], outdir: Path, keep_orientation: bool = True) -> list[dict]:
    """sources 를 읽어 outdir 에 같은 이름으로 사본을 쓴다. 원본에는 쓰지 않는다."""
    outdir = Path(outdir)
    results = []
    for src in sources:
        src = Path(src)
        dst = outdir / src.name
        if dst.resolve() == src.resolve():
            raise itemmod.ItemError("출력 위치가 원본과 같습니다: %s (다른 폴더를 --out 으로 지정하세요)" % src)
        raw = src.read_bytes()
        res = {"src": str(src), "dst": str(dst), "stripped": False, "removed": 0, "note": ""}
        if jpegmeta.sniff_format(raw) == "jpeg":
            try:
                clean = jpegmeta.strip_app1(raw, keep_orientation=keep_orientation)
            except jpegmeta.JpegError as e:
                res["note"] = "건너뜀: %s" % e
                results.append(res)
                continue
            if jpegmeta.parse_exif(clean).has_gps:
                raise itemmod.ItemError("제거 검증에 실패했습니다: %s" % src.name)
            _atomic_write(dst, clean)
            res.update(stripped=True, removed=len(raw) - len(clean))
        else:
            _atomic_write(dst, raw)
            res["note"] = "JPEG가 아니라 메타데이터를 건드리지 않고 복사만 했습니다"
        results.append(res)
    return results
