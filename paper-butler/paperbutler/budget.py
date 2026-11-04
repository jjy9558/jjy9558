"""월급 3봉투(고정비·생활비·저축)와 예산 비상벨.

- 설정: <home>/budget.json (손으로 고쳐도 됨. 카테고리 매핑 포함)
- 경보 상태: <home>/budget-alarms.json (달·봉투·기준선별로 이미 보낸 경보를 기억해서 반복하지 않음)
- 지출은 기존 가계부(ledger)의 값(승인 - 취소)을 그대로 쓴다. 카드/계좌 번호는 다루지 않는다.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import contract
from .masking import mask_text

BUDGET_FILE = "budget.json"
ALARM_FILE = "budget-alarms.json"
DEFAULT_THRESHOLDS = [70, 90, 100]

# (key, 표시 이름, 기본 카테고리 매핑)
ENVELOPES = [
    ("fixed", "고정비", ["공과금", "구독", "의료", "교육"]),
    ("living", "생활비", ["식비", "교통", "쇼핑", "문화", "기타"]),
    ("save", "저축", []),
]
KEYS = [k for k, _, _ in ENVELOPES]
NAMES = {k: n for k, n, _ in ENVELOPES}
NAME_TO_KEY = {n: k for k, n, _ in ENVELOPES}
DAILY_KEY = "living"       # "하루 쓸 수 있는 돈" 은 생활비 봉투 기준


class BudgetError(ValueError):
    pass


# -- 입력 해석 -------------------------------------------------------------
def parse_amount(s: str) -> int:
    t = str(s).strip().replace(",", "").replace("원", "").replace(" ", "")
    m = re.fullmatch(r"(\d+)(만)?", t)
    if not m:
        raise BudgetError("금액을 읽을 수 없습니다: %r (예: 2500000, 250만)" % s)
    return int(m.group(1)) * (10000 if m.group(2) else 1)


def parse_share(s: str) -> Dict[str, int]:
    """'40%' -> {'percent': 40}, '1000000' -> {'amount': 1000000}"""
    t = str(s).strip()
    if t.endswith("%"):
        try:
            p = float(t[:-1])
        except ValueError:
            raise BudgetError("비율을 읽을 수 없습니다: %r" % s)
        if not (0 <= p <= 100):
            raise BudgetError("비율은 0~100%% 여야 합니다: %r" % s)
        return {"percent": int(p) if p == int(p) else p}
    return {"amount": parse_amount(t)}


def parse_thresholds(s: str) -> List[int]:
    try:
        vals = sorted({int(x) for x in re.split(r"[,\s]+", s.strip()) if x})
    except ValueError:
        raise BudgetError("--thresholds 는 70,90,100 처럼 정수를 쉼표로 적어 주세요.")
    if not vals or any(v < 1 or v > 300 for v in vals):
        raise BudgetError("--thresholds 는 1~300 사이 정수여야 합니다.")
    return vals


def parse_map(items: List[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for it in items:
        if "=" not in it:
            raise BudgetError("--map 은 생활비=식비,교통 형식이어야 합니다: %r" % it)
        k, v = it.split("=", 1)
        k = k.strip()
        key = NAME_TO_KEY.get(k, k)
        if key not in KEYS:
            raise BudgetError("봉투 이름은 고정비/생활비/저축(fixed/living/save) 중 하나여야 합니다: %r" % k)
        out[key] = [c.strip() for c in v.split(",") if c.strip()]
    return out


# -- 설정 파일 ---------------------------------------------------------------
def default_config() -> Dict[str, Any]:
    return {
        "income": 0,
        "thresholds": list(DEFAULT_THRESHOLDS),
        "envelopes": {k: {"share": {"percent": 0}, "categories": list(c)} for k, _, c in ENVELOPES},
    }


def _write_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def load_config(home: Any) -> Optional[Dict[str, Any]]:
    p = os.path.join(str(home), BUDGET_FILE)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        raise BudgetError("%s 를 읽을 수 없습니다. 형식을 확인하거나 `budget set` 으로 다시 만드세요." % p)
    cfg = default_config()
    if not isinstance(data, dict):
        raise BudgetError("%s 의 최상위는 객체여야 합니다." % p)
    inc = data.get("income", 0)
    cfg["income"] = inc if isinstance(inc, int) and not isinstance(inc, bool) and inc >= 0 else 0
    th = data.get("thresholds")
    if isinstance(th, list) and th and all(isinstance(x, int) and not isinstance(x, bool) and 1 <= x <= 300 for x in th):
        cfg["thresholds"] = sorted(set(th))
    envs = data.get("envelopes")
    if isinstance(envs, dict):
        for k in KEYS:
            e = envs.get(k)
            if not isinstance(e, dict):
                continue
            sh = e.get("share")
            if isinstance(sh, dict) and ("percent" in sh or "amount" in sh):
                cfg["envelopes"][k]["share"] = {kk: sh[kk] for kk in ("percent", "amount") if kk in sh}
            cats = e.get("categories")
            if isinstance(cats, list):
                cfg["envelopes"][k]["categories"] = [str(c) for c in cats]
    return cfg


def envelope_amount(cfg: Dict[str, Any], key: str) -> int:
    sh = cfg["envelopes"][key]["share"]
    if "amount" in sh:
        return int(sh["amount"])
    return int(cfg["income"] * float(sh.get("percent", 0)) // 100)


def set_config(home: Any, income: Optional[str], shares: Dict[str, Optional[str]],
               maps: List[str], thresholds: Optional[str]) -> Tuple[Dict[str, Any], List[str]]:
    cfg = load_config(home) or default_config()
    notes: List[str] = []
    if income is not None:
        cfg["income"] = parse_amount(income)
    for k, v in shares.items():
        if v is not None:
            cfg["envelopes"][k]["share"] = parse_share(v)
    for k, cats in parse_map(maps).items():
        cfg["envelopes"][k]["categories"] = cats
    if thresholds is not None:
        cfg["thresholds"] = parse_thresholds(thresholds)
    # 검증
    uses_pct = any("percent" in cfg["envelopes"][k]["share"] and cfg["envelopes"][k]["share"].get("percent")
                   for k in KEYS)
    if uses_pct and cfg["income"] <= 0:
        raise BudgetError("비율(%)로 정하려면 --income 월급이 필요합니다.")
    pct = sum(float(cfg["envelopes"][k]["share"].get("percent", 0)) for k in KEYS
              if "percent" in cfg["envelopes"][k]["share"])
    if pct > 100:
        raise BudgetError("봉투 비율의 합이 100%%를 넘습니다 (%g%%)." % pct)
    seen: Dict[str, str] = {}
    for k in KEYS:
        for c in cfg["envelopes"][k]["categories"]:
            if c in seen and seen[c] != k:
                raise BudgetError("카테고리 '%s' 가 %s 와 %s 두 봉투에 들어 있습니다." % (c, NAMES[seen[c]], NAMES[k]))
            seen[c] = k
    total = sum(envelope_amount(cfg, k) for k in KEYS)
    if cfg["income"] and total > cfg["income"]:
        notes.append("봉투 합계 %s원이 월급 %s원보다 큽니다." % ("{:,}".format(total), "{:,}".format(cfg["income"])))
    _write_json(os.path.join(str(home), BUDGET_FILE), cfg)
    return cfg, notes


# -- 계산 ---------------------------------------------------------------------
def month_info(month: str, today: dt.date) -> Tuple[int, str]:
    """(남은 일수(오늘 포함), 'past'|'current'|'future')"""
    y, m = int(month[:4]), int(month[5:7])
    last = calendar.monthrange(y, m)[1]
    cur = (today.year, today.month)
    if (y, m) == cur:
        return last - today.day + 1, "current"
    if (y, m) < cur:
        return 0, "past"
    return last, "future"


def compute(cfg: Dict[str, Any], ledger: Dict[str, Any], month: str, today: dt.date) -> Dict[str, Any]:
    cat_to_key = {}
    for k in KEYS:
        for c in cfg["envelopes"][k]["categories"]:
            cat_to_key.setdefault(c, k)
    spent = {k: 0 for k in KEYS}
    unmapped: Dict[str, int] = {}
    for r in ledger["rows"]:
        if not r["counted"]:
            continue
        cat = r["category"] or "기타"
        k = cat_to_key.get(cat)
        if k is None:
            unmapped[cat] = unmapped.get(cat, 0) + r["signed"]
        else:
            spent[k] += r["signed"]
    days_left, phase = month_info(month, today)
    envs = []
    for k in KEYS:
        budget = envelope_amount(cfg, k)
        sp = max(0, spent[k])
        pct = (sp * 100 // budget) if budget > 0 else 0
        envs.append({
            "key": k, "name": NAMES[k], "budget": budget, "spent": sp, "remaining": budget - sp,
            "percent": int(pct), "tracked": bool(cfg["envelopes"][k]["categories"]),
            "crossed": [t for t in cfg["thresholds"] if budget > 0 and sp * 100 >= t * budget]
            if cfg["envelopes"][k]["categories"] else [],
        })
    daily_env = next(e for e in envs if e["key"] == DAILY_KEY)
    daily = max(0, daily_env["remaining"]) // days_left if days_left > 0 else 0
    return {"month": month, "days_left": days_left, "phase": phase, "daily_allowance": int(daily),
            "envelopes": envs, "unmapped": {k: v for k, v in unmapped.items() if v}}


def bar(percent: int, width: int = 20) -> str:
    filled = min(width, percent * width // 100)
    return "█" * filled + "░" * (width - filled) + (" !" if percent > 100 else "")


def _won(n: int) -> str:
    return "{:,}원".format(n)


def render(info: Dict[str, Any]) -> List[str]:
    from ._jjkit import textwidth
    rows = []
    for e in info["envelopes"]:
        if e["tracked"]:
            rest = ("남음 " + _won(e["remaining"])) if e["remaining"] >= 0 else ("초과 " + _won(-e["remaining"]))
            rows.append([e["name"], bar(e["percent"]), "%d%%" % e["percent"],
                         "%s / %s" % (_won(e["spent"]), _won(e["budget"])), rest])
        else:
            rows.append([e["name"], "(지출 추적 안 함)", "", "목표 " + _won(e["budget"]), "월급날 먼저 떼어 두기"])
    out = ["== %s 월급 3봉투 ==" % info["month"]]
    out += textwidth.table(rows, ["봉투", "소진", "%", "쓴 돈 / 예산", "남은 돈"], right_cols=[2])
    if info["phase"] == "current":
        out.append("이번 달 남은 날: %d일 (오늘 포함)" % info["days_left"])
        d = info["daily_allowance"]
        out.append("하루 쓸 수 있는 돈: %s (생활비 남은 돈 ÷ 남은 날)" % _won(d) if d else
                   "하루 쓸 수 있는 돈: 0원 (오늘은 구경만 합시다)")
    elif info["phase"] == "past":
        out.append("지난 달입니다. 남은 날: 0일")
    else:
        out.append("아직 시작 전인 달입니다. 한 달 전체 기준 하루 %s" % _won(info["daily_allowance"]))
    if info["unmapped"]:
        out.append("매핑 안 된 카테고리(봉투에 안 들어감): " +
                   ", ".join("%s %s" % (c, _won(v)) for c, v in sorted(info["unmapped"].items())))
    return out


# -- 경보 -----------------------------------------------------------------------
SPECIAL = {
    ("living", 70): "생활비 봉투 70% 소진. 장바구니에 담기만 하는 건 무료입니다.",
    ("living", 90): "생활비 봉투 90% 소진. 이번 주는 편의점 출입 금지 권고",
    ("living", 100): "생활비 봉투 바닥. 오늘부터 집밥과 물이 제일 맛있는 시기입니다.",
    ("fixed", 70): "고정비 봉투 70% 소진. 나갈 건 거의 다 나갔네요. 고마워요, 성실한 자동이체.",
    ("fixed", 90): "고정비 봉투 90% 소진. 이번 달 고정비는 사실상 마감입니다. 새 구독은 다음 달에.",
    ("fixed", 100): "고정비 봉투 한도 도달. 혹시 쓰지 않는 구독이 있는지 한번 훑어볼까요?",
}


def alert_message(env: Dict[str, Any], threshold: int) -> str:
    base = SPECIAL.get((env["key"], threshold))
    if base is None:
        base = "%s 봉투 %d%% 소진. 한 번만 더 생각하고 결제해요." % (env["name"], threshold)
    rest = env["remaining"]
    tail = "남은 돈 %s" % _won(rest) if rest >= 0 else "예산 초과 %s" % _won(-rest)
    return "%s (%s)" % (base, tail)


def load_state(home: Any) -> Dict[str, Any]:
    p = os.path.join(str(home), ALARM_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def run_alarm(home: Any, cfg: Dict[str, Any], info: Dict[str, Any],
              send: Optional[Callable[[str, str], None]] = None, dry_run: bool = False) -> List[Dict[str, Any]]:
    """새로 넘긴 기준선마다 경보 하나를 만든다 (달·봉투·기준선당 한 번).

    한 번에 여러 기준선을 넘으면(예: 60% -> 95%) 가장 높은 것만 알리고 낮은 것도 보낸 것으로 기록한다.
    send 가 있으면 전송하고, 전송에 실패하면 기록하지 않아 다음 실행에서 다시 시도한다.
    """
    state = load_state(home)
    month_state = state.setdefault(info["month"], {})
    alerts: List[Dict[str, Any]] = []
    for e in info["envelopes"]:
        if not e["crossed"]:
            continue
        done = set(month_state.get(e["key"], []))
        new = [t for t in e["crossed"] if t not in done]
        if not new:
            continue
        top = max(new)
        msg = mask_text(alert_message(e, top))
        status = "off"
        if send is not None:
            try:
                send("예산 비상벨 · %s %d%%" % (e["name"], top), msg)
                status = "sent"
            except Exception:  # 전송 실패는 경보 자체를 막지 않는다
                status = "failed"
        alerts.append({"envelope": e["name"], "threshold": top, "percent": e["percent"],
                       "message": msg, "ntfy": status})
        if status != "failed":
            month_state[e["key"]] = sorted(done | set(new))
    if not dry_run and any(a["ntfy"] != "failed" for a in alerts):
        # 오래된 달 기록은 12개월만 남긴다
        for m in sorted(state)[:-12]:
            state.pop(m, None)
        _write_json(os.path.join(str(home), ALARM_FILE), state)
    return alerts


def payload(today: dt.date, info: Dict[str, Any], alerts: List[Dict[str, Any]]) -> Dict[str, Any]:
    obj = {
        "version": contract.BUDGET_VERSION,
        "kind": "budget",
        "generated_for": today.isoformat(),
        "month": info["month"],
        "days_left": info["days_left"],
        "daily_allowance": info["daily_allowance"],
        "envelopes": [{k: e[k] for k in contract.BUDGET_ENVELOPE_KEYS} for e in info["envelopes"]],
        "alerts": alerts,
    }
    return contract.validate_budget(obj)
