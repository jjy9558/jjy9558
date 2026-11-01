"""테스트용 가짜 시스템 + PowerShell 출력 견본."""

import copy
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pc_doctor.system import SystemBase, split_hive  # noqa: E402

GB = 1024 ** 3

# 실제 PowerShell 5.1 ConvertTo-Json 출력 형태를 흉내 낸 견본 (RTX 5080, DDR5 2x32GB EXPO 6000)
SNAPSHOT_RAW = {
    "cpu": {"Name": "AMD Ryzen 7 9800X3D 8-Core Processor           ", "Manufacturer": "AuthenticAMD",
            "NumberOfCores": 8, "NumberOfLogicalProcessors": 16, "MaxClockSpeed": 4700},
    "computer": {"Manufacturer": "Micro-Star International Co., Ltd.", "Model": "MS-7E49",
                 "TotalPhysicalMemory": 68_400_000_000, "AutomaticManagedPagefile": True},
    "board": {"Manufacturer": "Micro-Star International Co., Ltd.", "Product": "MAG X870 TOMAHAWK WIFI (MS-7E51)"},
    "bios": {"SMBIOSBIOSVersion": "1.A20", "ReleaseDate": "2025-06-01"},
    "memory": [
        {"Capacity": 32 * GB, "Speed": 4800, "ConfiguredClockSpeed": 6000, "BankLabel": "P0 CHANNEL A",
         "DeviceLocator": "DIMM 1", "Manufacturer": "G Skill Intl", "PartNumber": "F5-6000J3038F16G    "},
        {"Capacity": 32 * GB, "Speed": 4800, "ConfiguredClockSpeed": 6000, "BankLabel": "P0 CHANNEL B",
         "DeviceLocator": "DIMM 1", "Manufacturer": "G Skill Intl", "PartNumber": "F5-6000J3038F16G    "},
    ],
    "memory_slots": 4,
    "os": {"Caption": "Microsoft Windows 11 Pro", "Version": "10.0.26100", "BuildNumber": "26100",
           "TotalVisibleMemorySize": 66_800_000, "FreePhysicalMemory": 40_000_000},
    "os_extra": {"DisplayVersion": "24H2", "UBR": 4351},
    # 단일 GPU → PowerShell이 배열을 풀어 객체로 낼 수 있음 (as_list로 처리되는지 확인)
    "gpu": {"Name": "NVIDIA GeForce RTX 5080", "DriverVersion": "32.0.15.7680", "DriverDate": "2026-09-15",
            "AdapterRAM": 4293918720},
    "physical_disks": [
        {"DeviceId": "0", "FriendlyName": "Samsung SSD 990 PRO 2TB", "MediaType": "SSD", "BusType": "NVMe",
         "Size": 2_000_398_934_016, "HealthStatus": "Healthy"},
        {"DeviceId": "1", "FriendlyName": "WDC WD40EZAZ", "MediaType": "HDD", "BusType": "SATA",
         "Size": 4_000_787_030_016, "HealthStatus": "Healthy"},
    ],
    "volumes": [
        {"DriveLetter": "C", "Label": "", "FileSystem": "NTFS", "Size": 1000 * GB, "SizeRemaining": 400 * GB, "DiskNumber": 0},
        {"DriveLetter": "D", "Label": "Data", "FileSystem": "NTFS", "Size": 3700 * GB, "SizeRemaining": 1500 * GB, "DiskNumber": 1},
    ],
    "device_guard": {"VirtualizationBasedSecurityStatus": 0, "SecurityServicesRunning": [0]},
    "game_mode": None,
    "hags": 2,
    "startup": [{"Name": "Discord", "Location": "HKU\\...\\Run"}, {"Name": "Steam", "Location": "HKU\\...\\Run"}],
    "pagefile": {"Name": "C:\\pagefile.sys", "AllocatedBaseSize": 8192, "CurrentUsage": 120},
    "top_memory": [{"Name": "llama-server", "Id": 1234, "WorkingSetMB": 20480}, {"Name": "chrome", "Id": 99, "WorkingSetMB": 2048}],
    "top_cpu": [{"Name": "chrome", "Id": 99, "CPUSeconds": 812.3}],
}

POWERCFG_BALANCED_KO = "전원 구성표 GUID: 381b4222-f2b5-4f9c-a9d4-e6b4e9ca5e9a  (균형 조정)\r\n"
POWERCFG_SAVER_EN = "Power Scheme GUID: a1841308-3542-4df3-9d6c-0bbc1e8a1b7c  (Power saver)\r\n"
NVIDIA_SMI = "NVIDIA GeForce RTX 5080, 576.88, 16303, 1200, 1, 5, 16, 16\r\n"

HOTFIXES = [
    {"HotFixID": "KB0000000", "Description": "Security Update", "InstalledOn": "2026-10-20"},
    {"HotFixID": "KB0000002", "Description": "Update", "InstalledOn": "2026-09-01"},
]
HISTORY = [
    {"Title": "2026-10 누적 업데이트 (KB0000000)", "Date": "2026-10-20", "ResultCode": 2},
    {"Title": "드라이버 업데이트 (KB0000009)", "Date": "2026-10-25", "ResultCode": 4},
]
PENDING = {"Title": "2026-11 미리 보기 (KB0000001)", "KB": ["KB0000001"]}  # 1개 → 객체로 풀린 형태


class FakeSystem(SystemBase):
    def __init__(self, ps=None, powercfg=POWERCFG_BALANCED_KO, nvidia=NVIDIA_SMI, admin=False, tasks=None,
                 services=None, process_paths=None, registry=None, readonly_values=None):
        self.ps = {
            "snapshot": json.dumps(SNAPSHOT_RAW),
            "hotfixes": json.dumps(HOTFIXES),
            "update_history": json.dumps(HISTORY),
            "pending_updates": json.dumps(PENDING),
            "processes": json.dumps(process_paths or []),
        }
        self.ps.update(ps or {})
        self.powercfg = powercfg
        self.nvidia = nvidia
        self.admin = admin
        self.tasks = set(t.lower() for t in (tasks or []))
        self.services = dict(services or {})
        self.registry = registry if registry is not None else {}  # {KEY(upper): {name: RegValue}}
        self.readonly_values = set(readonly_values or [])
        self.calls = []
        self.launched = []

    # --- 조회 ---
    def ps_raw(self, key):
        self.calls.append(("ps", key))
        return self.ps.get(key)

    def powercfg_active_raw(self):
        return self.powercfg

    def nvidia_smi_raw(self):
        return self.nvidia

    def tasklist_raw(self):
        return "".join('"%s","%d","Console","1","10,000 K"\r\n' % (t, i) for i, t in enumerate(sorted(self.tasks)))

    def service_query_raw(self, name):
        st = self.services.get(name)
        if st is None:
            return None
        code = {"RUNNING": 4, "STOPPED": 1}[st]
        return "SERVICE_NAME: %s\r\n        TYPE               : 10  WIN32_OWN_PROCESS\r\n        STATE              : %d  %s\r\n" % (name, code, st)

    def is_admin(self):
        return self.admin

    # --- 레지스트리 ---
    @staticmethod
    def _k(path):
        hive, sub = split_hive(path)
        return (hive + "\\" + sub).upper()

    def _check_hklm(self, path):
        if split_hive(path)[0] == "HKLM" and not self.admin:
            raise PermissionError("Access is denied (HKLM)")

    def reg_get(self, path, name):
        v = self.registry.get(self._k(path), {}).get(name.lower())
        return copy.deepcopy(v)

    def reg_set(self, path, name, value):
        self._check_hklm(path)
        if (self._k(path), name.lower()) in self.readonly_values:
            raise PermissionError("Access is denied")
        self.calls.append(("reg_set", path, name, value.type, value.data))
        # 상위 키들도 생성 (CreateKeyEx 동작)
        hive, sub = split_hive(path)
        parts = sub.split("\\")
        for i in range(1, len(parts) + 1):
            self.registry.setdefault((hive + "\\" + "\\".join(parts[:i])).upper(), {})
        self.registry[self._k(path)][name.lower()] = copy.deepcopy(value)

    def reg_delete_value(self, path, name):
        self._check_hklm(path)
        self.calls.append(("reg_delete_value", path, name))
        self.registry.get(self._k(path), {}).pop(name.lower(), None)

    def reg_key_exists(self, path):
        return self._k(path) in self.registry

    def reg_delete_key_tree(self, path):
        self._check_hklm(path)
        self.calls.append(("reg_delete_key_tree", path))
        k = self._k(path)
        for key in list(self.registry):
            if key == k or key.startswith(k + "\\"):
                del self.registry[key]

    # --- 상태 변경 ---
    def powercfg_setactive(self, guid):
        self.calls.append(("powercfg_setactive", guid))
        names = {"8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c": "고성능", "381b4222-f2b5-4f9c-a9d4-e6b4e9ca5e9a": "균형 조정"}
        self.powercfg = "전원 구성표 GUID: %s  (%s)\r\n" % (guid, names.get(guid, "사용자 지정"))
        return True

    def kill_process(self, image_name):
        self.calls.append(("kill", image_name))
        self.tasks.discard(image_name.lower())
        return True

    def launch(self, exe_path):
        self.calls.append(("launch", exe_path))
        self.launched.append(exe_path)
        self.tasks.add(os.path.basename(exe_path.replace("\\", "/")).lower())
        return True

    def service_stop(self, name):
        self.calls.append(("service_stop", name))
        self.services[name] = "STOPPED"
        return True

    def service_start(self, name):
        self.calls.append(("service_start", name))
        self.services[name] = "RUNNING"
        return True

    def restart_explorer(self):
        self.calls.append(("restart_explorer",))
        return True


class TempHome:
    """PC_DOCTOR_HOME을 임시 폴더로 바꿔 실제 %LOCALAPPDATA%를 건드리지 않게 함."""

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="pcdoc-")
        self.old = os.environ.get("PC_DOCTOR_HOME")
        os.environ["PC_DOCTOR_HOME"] = self.dir
        return self.dir

    def __exit__(self, *exc):
        if self.old is None:
            os.environ.pop("PC_DOCTOR_HOME", None)
        else:
            os.environ["PC_DOCTOR_HOME"] = self.old
        shutil.rmtree(self.dir, ignore_errors=True)


def sample_snapshot(**overrides):
    from pc_doctor import snapshot

    raw = copy.deepcopy(SNAPSHOT_RAW)
    raw.update(overrides.pop("raw", {}))
    return snapshot.normalize(raw, powercfg_raw=overrides.pop("powercfg", POWERCFG_BALANCED_KO),
                              nvidia_raw=overrides.pop("nvidia", NVIDIA_SMI),
                              models=overrides.pop("models", None),
                              taken_at=overrides.pop("taken_at", "2026-11-01T12:00:00"), hostname="TESTPC")
