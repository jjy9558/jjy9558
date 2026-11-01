"""원본 백업/복원.

게임 폴더에 쓰는 모든 파일은 :class:`BackupStore.write` 를 거친다.
처음 쓰는 파일은 ``<game_dir>/.hpatch_backup/`` 에 원본을 그대로 복사해 두고,
``manifest.json`` 에 원본/패치본 해시를 기록한다. 새로 만든 파일은 복원 시 삭제된다.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

BACKUP_DIRNAME = ".hpatch_backup"
MANIFEST_NAME = "manifest.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".hpatch_tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


class BackupStore:
    def __init__(self, game_dir) -> None:
        self.game_dir = Path(game_dir)
        self.backup_dir = self.game_dir / BACKUP_DIRNAME
        self.manifest_path = self.backup_dir / MANIFEST_NAME
        self.files: Dict[str, dict] = {}
        if self.manifest_path.is_file():
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                self.files = json.load(f).get("files", {})

    # -- 읽기 ---------------------------------------------------------
    def current_path(self, rel: str) -> Path:
        return self.game_dir / rel

    def original_bytes(self, rel: str) -> Optional[bytes]:
        """패치되기 전의 원본 내용. 없던 파일이면 None.

        현재 파일이 우리가 쓴 패치본이면 백업에서, 그렇지 않으면(게임 업데이트로
        덮어쓰였거나 아직 안 건드렸으면) 현재 파일에서 읽는다.
        """
        entry = self.files.get(rel)
        cur = self.current_path(rel)
        if entry and cur.is_file() and sha256_file(cur) == entry.get("patched_sha256"):
            if entry.get("existed"):
                return (self.backup_dir / "files" / rel).read_bytes()
            return None
        if cur.is_file():
            return cur.read_bytes()
        if entry and entry.get("existed") and (self.backup_dir / "files" / rel).is_file():
            return (self.backup_dir / "files" / rel).read_bytes()
        return None

    # -- 쓰기 ---------------------------------------------------------
    def write(self, rel: str, data: bytes) -> None:
        cur = self.current_path(rel)
        cur_sha = sha256_file(cur)
        entry = self.files.get(rel)
        if entry and cur_sha is not None and cur_sha in (entry.get("patched_sha256"), entry.get("orig_sha256")):
            pass  # 백업이 여전히 유효한 원본이다
        else:
            if cur_sha is not None:
                dst = self.backup_dir / "files" / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cur, dst)
                entry = {"existed": True, "orig_sha256": cur_sha}
            else:
                entry = {"existed": False, "orig_sha256": None}
        entry["patched_sha256"] = sha256_bytes(data)
        self.files[rel] = entry
        self._save_manifest()  # 쓰기 전에 매니페스트부터 (중간에 죽어도 복원 가능)
        _atomic_write(cur, data)

    def _save_manifest(self) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "files": self.files}, ensure_ascii=False, indent=2)
        _atomic_write(self.manifest_path, payload.encode("utf-8"))

    # -- 복원 ---------------------------------------------------------
    def restore(self, force: bool = False) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {"restored": [], "removed": [], "skipped": [], "already": []}
        for rel, entry in list(self.files.items()):
            cur = self.current_path(rel)
            cur_sha = sha256_file(cur)
            if cur_sha is not None and cur_sha == entry.get("orig_sha256") and entry.get("existed"):
                result["already"].append(rel)
                self.files.pop(rel)
                continue
            if cur_sha is not None and cur_sha != entry.get("patched_sha256") and not force:
                result["skipped"].append(rel)  # 패치 이후 사용자가/업데이트가 바꾼 파일
                continue
            if entry.get("existed"):
                src = self.backup_dir / "files" / rel
                if not src.is_file():
                    result["skipped"].append(rel)
                    continue
                cur.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, cur)
                result["restored"].append(rel)
            else:
                if cur.is_file():
                    cur.unlink()
                    self._prune_empty_dirs(cur.parent)
                result["removed"].append(rel)
            self.files.pop(rel)
        if self.files:
            self._save_manifest()
        else:
            shutil.rmtree(self.backup_dir, ignore_errors=True)
        return result

    def _prune_empty_dirs(self, d: Path) -> None:
        while d != self.game_dir and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent
