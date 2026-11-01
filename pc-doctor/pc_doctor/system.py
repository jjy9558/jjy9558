"""Windows와 맞닿는 유일한 모듈.

pc-doctor의 다른 모든 모듈은 여기 정의된 `SystemBase` 인터페이스만 사용합니다.
테스트에서는 `tests/fakes.py`의 FakeSystem으로 통째로 바꿔 끼웁니다.

원칙
- 외부 명령은 항상 *인자 리스트*로 실행합니다 (셸을 거치지 않음).
- PowerShell 스크립트는 전부 아래 `PS_SCRIPTS`의 상수 문자열입니다.
  사용자 입력(프로세스 이름 등)을 스크립트 문자열에 끼워 넣지 않습니다.
  필터링은 Python 쪽에서 합니다.
- 레지스트리는 표준 라이브러리 `winreg`로 직접 읽고 씁니다
  (`reg.exe` 출력의 언어별 차이를 파싱하지 않아도 되고, 값 형식이 정확히 보존됨).
- 서비스는 `sc.exe`, 전원 옵션은 `powercfg.exe`, GPU는 `nvidia-smi`를 씁니다.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 데이터 타입
# ---------------------------------------------------------------------------

REG_TYPES = ("REG_SZ", "REG_EXPAND_SZ", "REG_DWORD", "REG_QWORD", "REG_BINARY", "REG_MULTI_SZ")


@dataclass
class RegValue:
    """레지스트리 값. DWORD/QWORD는 int, BINARY는 hex 문자열, MULTI_SZ는 리스트."""

    type: str
    data: object

    def to_json(self):
        return {"type": self.type, "data": self.data}

    @classmethod
    def from_json(cls, d):
        return cls(d["type"], d["data"])


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self):
        return self.returncode == 0


HIVE_ALIASES = {
    "HKCU": "HKCU",
    "HKEY_CURRENT_USER": "HKCU",
    "HKLM": "HKLM",
    "HKEY_LOCAL_MACHINE": "HKLM",
}


def split_hive(path):
    """'HKCU\\Software\\X' -> ('HKCU', 'Software\\X')."""
    head, _, rest = path.partition("\\")
    hive = HIVE_ALIASES.get(head.upper())
    if hive is None:
        raise ValueError("지원하지 않는 레지스트리 하이브: %r (HKCU/HKLM만 허용)" % head)
    return hive, rest


def needs_admin(path):
    return split_hive(path)[0] == "HKLM"


SAFE_PROCESS_RE = re.compile(r"^[\w .\-()+]+\.exe$", re.IGNORECASE)
SAFE_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

# ---------------------------------------------------------------------------
# PowerShell 상수 스크립트
# ---------------------------------------------------------------------------

PS_PREFIX = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    "$ErrorActionPreference = 'SilentlyContinue'; $ProgressPreference = 'SilentlyContinue'; "
)

PS_SCRIPTS = {
    # 스냅샷용: 프로세스 1번만 띄우도록 한 번에 모읍니다. 각 항목 실패는 null로 남습니다.
    "snapshot": r"""
$r = [ordered]@{}
try { $r.cpu = @(Get-CimInstance Win32_Processor | Select-Object Name,Manufacturer,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed) } catch {}
try { $r.computer = Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,TotalPhysicalMemory,AutomaticManagedPagefile } catch {}
try { $r.board = Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product } catch {}
try { $r.bios = Get-CimInstance Win32_BIOS | Select-Object SMBIOSBIOSVersion,@{n='ReleaseDate';e={if($_.ReleaseDate){$_.ReleaseDate.ToString('yyyy-MM-dd')}}} } catch {}
try { $r.memory = @(Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity,Speed,ConfiguredClockSpeed,BankLabel,DeviceLocator,Manufacturer,PartNumber) } catch {}
try { $r.memory_slots = (Get-CimInstance Win32_PhysicalMemoryArray | Measure-Object -Property MemoryDevices -Sum).Sum } catch {}
try { $r.os = Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,TotalVisibleMemorySize,FreePhysicalMemory } catch {}
try { $cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'; $r.os_extra = [ordered]@{DisplayVersion=$cv.DisplayVersion; UBR=$cv.UBR} } catch {}
try { $r.gpu = @(Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,@{n='DriverDate';e={if($_.DriverDate){$_.DriverDate.ToString('yyyy-MM-dd')}}},AdapterRAM) } catch {}
try { $r.physical_disks = @(Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,@{n='MediaType';e={[string]$_.MediaType}},@{n='BusType';e={[string]$_.BusType}},Size,@{n='HealthStatus';e={[string]$_.HealthStatus}}) } catch {}
try { $r.volumes = @(Get-Volume | Where-Object { $_.DriveLetter } | ForEach-Object { $dn = $null; try { $dn = (Get-Partition -DriveLetter $_.DriveLetter -ErrorAction Stop).DiskNumber } catch {}; [ordered]@{DriveLetter=[string]$_.DriveLetter; Label=$_.FileSystemLabel; FileSystem=$_.FileSystem; Size=$_.Size; SizeRemaining=$_.SizeRemaining; DiskNumber=$dn} }) } catch {}
try { $dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard; if ($dg) { $r.device_guard = [ordered]@{VirtualizationBasedSecurityStatus=$dg.VirtualizationBasedSecurityStatus; SecurityServicesRunning=@($dg.SecurityServicesRunning)} } } catch {}
try { $r.game_mode = (Get-ItemProperty 'HKCU:\Software\Microsoft\GameBar' -Name AutoGameModeEnabled -ErrorAction Stop).AutoGameModeEnabled } catch { $r.game_mode = $null }
try { $r.hags = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' -Name HwSchMode -ErrorAction Stop).HwSchMode } catch { $r.hags = $null }
try { $r.startup = @(Get-CimInstance Win32_StartupCommand | Select-Object Name,Location) } catch {}
try { $r.pagefile = @(Get-CimInstance Win32_PageFileUsage | Select-Object Name,AllocatedBaseSize,CurrentUsage) } catch {}
try { $r.top_memory = @(Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 10 @{n='Name';e={$_.ProcessName}},Id,@{n='WorkingSetMB';e={[math]::Round($_.WorkingSet64/1MB)}}) } catch {}
try { $r.top_cpu = @(Get-Process | Where-Object { $_.CPU } | Sort-Object CPU -Descending | Select-Object -First 10 @{n='Name';e={$_.ProcessName}},Id,@{n='CPUSeconds';e={[math]::Round($_.CPU,1)}}) } catch {}
$r | ConvertTo-Json -Depth 5 -Compress
""",
    "hotfixes": r"""
ConvertTo-Json -Compress -Depth 3 -InputObject @(Get-HotFix | ForEach-Object { [ordered]@{HotFixID=$_.HotFixID; Description=$_.Description; InstalledOn=$(if($_.InstalledOn){$_.InstalledOn.ToString('yyyy-MM-dd')})} })
""",
    # Windows Update 기록 (성공/실패 포함). 관리자 권한 없이 동작하는 COM API.
    "update_history": r"""
$s = New-Object -ComObject Microsoft.Update.Session
$q = $s.CreateUpdateSearcher()
$n = $q.GetTotalHistoryCount()
$h = @(); if ($n -gt 0) { $h = $q.QueryHistory(0, [math]::Min($n, 60)) }
ConvertTo-Json -Compress -Depth 3 -InputObject @($h | Where-Object { $_.Title } | ForEach-Object { [ordered]@{Title=$_.Title; Date=$_.Date.ToString('yyyy-MM-dd'); ResultCode=[int]$_.ResultCode} })
""",
    # 설치 대기 중인 업데이트 검색. 네트워크/정책에 따라 실패할 수 있음 → 감지 불가로 처리.
    "pending_updates": r"""
$s = New-Object -ComObject Microsoft.Update.Session
$q = $s.CreateUpdateSearcher()
$res = $q.Search("IsInstalled=0 and IsHidden=0 and Type='Software'")
ConvertTo-Json -Compress -Depth 4 -InputObject @($res.Updates | ForEach-Object { [ordered]@{Title=$_.Title; KB=@($_.KBArticleIDs | ForEach-Object { 'KB' + $_ })} })
""",
    # 게임 모드에서 종료한 프로그램을 다시 띄우기 위한 실행 경로 목록.
    "processes": r"""
ConvertTo-Json -Compress -Depth 3 -InputObject @(Get-Process | ForEach-Object { [ordered]@{Name=$_.ProcessName; Id=$_.Id; Path=$_.Path} })
""",
}


def parse_ps_json(raw):
    """PowerShell ConvertTo-Json 출력 → Python 객체. 비어 있거나 깨졌으면 None."""
    if raw is None:
        return None
    raw = raw.strip().lstrip("\ufeff")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 인터페이스
# ---------------------------------------------------------------------------


class SystemBase:
    """pc-doctor가 OS에 요구하는 모든 동작. 테스트는 이것을 FakeSystem으로 대체."""

    # --- 조회 ---
    def ps_raw(self, key):  # -> Optional[str]
        raise NotImplementedError

    def ps_json(self, key):
        return parse_ps_json(self.ps_raw(key))

    def powercfg_active_raw(self):  # -> Optional[str]
        raise NotImplementedError

    def nvidia_smi_raw(self):  # -> Optional[str]
        raise NotImplementedError

    def tasklist_raw(self):  # -> Optional[str]  (tasklist /FO CSV /NH)
        raise NotImplementedError

    def service_query_raw(self, name):  # -> Optional[str]  (sc query NAME)
        raise NotImplementedError

    def is_admin(self):
        raise NotImplementedError

    # --- 레지스트리 ---
    def reg_get(self, path, name):  # -> Optional[RegValue]
        raise NotImplementedError

    def reg_set(self, path, name, value):
        raise NotImplementedError

    def reg_delete_value(self, path, name):
        raise NotImplementedError

    def reg_key_exists(self, path):
        raise NotImplementedError

    def reg_delete_key_tree(self, path):
        raise NotImplementedError

    # --- 상태 변경 (게임 모드) ---
    def powercfg_setactive(self, guid):  # -> bool
        raise NotImplementedError

    def kill_process(self, image_name):  # -> bool
        raise NotImplementedError

    def launch(self, exe_path):  # -> bool
        raise NotImplementedError

    def service_stop(self, name):  # -> bool
        raise NotImplementedError

    def service_start(self, name):  # -> bool
        raise NotImplementedError

    def restart_explorer(self):  # -> bool
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 실제 Windows 구현
# ---------------------------------------------------------------------------


def _decode(data):
    if data is None:
        return ""
    for enc in ("utf-8", "oem", "mbcs", "cp949"):
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


class WindowsSystem(SystemBase):
    POWERSHELL = "powershell.exe"

    def __init__(self, timeout=120):
        self.timeout = timeout

    # 모든 외부 명령은 여기만 지나갑니다.
    def run(self, args, timeout=None):
        if not isinstance(args, (list, tuple)) or not all(isinstance(a, str) for a in args):
            raise TypeError("명령은 문자열 리스트여야 합니다")
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            p = subprocess.run(
                list(args),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                timeout=timeout or self.timeout,
                **kwargs
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            return Result(127, "", str(e))
        return Result(p.returncode, _decode(p.stdout), _decode(p.stderr))

    def _run_out(self, args, timeout=None):
        r = self.run(args, timeout)
        return r.stdout if r.ok else None

    def ps_raw(self, key):
        script = PS_SCRIPTS[key]  # KeyError = 프로그래밍 오류
        return self._run_out(
            [self.POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", PS_PREFIX + script],
            timeout=300 if key == "pending_updates" else None,
        )

    def powercfg_active_raw(self):
        return self._run_out(["powercfg", "/getactivescheme"])

    def powercfg_setactive(self, guid):
        return self.run(["powercfg", "/setactive", guid]).ok

    def nvidia_smi_raw(self):
        exe = shutil.which("nvidia-smi")
        if not exe:
            return None
        return self._run_out([exe, "--query-gpu=" + ",".join(NVIDIA_FIELDS), "--format=csv,noheader,nounits"])

    def tasklist_raw(self):
        return self._run_out(["tasklist", "/FO", "CSV", "/NH"])

    def kill_process(self, image_name):
        if not SAFE_PROCESS_RE.match(image_name):
            return False
        # /F 없이: 창에 정상 종료 요청 (저장 안 된 작업 보호)
        return self.run(["taskkill", "/IM", image_name]).ok

    def launch(self, exe_path):
        if not exe_path or not os.path.isfile(exe_path):
            return False
        try:
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen([exe_path], creationflags=flags, close_fds=True)
            return True
        except OSError:
            return False

    def service_query_raw(self, name):
        if not SAFE_SERVICE_RE.match(name):
            return None
        return self._run_out(["sc", "query", name])

    def service_stop(self, name):
        return SAFE_SERVICE_RE.match(name) is not None and self.run(["sc", "stop", name]).ok

    def service_start(self, name):
        return SAFE_SERVICE_RE.match(name) is not None and self.run(["sc", "start", name]).ok

    def restart_explorer(self):
        self.run(["taskkill", "/F", "/IM", "explorer.exe"])
        windir = os.environ.get("WINDIR", r"C:\Windows")
        return self.launch(os.path.join(windir, "explorer.exe"))

    def is_admin(self):
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    # --- 레지스트리 (winreg) ---
    @staticmethod
    def _winreg():
        import winreg  # Windows 전용 표준 라이브러리

        return winreg

    def _root(self, path):
        w = self._winreg()
        hive, sub = split_hive(path)
        root = w.HKEY_CURRENT_USER if hive == "HKCU" else w.HKEY_LOCAL_MACHINE
        return w, root, sub

    def reg_get(self, path, name):
        w, root, sub = self._root(path)
        try:
            with w.OpenKey(root, sub, 0, w.KEY_READ | w.KEY_WOW64_64KEY) as k:
                data, t = w.QueryValueEx(k, name)
        except FileNotFoundError:
            return None
        names = {w.REG_SZ: "REG_SZ", w.REG_EXPAND_SZ: "REG_EXPAND_SZ", w.REG_DWORD: "REG_DWORD",
                 w.REG_QWORD: "REG_QWORD", w.REG_BINARY: "REG_BINARY", w.REG_MULTI_SZ: "REG_MULTI_SZ"}
        tname = names.get(t, "REG_BINARY")
        if tname == "REG_BINARY":
            data = bytes(data or b"").hex()
        elif tname == "REG_MULTI_SZ":
            data = list(data or [])
        return RegValue(tname, data)

    def reg_set(self, path, name, value):
        w, root, sub = self._root(path)
        tmap = {"REG_SZ": w.REG_SZ, "REG_EXPAND_SZ": w.REG_EXPAND_SZ, "REG_DWORD": w.REG_DWORD,
                "REG_QWORD": w.REG_QWORD, "REG_BINARY": w.REG_BINARY, "REG_MULTI_SZ": w.REG_MULTI_SZ}
        data = value.data
        if value.type == "REG_BINARY":
            data = bytes.fromhex(data or "")
        elif value.type in ("REG_DWORD", "REG_QWORD"):
            data = int(data)
        with w.CreateKeyEx(root, sub, 0, w.KEY_SET_VALUE | w.KEY_WOW64_64KEY) as k:
            w.SetValueEx(k, name, 0, tmap[value.type], data)

    def reg_delete_value(self, path, name):
        w, root, sub = self._root(path)
        try:
            with w.OpenKey(root, sub, 0, w.KEY_SET_VALUE | w.KEY_WOW64_64KEY) as k:
                w.DeleteValue(k, name)
        except FileNotFoundError:
            pass

    def reg_key_exists(self, path):
        w, root, sub = self._root(path)
        try:
            with w.OpenKey(root, sub, 0, w.KEY_READ | w.KEY_WOW64_64KEY):
                return True
        except FileNotFoundError:
            return False

    def reg_delete_key_tree(self, path):
        w, root, sub = self._root(path)
        if not sub:
            raise ValueError("하이브 루트는 삭제할 수 없습니다")
        try:
            with w.OpenKey(root, sub, 0, w.KEY_ALL_ACCESS | w.KEY_WOW64_64KEY) as k:
                children = []
                i = 0
                while True:
                    try:
                        children.append(w.EnumKey(k, i))
                        i += 1
                    except OSError:
                        break
            for c in children:
                self.reg_delete_key_tree(path + "\\" + c)
            w.DeleteKeyEx(root, sub, w.KEY_WOW64_64KEY, 0)
        except FileNotFoundError:
            pass


NVIDIA_FIELDS = [
    "name",
    "driver_version",
    "memory.total",
    "memory.used",
    "pcie.link.gen.current",
    "pcie.link.gen.max",
    "pcie.link.width.current",
    "pcie.link.width.max",
]


def default_system():
    return WindowsSystem()
