"""Windows 키 입력 (ctypes SendInput). 순수 함수(파싱·구조 만들기)와 실제 전송을 분리했다.

- parse_combo("ctrl+shift+esc") -> 가상 키 코드 목록 (수정 키 먼저). 어느 OS에서든 동작.
- key_combo_inputs(vks) / text_inputs(text) -> INPUT 구조를 설명하는 dict 목록. 어느 OS에서든 동작.
- WindowsKeySender.send_* 만 실제 SendInput을 부른다. Windows가 아니면 WindowsOnlyError.
"""
import sys

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

MODIFIERS = {"ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "windows": 0x5B,
             "meta": 0x5B, "super": 0x5B}
SPECIAL = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C,
    # 미디어 키
    "playpause": 0xB3, "play/pause": 0xB3, "play_pause": 0xB3, "play-pause": 0xB3,
    "next": 0xB0, "nexttrack": 0xB0, "prev": 0xB1, "previous": 0xB1, "prevtrack": 0xB1,
    "volumeup": 0xAF, "volume_up": 0xAF, "volup": 0xAF, "volume-up": 0xAF,
    "volumedown": 0xAE, "volume_down": 0xAE, "voldown": 0xAE, "volume-down": 0xAE,
    "mute": 0xAD, "volumemute": 0xAD, "volume_mute": 0xAD,
}
# KEYEVENTF_EXTENDEDKEY가 필요한 키 (방향키·편집 키·Win 키)
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B}
VK_RETURN = 0x0D
MAX_TEXT = 500


class WindowsOnlyError(RuntimeError):
    pass


class KeyParseError(ValueError):
    pass


def parse_key(name):
    n = str(name or "").strip().lower()
    if not n:
        raise KeyParseError("빈 키 이름")
    if n in MODIFIERS:
        return MODIFIERS[n]
    if n in SPECIAL:
        return SPECIAL[n]
    if len(n) == 1 and "a" <= n <= "z":
        return ord(n.upper())
    if len(n) == 1 and "0" <= n <= "9":
        return ord(n)
    if n[0] == "f" and n[1:].isdigit() and 1 <= int(n[1:]) <= 24:
        return 0x70 + int(n[1:]) - 1
    raise KeyParseError("알 수 없는 키: %r" % name)


def parse_combo(combo):
    """'ctrl+shift+esc' -> [0x11, 0x10, 0x1B]. 수정 키는 앞으로, 일반 키는 정확히 1개."""
    if not isinstance(combo, str) or not combo.strip():
        raise KeyParseError("키 조합이 비었습니다")
    parts = combo.replace(" ", "").split("+")
    if "" in parts:
        raise KeyParseError("잘못된 키 조합: %r" % combo)
    mods, keys = [], []
    for p in parts:
        vk = parse_key(p)
        (mods if p.lower() in MODIFIERS else keys).append(vk)
    if len(keys) != 1:
        raise KeyParseError("키 조합에는 수정 키 외에 키가 정확히 1개 필요합니다: %r" % combo)
    if len(set(mods)) != len(mods):
        raise KeyParseError("수정 키가 중복됐습니다: %r" % combo)
    return mods + keys


def _key_event(vk, up):
    flags = (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0) | (KEYEVENTF_KEYUP if up else 0)
    return {"vk": vk, "scan": 0, "flags": flags}


def key_combo_inputs(vks):
    """앞에서부터 모두 누르고 역순으로 뗀다."""
    return [_key_event(v, False) for v in vks] + [_key_event(v, True) for v in reversed(vks)]


def text_inputs(text):
    """유니코드 문자열 -> KEYEVENTF_UNICODE 이벤트 (UTF-16 코드 유닛 단위, 서로게이트 쌍 포함). 줄바꿈은 Enter 키."""
    text = str(text)
    if len(text) > MAX_TEXT:
        raise ValueError("텍스트가 너무 깁니다 (최대 %d자)" % MAX_TEXT)
    out = []
    for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
        if ch == "\n":
            out += [_key_event(VK_RETURN, False), _key_event(VK_RETURN, True)]
            continue
        data = ch.encode("utf-16-le", "surrogatepass")
        for i in range(0, len(data), 2):
            unit = data[i] | (data[i + 1] << 8)
            out.append({"vk": 0, "scan": unit, "flags": KEYEVENTF_UNICODE})
            out.append({"vk": 0, "scan": unit, "flags": KEYEVENTF_UNICODE | KEYEVENTF_KEYUP})
    return out


class KeySender:
    """인터페이스. macro 모듈은 이것만 안다."""

    def send_combo(self, vks):
        raise NotImplementedError

    def type_text(self, text):
        raise NotImplementedError


class WindowsKeySender(KeySender):
    def __init__(self):
        if not sys.platform.startswith("win"):
            raise WindowsOnlyError("키 입력 매크로는 Windows에서만 동작합니다 (Windows only).")

    def _send(self, events):
        import ctypes
        from ctypes import wintypes

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class MOUSEINPUT(ctypes.Structure):  # union 크기를 맞추기 위한 자리
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class _U(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        arr = (INPUT * len(events))()
        for i, e in enumerate(events):
            arr[i].type = 1  # INPUT_KEYBOARD
            arr[i].u.ki = KEYBDINPUT(e["vk"], e["scan"], e["flags"], 0, 0)
        sent = ctypes.windll.user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
        if sent != len(events):
            raise OSError("SendInput이 %d/%d개만 전달했습니다 (관리자 권한 창이 앞에 있을 수 있음)" % (sent, len(events)))

    def send_combo(self, vks):
        self._send(key_combo_inputs(list(vks)))

    def type_text(self, text):
        self._send(text_inputs(text))


def default_sender():
    return WindowsKeySender()
