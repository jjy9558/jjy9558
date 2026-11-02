"""jarvis-hub 작업 큐 등록(선택). 서버가 없거나 설정이 비어 있으면 건너뛴다(예외 없음)."""
from __future__ import annotations

from ._jjkit.netutil import NetError, request_json

JOB_TYPE = "story.batch"


def submit(summary: dict, *, url: str, device: str, token: str, opener=None, timeout: float = 4.0) -> tuple[bool, str]:
    """``POST {url}/api/jobs``. summary 는 후보 제목/경로만(대본 본문은 보내지 않는다). (성공 여부, 한국어 메시지)."""
    if not url or not device or not token:
        return False, "jarvis 설정(--jarvis-url/--jarvis-device/--jarvis-token 또는 JARVIS_*)이 비어 있어 건너뜀"
    body = {"type": JOB_TYPE, "payload": summary, "caps": [], "priority": 0,
            "title": "사연 공장 후보 %d개" % len(summary.get("candidates", []))}
    try:
        _, data = request_json(url.rstrip("/") + "/api/jobs", body,
                               headers={"X-Jarvis-Device": device, "X-Jarvis-Token": token},
                               opener=opener, timeout=timeout)
    except NetError as e:
        return False, "jarvis 허브에 연결하지 못해 건너뜀 (%s)" % e
    job = data.get("job") if isinstance(data, dict) else None
    jid = job.get("id") if isinstance(job, dict) else None
    return True, "jarvis 작업 등록 완료%s" % (" (#%s)" % jid if jid is not None else "")
