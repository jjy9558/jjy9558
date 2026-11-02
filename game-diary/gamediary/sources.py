"""스크린샷 폴더 스캔, 파일명 타임스탬프 파싱, PNG/JPEG 헤더 크기 읽기."""
import json
import os
import re
import struct
from datetime import datetime

from .config import expand

IMG_EXT = (".jpg", ".jpeg", ".png")

_STEAM_RE = re.compile(r"^(\d{14})(?:_\d+)?$")
_GENERIC_RE = re.compile(
    r"(\d{4})[-.](\d{2})[-.](\d{2})(?:\s*-\s*|[ _T])(\d{2})[-.:](\d{2})[-.:](\d{2})")


def parse_timestamp(filename):
    """파일명에서 datetime 추출. 실패하면 None."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    try:
        m = _STEAM_RE.match(stem)
        if m:
            return datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
        m = _GENERIC_RE.search(stem)
        if m:
            return datetime(*[int(x) for x in m.groups()])
    except ValueError:
        return None
    return None


def game_from_filename(filename):
    """'Halo Infinite 2026-11-01 21-30-05.png' -> 'Halo Infinite' (없으면 None)."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    m = _GENERIC_RE.search(stem)
    if not m:
        return None
    name = stem[:m.start()].strip(" -_.")
    return name or None


def image_size(path):
    """PNG/JPEG 헤더만 읽어 (w, h) 반환. 알 수 없으면 None."""
    try:
        with open(path, "rb") as f:
            head = f.read(26)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
                return struct.unpack(">II", head[16:24])
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    b = f.read(1)
                    if not b:
                        return None
                    if b != b"\xff":
                        continue
                    m = f.read(1)
                    while m == b"\xff":
                        m = f.read(1)
                    if not m:
                        return None
                    c = m[0]
                    if c in (0x01, 0xD8) or 0xD0 <= c <= 0xD7:
                        continue
                    seg = f.read(2)
                    if len(seg) < 2:
                        return None
                    ln = struct.unpack(">H", seg)[0]
                    if 0xC0 <= c <= 0xCF and c not in (0xC4, 0xC8, 0xCC):
                        data = f.read(5)
                        if len(data) < 5:
                            return None
                        h, w = struct.unpack(">HH", data[1:5])
                        return (w, h)
                    f.seek(ln - 2, 1)
    except OSError:
        return None
    return None


def mime_for(path):
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        head = b""
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:2] == b"\xff\xd8":
        return "image/jpeg"
    return "image/png" if path.lower().endswith(".png") else "image/jpeg"


def _collect_names(obj, out):
    if isinstance(obj, dict):
        name = obj.get("name")
        if isinstance(name, str):
            for k in ("appid", "app_id", "steam_appid", "id"):
                v = obj.get(k)
                if v is not None and str(v).isdigit():
                    out.setdefault(str(v), name)
                    break
        for k, v in obj.items():
            if str(k).isdigit() and isinstance(v, dict) and isinstance(v.get("name"), str):
                out.setdefault(str(k), v["name"])
            elif str(k).isdigit() and isinstance(v, str):
                out.setdefault(str(k), v)
            _collect_names(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_names(v, out)


def _load_json(path):
    try:
        with open(expand(path), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load_names(cfg):
    """appid -> 게임 이름. 우선순위: 설정 > 로컬 캐시 JSON > steam-buddy."""
    names = {}
    if cfg.get("use_steam_buddy", True):
        data = _load_json(os.path.join("~", ".steam-buddy", "data.json"))
        if data is not None:
            _collect_names(data, names)
    cache = _load_json(cfg.get("names_cache") or "")
    if isinstance(cache, dict):
        for k, v in cache.items():
            if isinstance(v, str):
                names[str(k)] = v
    for k, v in (cfg.get("game_names") or {}).items():
        names[str(k)] = v
    return names


def _find_thumb(path, kind, appid, thumbs_dir):
    name = os.path.basename(path)
    cands = [os.path.join(os.path.dirname(path), "thumbnails", name)]
    if thumbs_dir:
        td = expand(thumbs_dir)
        cands += [os.path.join(td, name)]
        if appid:
            cands += [os.path.join(td, appid, name),
                      os.path.join(td, appid, "screenshots", "thumbnails", name)]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def _is_image(n):
    return n.lower().endswith(IMG_EXT)


def scan_sources(cfg, names=None):
    """설정된 모든 소스를 훑어 shot dict 를 생성."""
    names = names if names is not None else load_names(cfg)
    thumbs_dir = cfg.get("thumbs_dir", "")
    seen = set()
    for src in cfg.get("sources", []):
        root = expand(src["path"])
        kind = src.get("kind", "folder")
        if not os.path.isdir(root):
            yield {"_warn": "폴더를 찾을 수 없음: " + root}
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() != "thumbnails"]
            base = os.path.basename(dirpath)
            appid = None
            if kind == "steam":
                if base.lower() != "screenshots":
                    continue
                parent = os.path.basename(os.path.dirname(dirpath))
                appid = parent if parent.isdigit() else None
            for fn in sorted(filenames):
                if not _is_image(fn):
                    continue
                p = os.path.join(dirpath, fn)
                if p in seen:
                    continue
                seen.add(p)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                ts = parse_timestamp(fn) or datetime.fromtimestamp(st.st_mtime).replace(microsecond=0)
                if kind == "steam":
                    game = names.get(appid) if appid else None
                    game = game or ("Steam 앱 " + appid if appid else "Steam")
                else:
                    game = game_from_filename(fn) or base or "기타"
                size = image_size(p) or (None, None)
                yield {"path": p, "source": root, "kind": kind, "game": game,
                       "appid": appid, "ts": ts, "bytes": st.st_size, "mtime": st.st_mtime,
                       "width": size[0], "height": size[1],
                       "thumb": _find_thumb(p, kind, appid, thumbs_dir)}
