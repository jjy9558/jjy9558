"""알람 연동 아침 브리핑: 알람이 승인되면 morning-brief의 brief-latest.json을 읽어 폰이 낭독하게 한다.

- 폴더 사이 결합은 파일 계약 하나뿐이다 (morning-brief/mbrief/contract.py 참고).
  쓰는 키: date, speak_text, reject_text. 모르는 키는 무시.
- 읽기 전용: 파일은 'r'로만 열고 절대 쓰지 않는다. 경로는 설정(alarm.morning_gate.brief_path), 기본 꺼짐.
- date가 오늘이 아니면 오래된 파일로 보고 speak_text를 읽지 않는다 -> 짧은 대체 문장.
- 반려 판정에서는 speak_text를 절대 읽지 않는다. (선택) reject_text 한 줄만.
"""
import json
import logging

from .relay import clean_say_text

log = logging.getLogger("sidekick.morning_gate")

STALE_DEFAULT = "오늘 브리핑이 아직 없어요."
REJECT_DEFAULT = "브리핑은 일어나면 해 드립니다."
MAX_BYTES = 256 * 1024

DEFAULTS = {
    "enabled": False,
    "brief_path": "",       # 예: C:/dev/morning-brief/out/brief-latest.json
    "stale_text": STALE_DEFAULT,
    "speak_reject": False,  # True면 반려 때 reject_text 한 줄을 덧붙임
    "reject_text": REJECT_DEFAULT,  # 파일의 reject_text를 못 읽을 때 쓸 문장
    "max_chars": 300,
}


def read_brief(path, today):
    """(상태, 객체|None). 상태: ok | missing | stale | invalid. 파일을 수정하지 않는다."""
    if not path:
        return "missing", None
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_BYTES + 1)
    except OSError:
        return "missing", None
    if len(raw) > MAX_BYTES:
        return "invalid", None
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "invalid", None
    if not isinstance(obj, dict):
        return "invalid", None
    if obj.get("date") != today.isoformat():
        return "stale", obj
    speak = obj.get("speak_text")
    if not isinstance(speak, str) or not speak.strip():
        return "invalid", obj
    return "ok", obj


class MorningGate:
    def __init__(self, cfg, clock):
        self.cfg = dict(DEFAULTS, **(cfg or {}))
        self.clock = clock

    @property
    def enabled(self):
        return bool(self.cfg["enabled"] and self.cfg["brief_path"])

    def _limit(self):
        return int(self.cfg.get("max_chars") or 300)

    def on_approved(self):
        """(낭독할 문장, 상태). 꺼져 있으면 ("", "disabled")."""
        if not self.enabled:
            return "", "disabled"
        try:
            status, obj = read_brief(self.cfg["brief_path"], self.clock().date())
        except Exception as e:  # 브리핑 때문에 알람 승인이 깨지면 안 된다
            log.warning("아침 브리핑 읽기 실패: %s", type(e).__name__)
            status, obj = "invalid", None
        if status == "ok":
            return clean_say_text(obj["speak_text"], self._limit()), "ok"
        log.info("아침 브리핑 건너뜀: %s", status)
        return clean_say_text(self.cfg["stale_text"] or STALE_DEFAULT, self._limit()), status

    def on_rejected(self):
        """반려 때 덧붙일 한 줄. speak_text는 읽지 않는다."""
        if not (self.enabled and self.cfg["speak_reject"]):
            return ""
        text = ""
        try:
            with open(self.cfg["brief_path"], "rb") as f:
                obj = json.loads(f.read(MAX_BYTES).decode("utf-8"))
            rej = obj.get("reject_text") if isinstance(obj, dict) else None
            if isinstance(rej, str):
                text = rej
        except (OSError, ValueError):
            pass
        return clean_say_text(text or self.cfg["reject_text"] or REJECT_DEFAULT, 120)
