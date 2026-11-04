"""자식 프로세스의 출력을 크기 기준으로 회전하는 로그 파일에 받아 적는 작은 감독자.

두 가지로 쓴다.

* 라이브러리: ``RotatingLog`` (``homelab job`` 이 씀)
* 스크립트: ``python -I logrun.py --log 파일 --max-bytes N --backups K -- 명령 ...``
  (``homelab up`` 이 서버마다 하나씩 띄움. 서버 출력이 파이프로 오고, 파일이 커지면 ``.1``, ``.2`` 로 밀어낸다)

이 파일은 상대 import 없이 표준 라이브러리만 쓴다(스크립트로 실행되므로).
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


class RotatingLog:
    def __init__(self, path: Path | str, max_bytes: int = 2 * 1024 * 1024, backups: int = 3):
        self.path = Path(path)
        self.max_bytes = max(1024, int(max_bytes))
        self.backups = max(0, int(backups))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = None
        self._size = 0
        self._open()

    def _open(self) -> None:
        self._fh = open(self.path, "ab")
        try:
            self._size = self.path.stat().st_size
        except OSError:
            self._size = 0

    def rotate(self) -> bool:
        """현재 파일을 .1 로 밀고 새로 연다. 윈도우에서 다른 프로세스가 열고 있으면 실패해도 계속 쓴다."""
        if self._fh:
            self._fh.close()
            self._fh = None
        ok = True
        try:
            if self.backups == 0:
                os.unlink(self.path)
            else:
                oldest = self.path.with_name("%s.%d" % (self.path.name, self.backups))
                if oldest.exists():
                    os.unlink(oldest)
                for i in range(self.backups - 1, 0, -1):
                    src = self.path.with_name("%s.%d" % (self.path.name, i))
                    if src.exists():
                        os.replace(src, self.path.with_name("%s.%d" % (self.path.name, i + 1)))
                os.replace(self.path, self.path.with_name(self.path.name + ".1"))
        except OSError:
            ok = False
        self._open()
        return ok

    def write(self, data: bytes) -> None:
        if self._fh is None:
            self._open()
        if self._size > 0 and self._size + len(data) > self.max_bytes:
            self.rotate()
        self._fh.write(data)
        self._fh.flush()
        self._size += len(data)

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


def rotate_if_big(path: Path | str, max_bytes: int, backups: int) -> bool:
    """시작하기 전에 이미 큰 로그를 밀어낸다."""
    p = Path(path)
    try:
        if p.exists() and p.stat().st_size > max_bytes:
            log = RotatingLog(p, max_bytes, backups)
            ok = log.rotate()
            log.close()
            return ok
    except OSError:
        pass
    return False


def stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print("사용법: logrun.py --log FILE [--max-bytes N] [--backups K] -- 명령 ...", file=sys.stderr)
        return 2
    i = argv.index("--")
    own, cmd = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--log", required=True)
    ap.add_argument("--max-bytes", type=int, default=2 * 1024 * 1024)
    ap.add_argument("--backups", type=int, default=3)
    args = ap.parse_args(own)
    if not cmd:
        return 2
    log = RotatingLog(args.log, args.max_bytes, args.backups)
    log.write(("[homelab %s] 시작: %s\n" % (stamp(), os.path.basename(cmd[0]) + " " + " ".join(cmd[1:3]))).encode("utf-8"))
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                bufsize=0, shell=False, **kwargs)
    except OSError as e:
        log.write(("[homelab %s] 실행 실패: %s\n" % (stamp(), e)).encode("utf-8", "replace"))
        log.close()
        return 127

    def stop(signum, frame):  # noqa: ARG001
        try:
            proc.terminate()
        except OSError:
            pass

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, stop)
            except (ValueError, OSError):
                pass
    try:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            log.write(chunk)
    except (OSError, ValueError):
        pass
    rc = proc.wait()
    log.write(("[homelab %s] 종료 코드 %s\n" % (stamp(), rc)).encode("utf-8"))
    log.close()
    return rc if rc >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
