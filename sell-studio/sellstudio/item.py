"""물품 폴더(item.json + photos/)의 읽기/쓰기. 원본 사진은 건드리지 않고 사본을 둔다."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from . import jpegmeta

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
UNSUPPORTED_EXT = {".heic", ".heif", ".bmp", ".tif", ".tiff"}
ITEM_FILE = "item.json"
SCHEMA = 1


class ItemError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_item_dir(path: Path) -> bool:
    return (Path(path) / ITEM_FILE).is_file()


def load(path: str | os.PathLike) -> tuple[Path, dict]:
    root = Path(path)
    if root.is_file() and root.name == ITEM_FILE:
        root = root.parent
    f = root / ITEM_FILE
    if not f.is_file():
        raise ItemError("물품 폴더가 아닙니다 (item.json 없음): %s  -> 먼저 `new` 로 만드세요" % root)
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ItemError("item.json 을 읽지 못했습니다: %s" % e) from e
    if not isinstance(data, dict):
        raise ItemError("item.json 형식이 이상합니다")
    data.setdefault("photos", [])
    return root, data


def save(root: Path, data: dict) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    data["schema"] = SCHEMA
    fd, tmp = tempfile.mkstemp(dir=str(root), prefix=".item.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, root / ITEM_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def slugify(text: str, default: str = "item") -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", text or "").strip("-._")
    return (s[:40] or default)


def collect_images(paths: list[str]) -> tuple[list[Path], list[str]]:
    """파일/폴더 인자에서 이미지 목록과 경고를 모은다 (폴더는 한 단계만, 이름순)."""
    files: list[Path] = []
    warns: list[str] = []
    for a in paths:
        p = Path(a)
        if p.is_dir():
            for c in sorted(p.iterdir(), key=lambda x: x.name.lower()):
                if c.is_file() and c.suffix.lower() in IMAGE_EXT:
                    files.append(c)
                elif c.is_file() and c.suffix.lower() in UNSUPPORTED_EXT:
                    warns.append("지원하지 않는 형식이라 건너뜀: %s (JPEG로 변환해서 쓰세요)" % c.name)
        elif p.is_file():
            if p.suffix.lower() in IMAGE_EXT:
                files.append(p)
            elif p.suffix.lower() in UNSUPPORTED_EXT:
                warns.append("지원하지 않는 형식이라 건너뜀: %s (JPEG로 변환해서 쓰세요)" % p.name)
            else:
                warns.append("이미지가 아니라 건너뜀: %s" % p.name)
        else:
            raise ItemError("파일/폴더를 찾을 수 없습니다: %s" % a)
    return files, warns


def create(out: Path, photos: list[Path], fields: dict, force: bool = False) -> dict:
    out = Path(out)
    if is_item_dir(out) and not force:
        raise ItemError("이미 물품 폴더입니다: %s (덮어쓰려면 --force, 기존 사진 사본은 지우지 않음)" % out)
    (out / "photos").mkdir(parents=True, exist_ok=True)
    entries = []
    for i, src in enumerate(photos, 1):
        ext = src.suffix.lower().replace(".jpeg", ".jpg")
        name = "photo%02d%s" % (i, ext)
        dst = out / "photos" / name
        shutil.copyfile(src, dst)  # 원본은 읽기만 한다
        raw = dst.read_bytes()
        size = jpegmeta.image_size(raw)
        entries.append({
            "file": name,
            "original_name": src.name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "format": jpegmeta.sniff_format(raw),
            "width": size[0] if size else None,
            "height": size[1] if size else None,
            "caption": "",
            "caption_source": "",
        })
    data = {"schema": SCHEMA, "photos": entries}
    data.update({k: v for k, v in fields.items() if v not in (None, "", [])})
    save(out, data)
    return data
