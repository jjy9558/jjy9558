"""지갑 고발장: Claude Code 로컬 트랜스크립트(~/.claude/projects/**/*.jsonl)로 토큰/비용 집계.

- 각 줄의 message.usage (input_tokens, output_tokens, cache_read_input_tokens,
  cache_creation_input_tokens) 와 message.model 을 읽는다.
- 같은 응답이 여러 줄/여러 파일(세션 재개)에 중복 기록되므로 (message.id, requestId) 로 중복 제거.
- 단가표는 아래 PLACEHOLDER_PRICES (예시값!) 또는 ~/.devguard/config.json 의 wallet.prices.
  반드시 공식 가격 페이지를 보고 직접 갱신하세요.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from devguard._jjkit import notify
from devguard.config import deep_merge, devguard_home, load_config

# !!! 예시(PLACEHOLDER) 단가 !!!  USD per 1M tokens.  실제 가격이 아닐 수 있습니다.
# 공식 가격 페이지를 보고 ~/.devguard/config.json 의 "wallet": {"prices": {...}} 로 덮어쓰세요.
# 갱신했다면 "prices_confirmed": true 로 바꾸면 보고서의 경고 문구가 사라집니다.
PLACEHOLDER_PRICES: Dict[str, Dict[str, float]] = {
    "opus":   {"input": 15.0, "output": 75.0, "cache_read": 1.5,  "cache_write": 18.75, "cache_write_1h": 30.0},
    "sonnet": {"input": 3.0,  "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75,  "cache_write_1h": 6.0},
    "haiku":  {"input": 1.0,  "output": 5.0,  "cache_read": 0.1,  "cache_write": 1.25,  "cache_write_1h": 2.0},
    "default": {"input": 3.0, "output": 15.0, "cache_read": 0.3,  "cache_write": 3.75,  "cache_write_1h": 6.0},
}

PRICE_WARNING = ("[주의] 단가표가 '예시값'입니다. 공식 가격 페이지를 보고 ~/.devguard/config.json 의 "
                 "wallet.prices 를 갱신한 뒤 wallet.prices_confirmed=true 로 바꾸세요. (금액은 추정치)")

TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")


# ----------------------------------------------------------------------------
# 파싱
# ----------------------------------------------------------------------------


@dataclass
class Usage:
    ts: Optional[_dt.datetime]
    session: str
    model: str
    project: str
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    key: Optional[Tuple[str, str]] = None

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write_5m + self.cache_write_1h


def _int(v: Any) -> int:
    if isinstance(v, bool):
        return 0
    if isinstance(v, int):
        return max(v, 0)
    if isinstance(v, float):
        return max(int(v), 0)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return 0


_TS_FRAC = re.compile(r"(\.\d+)")


def parse_timestamp(s: Any) -> Optional[_dt.datetime]:
    if not isinstance(s, str) or not s:
        return None
    t = s.strip()
    if t.endswith(("Z", "z")):
        t = t[:-1] + "+00:00"
    # Python 3.9 fromisoformat 은 소수부 자릿수가 3 또는 6 이어야 한다
    m = _TS_FRAC.search(t)
    if m:
        frac = m.group(1)[1:]
        t = t.replace(m.group(1), "." + (frac + "000000")[:6], 1)
    try:
        d = _dt.datetime.fromisoformat(t)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d


def parse_line(line: str, fallback_session: str = "") -> Optional[Usage]:
    line = line.strip()
    if not line or line[0] != "{":
        return None
    try:
        obj = json.loads(line)
    except (ValueError, RecursionError):
        return None
    if not isinstance(obj, dict):
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    model = msg.get("model")
    model = model if isinstance(model, str) else ""
    if model == "<synthetic>":
        return None
    cc = usage.get("cache_creation")
    w5 = _int(usage.get("cache_creation_input_tokens"))
    w1 = 0
    if isinstance(cc, dict) and ("ephemeral_5m_input_tokens" in cc or "ephemeral_1h_input_tokens" in cc):
        w5 = _int(cc.get("ephemeral_5m_input_tokens"))
        w1 = _int(cc.get("ephemeral_1h_input_tokens"))
    u = Usage(
        ts=parse_timestamp(obj.get("timestamp")),
        session=str(obj.get("sessionId") or fallback_session or "unknown"),
        model=model,
        project=str(obj.get("cwd") or ""),
        input=_int(usage.get("input_tokens")),
        output=_int(usage.get("output_tokens")),
        cache_read=_int(usage.get("cache_read_input_tokens")),
        cache_write_5m=w5,
        cache_write_1h=w1,
    )
    mid, rid = msg.get("id"), obj.get("requestId")
    if isinstance(mid, str) and mid and isinstance(rid, str) and rid:
        u.key = (mid, rid)
    elif isinstance(mid, str) and mid:
        u.key = (mid, "")
    if u.total == 0:
        return None
    return u


def projects_dir(env: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if env is None else env
    base = env.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def iter_jsonl(root: str, min_mtime: Optional[float] = None) -> Iterator[str]:
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".jsonl"):
                continue
            p = os.path.join(dirpath, fn)
            if min_mtime is not None:
                try:
                    if os.path.getmtime(p) < min_mtime:
                        continue
                except OSError:
                    continue
            yield p


def load_usage(root: str, since: Optional[_dt.datetime] = None) -> List[Usage]:
    """root 아래 모든 jsonl 을 읽어 중복 제거된 Usage 목록을 돌려준다. 깨진 줄/파일은 건너뜀."""
    min_mtime = (since.timestamp() - 3600) if since else None
    by_key: Dict[Tuple[str, str], Usage] = {}
    keyless: List[Usage] = []
    for path in iter_jsonl(root, min_mtime):
        stem = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    u = parse_line(line, stem)
                    if u is None:
                        continue
                    if u.key is None:
                        keyless.append(u)
                    else:
                        old = by_key.get(u.key)
                        if old is None or u.total >= old.total:  # 스트리밍 중간 기록보다 최종 기록 우선
                            by_key[u.key] = u
        except OSError:
            continue
    return list(by_key.values()) + keyless


# ----------------------------------------------------------------------------
# 비용 계산
# ----------------------------------------------------------------------------


def merged_prices(wcfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Dict[str, float]]:
    prices = {k: dict(v) for k, v in PLACEHOLDER_PRICES.items()}
    user = (wcfg or {}).get("prices") or {}
    for fam, p in user.items():
        if isinstance(p, dict):
            prices.setdefault(fam, dict(prices["default"])).update(
                {k: float(v) for k, v in p.items() if isinstance(v, (int, float)) and not isinstance(v, bool)})
    return prices


def price_for(model: str, prices: Mapping[str, Mapping[str, float]]) -> Tuple[str, Mapping[str, float], bool]:
    """(매칭된 계열 키, 단가, 정확히 매칭됐는지). 가장 긴 키가 우선."""
    low = model.lower()
    best = ""
    for fam in prices:
        if fam != "default" and not fam.startswith("_") and fam.lower() in low and len(fam) > len(best):
            best = fam
    if best:
        return best, prices[best], True
    return "default", prices["default"], False


def cost_of(u: Usage, prices: Mapping[str, Mapping[str, float]]) -> float:
    _fam, p, _exact = price_for(u.model, prices)
    w1 = p.get("cache_write_1h", p.get("cache_write", 0.0))
    return (
        u.input * p.get("input", 0.0)
        + u.output * p.get("output", 0.0)
        + u.cache_read * p.get("cache_read", 0.0)
        + u.cache_write_5m * p.get("cache_write", 0.0)
        + u.cache_write_1h * w1
    ) / 1_000_000.0


@dataclass
class Bucket:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0
    messages: int = 0

    def add(self, u: Usage, cost: float) -> None:
        self.input += u.input
        self.output += u.output
        self.cache_read += u.cache_read
        self.cache_write += u.cache_write_5m + u.cache_write_1h
        self.cost += cost
        self.messages += 1

    @property
    def total_tokens(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write


@dataclass
class DayReport:
    date: _dt.date
    total: Bucket = field(default_factory=Bucket)
    models: Dict[str, Bucket] = field(default_factory=dict)
    sessions: Dict[str, Bucket] = field(default_factory=dict)
    unknown_models: set = field(default_factory=set)


def local_date(u: Usage, tz: Optional[_dt.tzinfo]) -> Optional[_dt.date]:
    if u.ts is None:
        return None
    return u.ts.astimezone(tz).date()


def aggregate(usages: Iterable[Usage], prices: Mapping[str, Mapping[str, float]],
              tz: Optional[_dt.tzinfo] = None) -> Dict[_dt.date, DayReport]:
    days: Dict[_dt.date, DayReport] = {}
    for u in usages:
        d = local_date(u, tz)
        if d is None:
            continue
        rep = days.setdefault(d, DayReport(d))
        c = cost_of(u, prices)
        rep.total.add(u, c)
        rep.models.setdefault(u.model or "(unknown)", Bucket()).add(u, c)
        rep.sessions.setdefault(u.session, Bucket()).add(u, c)
        if not price_for(u.model, prices)[2]:
            rep.unknown_models.add(u.model or "(unknown)")
    return days


def evaluate_threshold(spent: float, limit: float, warn_ratio: float = 0.8) -> str:
    """'none'(한도 없음) | 'ok' | 'warn' | 'over'"""
    if not limit or limit <= 0:
        return "none"
    if spent >= limit:
        return "over"
    if spent >= limit * warn_ratio:
        return "warn"
    return "ok"


# ----------------------------------------------------------------------------
# 출력
# ----------------------------------------------------------------------------


def _fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return "%.2fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.1fK" % (n / 1_000)
    return str(n)


def _bucket_line(label: str, b: Bucket, width: int = 34) -> str:
    return "  %-*s $%8.2f  in %7s  out %7s  cache읽기 %7s  cache쓰기 %7s" % (
        width, label[:width], b.cost, _fmt_tok(b.input), _fmt_tok(b.output),
        _fmt_tok(b.cache_read), _fmt_tok(b.cache_write))


def format_report(days: Mapping[_dt.date, DayReport], prices_confirmed: bool = False,
                  top_sessions: int = 5) -> str:
    lines: List[str] = ["=== 지갑 고발장 (Claude Code 사용량 추정) ==="]
    if not prices_confirmed:
        lines.append(PRICE_WARNING)
    if not days:
        lines.append("(기록 없음)")
        return "\n".join(lines)
    grand = Bucket()
    for d in sorted(days):
        rep = days[d]
        lines.append("")
        lines.append("[%s] 합계 $%.2f  (%d 응답, 토큰 %s)" % (d.isoformat(), rep.total.cost, rep.total.messages,
                                                       _fmt_tok(rep.total.total_tokens)))
        for model, b in sorted(rep.models.items(), key=lambda kv: -kv[1].cost):
            lines.append(_bucket_line("모델 " + model, b))
        for sid, b in sorted(rep.sessions.items(), key=lambda kv: -kv[1].cost)[:top_sessions]:
            lines.append(_bucket_line("세션 " + sid[:8], b))
        if rep.unknown_models:
            lines.append("  (단가표에 없는 모델 -> default 단가 적용: %s)" % ", ".join(sorted(rep.unknown_models)))
        grand.cost += rep.total.cost
        grand.messages += rep.total.messages
    if len(days) > 1:
        lines.append("")
        lines.append("기간 합계 $%.2f" % grand.cost)
    return "\n".join(lines)


def report_json(days: Mapping[_dt.date, DayReport]) -> str:
    out = {}
    for d, rep in sorted(days.items()):
        out[d.isoformat()] = {
            "cost_usd": round(rep.total.cost, 6),
            "tokens": {"input": rep.total.input, "output": rep.total.output,
                       "cache_read": rep.total.cache_read, "cache_write": rep.total.cache_write},
            "models": {m: round(b.cost, 6) for m, b in rep.models.items()},
            "sessions": {s: round(b.cost, 6) for s, b in rep.sessions.items()},
        }
    return json.dumps(out, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------------
# 오늘 지출 / 알림
# ----------------------------------------------------------------------------


def start_of_day(now: _dt.datetime) -> _dt.datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def spend_today(root: str, wcfg: Mapping[str, Any], now: Optional[_dt.datetime] = None,
                tz: Optional[_dt.tzinfo] = None) -> Tuple[DayReport, Dict[str, Bucket]]:
    now = now or _dt.datetime.now().astimezone()
    tz = tz or now.tzinfo
    since = start_of_day(now.astimezone(tz))
    usages = load_usage(root, since)
    days = aggregate(usages, merged_prices(wcfg), tz)
    today = now.astimezone(tz).date()
    rep = days.get(today) or DayReport(today)
    return rep, rep.sessions


def summary_line(rep: DayReport, wcfg: Mapping[str, Any], session_id: str = "") -> str:
    limit = float(wcfg.get("daily_limit_usd") or 0)
    s = "[devguard 지갑] 오늘 $%.2f" % rep.total.cost
    if limit > 0:
        s += " / 한도 $%.2f (%d%%)" % (limit, round(rep.total.cost / limit * 100))
    if session_id and session_id in rep.sessions:
        s += " | 이 세션 $%.2f" % rep.sessions[session_id].cost
    if not wcfg.get("prices_confirmed"):
        s += " | 단가 예시값(추정)"
    return s


LEVEL_TEXT = {
    "none": "한도 미설정",
    "ok": "정상",
    "warn": "경고: 한도에 근접했습니다",
    "over": "초과: 오늘 한도를 넘겼습니다",
}


def notify_state_path(home: Optional[str] = None) -> str:
    return os.path.join(home or devguard_home(), "wallet-state.json")


def should_notify(level: str, today: _dt.date, home: Optional[str] = None) -> bool:
    """같은 날 같은(또는 더 낮은) 수준의 알림은 한 번만."""
    if level not in ("warn", "over"):
        return False
    order = {"warn": 1, "over": 2}
    try:
        with open(notify_state_path(home), "r", encoding="utf-8") as f:
            st = json.load(f)
        if st.get("date") == today.isoformat() and order.get(st.get("level"), 0) >= order[level]:
            return False
    except Exception:
        pass
    return True


def mark_notified(level: str, today: _dt.date, home: Optional[str] = None) -> None:
    p = notify_state_path(home)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"date": today.isoformat(), "level": level}, f)
    except OSError:
        pass


def send_ntfy(url: str, title: str, body: str, priority: str = "default", timeout: float = 5.0) -> bool:
    """ntfy 토픽 URL 로 보낸다(jjkit notify, 헤더 모드). 실패해도 예외를 던지지 않는다."""
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return False
        server_path, _, topic = parts.path.rstrip("/").rpartition("/")
        status = notify.ntfy(topic, body, title=title, priority=priority, tags=["money_with_wings"],
                             server="%s://%s%s" % (parts.scheme, parts.netloc, server_path),
                             mode="headers", timeout=timeout)
        return 200 <= status < 300
    except Exception:
        return False


def run_alert(root: str, cfg: Mapping[str, Any], now: Optional[_dt.datetime] = None,
              tz: Optional[_dt.tzinfo] = None, ntfy_url: str = "", home: Optional[str] = None,
              sender=send_ntfy, out=print) -> str:
    wcfg = cfg["wallet"]
    now = now or _dt.datetime.now().astimezone()
    rep, _ = spend_today(root, wcfg, now, tz)
    limit = float(wcfg.get("daily_limit_usd") or 0)
    level = evaluate_threshold(rep.total.cost, limit, float(wcfg.get("warn_ratio", 0.8)))
    out(summary_line(rep, wcfg))
    out("상태: " + LEVEL_TEXT[level])
    url = ntfy_url or wcfg.get("ntfy_url") or ""
    today = now.astimezone(tz).date() if tz else now.date()
    if url and should_notify(level, today, home):
        body = "%s\n오늘 $%.2f / 한도 $%.2f" % (LEVEL_TEXT[level], rep.total.cost, limit)
        ok = sender(url, "devguard wallet", body, "high" if level == "over" else "default")
        out("ntfy 전송 " + ("성공" if ok else "실패"))
        if ok:
            mark_notified(level, today, home)
    return level


def run_stop_hook(stdin_text: str, root: Optional[str] = None, cfg: Optional[Mapping[str, Any]] = None,
                  now: Optional[_dt.datetime] = None, tz: Optional[_dt.tzinfo] = None) -> str:
    """Stop 훅: {"systemMessage": "..."} JSON 문자열을 돌려준다 (decision 은 절대 내지 않는다)."""
    try:
        try:
            payload = json.loads(stdin_text) if stdin_text.strip() else {}
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if cfg is None:
            cfg = load_config(payload.get("cwd") or os.getcwd())
        wcfg = cfg["wallet"]
        rep, _ = spend_today(root or projects_dir(), wcfg, now, tz)
        msg = summary_line(rep, wcfg, str(payload.get("session_id") or ""))
        limit = float(wcfg.get("daily_limit_usd") or 0)
        level = evaluate_threshold(rep.total.cost, limit, float(wcfg.get("warn_ratio", 0.8)))
        if level in ("warn", "over"):
            msg += " | " + LEVEL_TEXT[level]
        return json.dumps({"systemMessage": msg}, ensure_ascii=True)
    except Exception:  # noqa: BLE001
        return ""


CONFIG_TEMPLATE = {
    "wallet": {
        "daily_limit_usd": 20,
        "session_limit_usd": 0,
        "warn_ratio": 0.8,
        "ntfy_url": "",
        "prices_confirmed": False,
        "prices": {
            "_comment": "아래는 예시값입니다. 공식 가격 페이지를 보고 수정하세요 (USD per 1M tokens).",
            **{k: v for k, v in PLACEHOLDER_PRICES.items() if k != "default"},
        },
    }
}
