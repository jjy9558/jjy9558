"""수집 파이프라인: 개인정보 시간 → 중복 → 저장 → 설명(VLM) → 색인. 보관 기간 정리."""
import hashlib
import os
import re
import threading
import time

from . import config as cfgmod
from .store import Store
from .vlm import VlmClient, VlmError

JPEG_SOI, JPEG_EOI = b"\xff\xd8", b"\xff\xd9"
IMG_EXT = (".jpg", ".jpeg")


class FrameError(ValueError):
    pass


def is_jpeg(data):
    return len(data) >= 4 and data[:2] == JPEG_SOI and data.rstrip(b"\x00")[-2:] == JPEG_EOI


def parse_hash(h):
    """16자리 hex(64비트) -> int, 잘못되면 None."""
    if isinstance(h, str) and re.fullmatch(r"[0-9a-fA-F]{16}", h):
        return int(h, 16)
    return None


def hamming(a, b):
    return bin(a ^ b).count("1")


def clean_camera(label):
    label = re.sub(r"[\x00-\x1f\x7f]", "", label or "").strip()
    return label[:40]


_FN_TS = re.compile(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[-_ T.]?(\d{2})[-_.:]?(\d{2})[-_.:]?(\d{2})")


def ts_from_name(name, fallback):
    m = _FN_TS.search(name)
    if m:
        try:
            y, mo, d, h, mi, s = (int(x) for x in m.groups())
            return time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
        except (ValueError, OverflowError):
            pass
    return fallback


class App:
    def __init__(self, cfg, clock=time.time, vlm=None, store=None):
        self.cfg = cfg
        self.clock = clock
        self.vlm = vlm or VlmClient(cfg["base_url"], cfg["model"], cfg["api_key"], cfg["vlm_timeout"])
        self.photo_dir = os.path.join(cfg["data_dir"], "photos")
        os.makedirs(self.photo_dir, exist_ok=True)
        self.store = store or Store(os.path.join(cfg["data_dir"], "objmem.db"), use_fts=cfg.get("use_fts", True))
        self.vlm_lock = threading.Lock()
        cfgmod.parse_ranges(cfg.get("privacy_hours"))   # 형식 오류를 시작 시점에 드러냄

    # ------------------------------------------------------------ 개인정보 시간
    def in_privacy(self, ts):
        lt = time.localtime(ts)
        return cfgmod.in_privacy(self.cfg.get("privacy_hours"), lt.tm_hour * 60 + lt.tm_min)

    # ------------------------------------------------------------ 수집
    def ingest_bytes(self, data, camera="", phash=None, ts=None, describe=True):
        """결과: {"status": stored|duplicate|similar|privacy, ...}. 형식 오류는 FrameError."""
        if not is_jpeg(data):
            raise FrameError("JPEG 가 아닙니다")
        if len(data) > self.cfg["max_frame_bytes"]:
            raise FrameError("프레임이 너무 큽니다")
        ts = self.clock() if ts is None else ts
        if self.in_privacy(ts):
            return {"status": "privacy"}          # 저장도, VLM 전송도 하지 않음
        camera = clean_camera(camera)
        sha = hashlib.sha256(data).hexdigest()
        if self.store.frame_by_sha(sha):
            return {"status": "duplicate", "reason": "sha256"}
        h = parse_hash(phash) if phash else None
        if h is not None:
            prev = parse_hash(self.store.last_phash(camera))
            if prev is not None and hamming(h, prev) <= self.cfg["hash_threshold"]:
                return {"status": "similar", "distance": hamming(h, prev)}
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        d = os.path.join(self.photo_dir, day)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "%d_%s.jpg" % (int(ts), sha[:10]))
        with open(path, "wb") as f:
            f.write(data)
        fid = self.store.add_frame(sha, camera, ts, path, "%016x" % h if h is not None else None, len(data))
        out = {"status": "stored", "frame_id": fid, "path": path, "described": False}
        if describe:
            out.update(self.describe_frame(fid))
        return out

    def describe_frame(self, fid):
        f = self.store.frame(fid)
        if f is None or not f["path"]:
            return {"described": False, "error": "no photo"}
        desc = self.store.cache_get(f["sha256"])
        cached = desc is not None
        if desc is None:
            try:
                with open(f["path"], "rb") as fh:
                    data = fh.read()
                with self.vlm_lock:
                    desc = self.vlm.describe(data)
            except (VlmError, OSError) as e:
                return {"described": False, "error": str(e)}
            self.store.cache_put(f["sha256"], desc, f["ts"])
        n = self.store.set_description(fid, desc)
        return {"described": True, "objects": n, "cached": cached}

    def describe_pending(self, limit=100):
        done = failed = 0
        for f in self.store.pending_frames(limit):
            if self.describe_frame(f["id"]).get("described"):
                done += 1
            else:
                failed += 1
        return {"described": done, "failed": failed}

    def ingest_folder(self, folder, camera="", recursive=True, describe=True):
        res = {"stored": 0, "duplicate": 0, "similar": 0, "privacy": 0, "skipped": 0, "described": 0}
        files = []
        for root, dirs, names in os.walk(folder):
            files += [os.path.join(root, n) for n in names if n.lower().endswith(IMG_EXT)]
            if not recursive:
                break
        for p in sorted(files):
            try:
                with open(p, "rb") as fh:
                    data = fh.read(self.cfg["max_frame_bytes"] + 1)
                ts = ts_from_name(os.path.basename(p), os.path.getmtime(p))
                r = self.ingest_bytes(data, camera, None, ts, describe=describe)
            except (FrameError, OSError):
                res["skipped"] += 1
                continue
            res[r["status"]] += 1
            if r.get("described"):
                res["described"] += 1
        return res

    # ------------------------------------------------------------ 보관 기간
    def purge(self):
        now = self.clock()
        pc = now - self.cfg["photo_days"] * 86400
        dc = now - self.cfg["desc_days"] * 86400
        photos = 0
        for r in self.store.photos_older_than(pc):
            _rm(r["path"])
            self.store.clear_photo(r["id"])
            photos += 1
        n, paths = self.store.delete_descriptions_older_than(dc)
        for p in paths:
            _rm(p)
        _rmdirs(self.photo_dir)
        return {"photos_deleted": photos, "descriptions_deleted": n}


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _rmdirs(root):
    try:
        for name in os.listdir(root):
            d = os.path.join(root, name)
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
    except OSError:
        pass
