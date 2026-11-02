"""OS 의존 코드는 전부 여기. 모든 외부 프로세스 호출은 인자 리스트(shell=False)만 쓴다.

주의: 이 모듈에는 프로세스를 죽이는 기능이 일부러 없다 (게임 강제 종료 금지).
"""
import csv
import io
import os
import subprocess
import sys

from .model import norm_proc

SUSPEND_CMD = ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"]
HIBERNATE_CMD = ["shutdown", "/h"]
TASKLIST_CMD = ["tasklist", "/FO", "CSV", "/NH"]


class PlatformUnavailable(RuntimeError):
    pass


def suspend_command(method="suspend"):
    if method == "hibernate":
        return list(HIBERNATE_CMD)
    if method == "suspend":
        return list(SUSPEND_CMD)
    raise ValueError("알 수 없는 sleep_method: %r" % (method,))


def parse_tasklist(raw):
    """`tasklist /FO CSV /NH` 출력(bytes 또는 str) -> 정규화된 이름 집합."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw.decode("cp949", errors="replace")
    names = set()
    for row in csv.reader(io.StringIO(raw)):
        if row and row[0].strip():
            names.add(norm_proc(row[0]))
    return frozenset(names)


def toast_burnttoast_command(title, body):
    """제목/본문은 환경변수로 넘겨 PowerShell 스크립트에 문자열로 끼워넣지 않는다."""
    script = "New-BurntToastNotification -Text $env:NAGGER_TITLE, $env:NAGGER_BODY"
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], \
        {"NAGGER_TITLE": title, "NAGGER_BODY": body}


def toast_msg_command(title, body):
    # msg 는 '/'로 시작하면 옵션으로 오해하므로 접두어를 붙인다.
    return ["msg", "*", "/TIME:15", "[%s] %s" % (title, body)]


class Platform:
    """이벤트 소스 + 부수효과의 단일 창구. 테스트에서는 FakePlatform으로 대체."""

    def idle_seconds(self):
        raise NotImplementedError

    def foreground_process(self):
        raise NotImplementedError

    def running_processes(self):
        raise NotImplementedError

    def suspend(self, method="suspend"):
        raise NotImplementedError

    def toast(self, title, body, method="auto"):
        """성공하면 True. 실패하면 False (호출자가 print 폴백)."""
        raise NotImplementedError


class WindowsPlatform(Platform):
    def __init__(self, runner=subprocess.run):
        if sys.platform != "win32":
            raise PlatformUnavailable("WindowsPlatform은 Windows에서만 동작합니다 (개발 중이면 테스트의 FakePlatform 사용).")
        self._run = runner

    # ---- 입력/창 ----
    def idle_seconds(self):
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            raise OSError("GetLastInputInfo 실패")
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0

    def foreground_process(self):
        import ctypes
        from ctypes import wintypes
        user32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        k32.OpenProcess.restype = wintypes.HANDLE
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return None
            return norm_proc(buf.value)
        finally:
            k32.CloseHandle(h)

    def running_processes(self):
        res = self._run(TASKLIST_CMD, capture_output=True, timeout=20)
        if res.returncode != 0:
            raise OSError("tasklist 실패 (%s)" % res.returncode)
        return parse_tasklist(res.stdout)

    # ---- 출력 ----
    def suspend(self, method="suspend"):
        self._run(suspend_command(method), timeout=30)

    def toast(self, title, body, method="auto"):
        if method in ("auto", "burnttoast"):
            cmd, extra = toast_burnttoast_command(title, body)
            try:
                res = self._run(cmd, capture_output=True, timeout=20, env=dict(os.environ, **extra))
                if res.returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                pass
            if method == "burnttoast":
                return False
        try:
            res = self._run(toast_msg_command(title, body), capture_output=True, timeout=20)
            return res.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False


def get_platform():
    return WindowsPlatform()
