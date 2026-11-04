"""사장님 책상: 카드 더미 + 대기 중인 결재 모으기.

- 카드: morning-brief 계약 파일 ``out/cards-latest.json`` (``[{kind,title,detail,due,actions[]}]``).
  파일이 없거나 깨져도 빈 더미. "확인/반려" 도장은 ``desk-log.jsonl``에 한 줄 기록만 한다.
- action: 문자열은 표시 전용 문장, ``{"id","label"}`` 객체만 버튼 후보. id 가 설정 ``desk.actions`` 화이트리스트
  (정확한 id 또는 ``"paperbutler.done:"`` 같은 접두사 + 검증된 인자 하나)에 맞을 때만 실행(셸 없이 argv). 기본은 빈 목록.
- 결재: s21-sidekick ``GET /api/approval/pending``, jarvis-hub ``GET /api/jobs?status=waiting_approval``.
  승인/반려는 해당 서비스 API로만 보낸다. ``link_only_patterns``에 걸리는 위험 결재는 버튼 없이 링크만.
"""
import hashlib
import json
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from .proxy import BackendError

STAMPS = {"ok": "확인", "reject": "반려"}
KEY_RE = re.compile(r"^[0-9a-f]{12}$")
SIDEKICK_ID_RE = re.compile(r"^[0-9a-f]{1,32}$")
JARVIS_ID_RE = re.compile(r"^\d{1,12}$")
# 접두사 화이트리스트("paperbutler.done:")에서 접미사로 받을 수 있는 인자 형식
ARG_PATTERNS = {"digits": re.compile(r"[0-9]{1,12}"), "slug": re.compile(r"[A-Za-z0-9_-]{1,64}")}
ARG_PLACEHOLDER = "{arg}"


class DeskError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _s(v, limit: int = 500) -> str:
    if v is None:
        return ""
    s = v if isinstance(v, str) else str(v)
    s = "".join(ch for ch in s if ch >= " " or ch in "\n\t")
    return s[:limit]


def card_key(kind: str, title: str, due: str) -> str:
    return hashlib.sha1(("%s\x1f%s\x1f%s" % (kind, title, due)).encode("utf-8")).hexdigest()[:12]


def due_sort_key(due: str):
    """마감 빠른 순. 해석 못 하는 값/없음은 맨 뒤(원래 순서 유지는 sorted가 해 준다)."""
    if not due:
        return (2, "")
    try:
        d = datetime.fromisoformat(due.replace("Z", "+00:00"))
        if d.tzinfo is not None:
            d = d.astimezone().replace(tzinfo=None)
        return (0, d.isoformat())
    except ValueError:
        return (1, due)


def resolve_action(allowed: dict, action_id) -> tuple[dict, list[str]] | None:
    """(스펙, 실행 argv) 또는 None. 정확히 같은 id 가 우선, 아니면 ':' 로 끝나는 접두사 항목.
    접두사 항목은 접미사 전체가 인자 형식(기본 digits)에 맞아야 하고 argv 의 "{arg}" 자리에 통째로(한 인자로) 들어간다."""
    if not isinstance(action_id, str) or not action_id or not isinstance(allowed, dict):
        return None
    spec = allowed.get(action_id)
    if isinstance(spec, dict) and not action_id.endswith(":"):
        return spec, list(spec["argv"])
    best = None
    for key, sp in allowed.items():
        if isinstance(key, str) and key.endswith(":") and isinstance(sp, dict) and action_id.startswith(key) \
                and (best is None or len(key) > len(best[0])):
            best = (key, sp)
    if best is None:
        return None
    key, sp = best
    arg = action_id[len(key):]
    pat = ARG_PATTERNS.get(sp.get("arg") or "digits")
    if pat is None or not pat.fullmatch(arg):
        return None
    return sp, [arg if x == ARG_PLACEHOLDER else x for x in sp["argv"]]


def _norm_action(a) -> dict | None:
    if isinstance(a, str):
        # 문자열은 표시 전용 문장(계약 호환). 실행 id 로 해석하지 않는다.
        label = _s(a, 120).strip()
        return {"id": "", "label": label, "url": "", "text": True} if label else None
    if not isinstance(a, dict):
        return None
    aid = _s(a.get("id") or a.get("action") or "", 64)
    label = _s(a.get("label") or a.get("title") or aid, 60)
    url = _s(a.get("url") or a.get("link") or "", 300)
    if url and not (url.startswith("/") and not url.startswith("//")):
        url = ""  # 외부 주소는 링크로 걸지 않는다(같은 출처 상대경로만)
    if not (aid or url):
        return None
    return {"id": aid, "label": label or "열기", "url": url}


def load_cards(path: Path | str | None) -> tuple[list[dict], str]:
    """(카드 목록, 상태). 상태: ok | missing | broken."""
    if not path:
        return [], "missing"
    p = Path(path).expanduser()
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], "missing"
    except (OSError, UnicodeDecodeError):
        return [], "broken"
    try:
        data = json.loads(raw)
    except ValueError:
        return [], "broken"
    if isinstance(data, dict):
        data = data.get("cards")
    if not isinstance(data, list):
        return [], "broken"
    cards = []
    for c in data[:200]:
        if not isinstance(c, dict):
            continue
        title = _s(c.get("title"), 120).strip()
        if not title:
            continue
        kind = _s(c.get("kind"), 30) or "card"
        due = _s(c.get("due"), 40)
        actions = c.get("actions") if isinstance(c.get("actions"), list) else []
        acts = [a for a in (_norm_action(x) for x in actions[:8]) if a]
        cards.append({"key": card_key(kind, title, due), "kind": kind, "title": title,
                      "detail": _s(c.get("detail"), 600), "due": due, "actions": acts})
    cards.sort(key=lambda c: due_sort_key(c["due"]))
    return cards, "ok"


class Desk:
    def __init__(self, cfg: dict, backends: dict, clock=time.time, runner=subprocess.run):
        self.cfg = cfg
        self.backends = backends
        self.clock = clock
        self.runner = runner
        self.log_path = Path(cfg.get("log_path") or "~/.phone-home/desk-log.jsonl").expanduser()
        self._lock = threading.Lock()
        pats = cfg.get("link_only_patterns") or []
        self.patterns = [p.lower() for p in pats if isinstance(p, str) and p]

    # ---- 기록 ----
    def log(self, entry: dict) -> None:
        entry = dict(entry, ts=datetime.fromtimestamp(self.clock()).isoformat(timespec="seconds"))
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)

    def stamps(self, max_lines: int = 2000) -> dict[str, str]:
        """카드 키 → 마지막 도장."""
        try:
            with open(self.log_path, encoding="utf-8") as f:
                lines = f.readlines()[-max_lines:]
        except (OSError, UnicodeDecodeError):
            return {}
        out = {}
        for ln in lines:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("type") == "stamp" and e.get("key"):
                out[str(e["key"])] = str(e.get("stamp"))
        return out

    # ---- 카드 ----
    def cards(self) -> tuple[list[dict], str]:
        cards, status = load_cards(self.cfg.get("cards_path"))
        stamped = self.stamps() if cards else {}
        allowed = self.cfg.get("actions") or {}
        for c in cards:
            c["stamp"] = stamped.get(c["key"], "")
            for a in c["actions"]:
                a["runnable"] = bool(a["id"]) and not a.get("text") and resolve_action(allowed, a["id"]) is not None
        return cards, status

    def stamp(self, key: str, stamp: str) -> dict:
        if stamp not in STAMPS:
            raise DeskError(400, "stamp는 ok 또는 reject")
        if not KEY_RE.match(key or ""):
            raise DeskError(400, "카드 키 형식 오류")
        card = next((c for c in load_cards(self.cfg.get("cards_path"))[0] if c["key"] == key), None)
        if card is None:
            raise DeskError(404, "그런 카드가 없습니다")
        self.log({"type": "stamp", "key": key, "stamp": stamp, "kind": card["kind"], "title": card["title"],
                  "due": card["due"]})
        return {"ok": True, "key": key, "stamp": stamp, "label": STAMPS[stamp]}

    def run_action(self, action_id: str, key: str = "") -> dict:
        allowed = self.cfg.get("actions") or {}
        resolved = resolve_action(allowed, action_id)
        if resolved is None:
            self.log({"type": "action", "action": _s(action_id, 64), "key": _s(key, 12), "result": "forbidden"})
            raise DeskError(403, "허용 목록에 없는 action입니다")
        spec, argv = resolved
        timeout = float(spec.get("timeout") or self.cfg.get("action_timeout") or 20)
        try:
            r = self.runner(argv, shell=False, capture_output=True, timeout=timeout,
                            text=True, encoding="utf-8", errors="replace")
            rc, out = r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
        except subprocess.TimeoutExpired:
            rc, out = -1, "시간 초과"
        except OSError as e:
            rc, out = -1, "실행 실패: %s" % type(e).__name__
        self.log({"type": "action", "action": action_id, "key": _s(key, 12), "rc": rc, "output": out[:200]})
        return {"ok": rc == 0, "action": action_id, "rc": rc, "output": out[:2000]}

    # ---- 결재 ----
    def risky(self, *texts: str) -> bool:
        blob = " ".join(t for t in texts if t).lower()
        return any(p in blob for p in self.patterns)

    def _sidekick(self, name, b) -> list[dict]:
        status, data = b.call_json("GET", "/api/approval/pending")
        if status != 200:
            raise BackendError("HTTP %d" % status)
        items = []
        for it in data.get("pending") or []:
            if not isinstance(it, dict) or not SIDEKICK_ID_RE.match(str(it.get("id") or "")):
                continue
            cmd = _s(it.get("command"), 400)
            title = _s(it.get("summary") or it.get("label") or "결재 요청", 120)
            items.append({"source": name, "kind": "sidekick", "id": str(it["id"]), "title": title,
                          "detail": cmd, "tool": _s(it.get("tool"), 40), "expires_in": it.get("expires_in"),
                          "link_only": self.risky(cmd, _s(it.get("label")), title)})
        return items

    def _jarvis(self, name, b) -> list[dict]:
        status, data = b.call_json("GET", "/api/jobs?status=waiting_approval&limit=50")
        if status != 200:
            raise BackendError("HTTP %d" % status)
        items = []
        for j in data.get("jobs") or []:
            if not isinstance(j, dict) or not JARVIS_ID_RE.match(str(j.get("id") or "")):
                continue
            if j.get("status") not in (None, "waiting_approval"):
                continue
            jtype = _s(j.get("type"), 40)
            payload = j.get("payload") if isinstance(j.get("payload"), dict) else {}
            preview = _s(payload.get("prompt") or payload.get("text") or payload.get("cmd") or "", 300)
            title = "#%s %s" % (j["id"], _s(j.get("title"), 100) or jtype)
            items.append({"source": name, "kind": "jarvis", "id": str(j["id"]), "title": title,
                          "detail": ("[%s] %s" % (jtype, preview)).strip(), "by": _s(j.get("created_by"), 40),
                          "link_only": self.risky(jtype, title, preview)})
        return items

    def approvals(self) -> tuple[list[dict], dict[str, str]]:
        """(결재 목록, 실패한 출처 → 이유)."""
        items, errors = [], {}
        for name, kind in (self.cfg.get("approvals") or {}).items():
            b = self.backends.get(name)
            if b is None:
                continue
            try:
                got = self._sidekick(name, b) if kind == "sidekick" else self._jarvis(name, b)
            except BackendError as e:
                errors[name] = str(e)
                continue
            for it in got:
                it["link"] = b.page_link()
            items.extend(got)
        return items, errors

    def decide(self, source: str, item_id: str, decision: str) -> dict:
        if decision not in ("allow", "deny"):
            raise DeskError(400, "decision은 allow 또는 deny")
        kind = (self.cfg.get("approvals") or {}).get(source)
        b = self.backends.get(source)
        if not kind or b is None:
            raise DeskError(404, "결재를 받는 서비스가 아닙니다")
        id_re = SIDEKICK_ID_RE if kind == "sidekick" else JARVIS_ID_RE
        if not id_re.match(str(item_id or "")):
            raise DeskError(400, "id 형식 오류")
        try:
            current = self._sidekick(source, b) if kind == "sidekick" else self._jarvis(source, b)
        except BackendError as e:
            raise DeskError(502, "%s에 연결할 수 없습니다 (%s)" % (source, e))
        item = next((i for i in current if i["id"] == item_id), None)
        if item is None:
            raise DeskError(404, "이미 처리됐거나 없는 결재입니다")
        if item["link_only"]:
            raise DeskError(403, "위험 결재는 %s 화면에서 직접 처리하세요" % source)
        try:
            if kind == "sidekick":
                status, data = b.call_json("POST", "/api/approval/decide", {"id": item_id, "decision": decision})
            else:
                action = "approve" if decision == "allow" else "reject"
                status, data = b.call_json("POST", "/api/jobs/%s/%s" % (item_id, action), {})
        except BackendError as e:
            raise DeskError(502, "%s에 연결할 수 없습니다 (%s)" % (source, e))
        self.log({"type": "decide", "source": source, "id": item_id, "decision": decision, "code": status,
                  "title": item["title"]})
        if status != 200:
            raise DeskError(status if 400 <= status < 600 else 502, _s(data.get("error") or "실패", 200))
        return {"ok": True, "source": source, "id": item_id, "decision": decision}

    def snapshot(self) -> dict:
        cards, status = self.cards()
        approvals, errors = self.approvals()
        return {"cards": cards, "cards_status": status, "approvals": approvals, "errors": errors,
                "actions": {k: _s(v.get("label") or k, 60) for k, v in (self.cfg.get("actions") or {}).items()}}
