"""되팔이 장부: 매입가·수리비·수수료·판매가 -> 순이익, 보유일, 시급. JSON 한 파일에 저장한다.

같은 입력을 다시 실행해도 장부가 변하지 않는다(멱등): ID 는 이름+매입가에서 정해지고,
이미 판매 처리된 건에 날짜를 생략하고 다시 `sold` 하면 기존 날짜를 유지한다.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from . import prices
from ._jjkit import textwidth as tw

VERSION = 1
CSV_COLUMNS = ["id", "name", "buy_date", "buy_price", "buy_fee", "repair", "other_cost", "cost_total",
               "sold_date", "sell_price", "sell_fee", "shipping", "profit", "roi_pct", "hold_days",
               "hours", "hourly", "market", "note"]
TAX_NOTE = ("참고: 반복적·계속적으로 사서 파는 경우 사업 소득으로 볼 수 있어 사업자등록·소득세 신고 대상일 수 있습니다. "
            "기준은 국세청/홈택스에서 확인하세요. 이 장부는 정리용이며 세무 자문이 아닙니다.")


class LedgerError(Exception):
    pass


def default_id(name: str, buy_price: int) -> str:
    h = hashlib.sha1(("%s|%d" % (re.sub(r"\s+", " ", name.strip().lower()), buy_price)).encode("utf-8")).hexdigest()
    return "L" + h[:6]


def parse_money(text, what: str = "금액") -> int:
    if isinstance(text, int):
        v = text
    else:
        v = prices.parse_won(str(text))
    if v is None or v < 0:
        raise LedgerError("%s 을(를) 읽을 수 없습니다: %r (예: 150000, 15만, 12.5만)" % (what, text))
    return int(v)


def parse_date(text: str | None, default: date | None = None) -> date:
    if not text:
        if default is None:
            raise LedgerError("날짜가 필요합니다")
        return default
    try:
        return date.fromisoformat(text)
    except ValueError as e:
        raise LedgerError("날짜는 YYYY-MM-DD 형식이어야 합니다: %r" % text) from e


def load(path: str | os.PathLike) -> dict:
    p = Path(path)
    if not p.exists():
        return {"version": VERSION, "items": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise LedgerError("장부를 읽지 못했습니다 (%s): %s" % (p, e)) from e
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise LedgerError("장부 형식이 이상합니다: %s" % p)
    return data


def save(path: str | os.PathLike, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = VERSION
    data["items"] = sorted(data["items"], key=lambda x: (x.get("buy_date", ""), x["id"]))
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".ledger.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def find(data: dict, key: str) -> dict:
    items = data["items"]
    exact = [i for i in items if i["id"] == key]
    if exact:
        return exact[0]
    pre = [i for i in items if i["id"].startswith(key)]
    if len(pre) == 1:
        return pre[0]
    byname = [i for i in items if i["name"] == key]
    if len(byname) == 1:
        return byname[0]
    if not pre and not byname:
        raise LedgerError("장부에 없는 ID/이름입니다: %s" % key)
    raise LedgerError("여러 건이 걸립니다: %s -> ID 를 정확히 쓰세요" % ", ".join(i["id"] for i in (pre or byname)))


def add(data: dict, *, name: str, buy_price, buy_fee=None, repair=None, other_cost=None, buy_date: str | None = None,
        note: str | None = None, hours: float | None = None, item_id: str | None = None,
        today: date | None = None) -> tuple[dict, str]:
    """(항목, 'added'|'updated'|'unchanged')"""
    if not name.strip():
        raise LedgerError("이름이 비었습니다")
    bp = parse_money(buy_price, "매입가")
    iid = item_id or default_id(name, bp)
    supplied = {}
    if buy_fee is not None:
        supplied["buy_fee"] = parse_money(buy_fee, "매입 수수료")
    if repair is not None:
        supplied["repair"] = parse_money(repair, "수리비")
    if other_cost is not None:
        supplied["other_cost"] = parse_money(other_cost, "기타 비용")
    if note is not None:
        supplied["note"] = note
    if hours is not None:
        if hours < 0:
            raise LedgerError("소요 시간은 0 이상이어야 합니다")
        supplied["hours"] = float(hours)
    if buy_date is not None:
        supplied["buy_date"] = parse_date(buy_date).isoformat()
    existing = next((i for i in data["items"] if i["id"] == iid), None)
    if existing is None:
        item = {"id": iid, "name": name.strip(), "buy_price": bp, "buy_fee": 0, "repair": 0, "other_cost": 0,
                "buy_date": (today or date.today()).isoformat(), "note": "", "hours": 0.0, "sold": None}
        item.update(supplied)
        data["items"].append(item)
        return item, "added"
    changed = False
    if existing["name"] != name.strip() and item_id:
        existing["name"] = name.strip()
        changed = True
    if item_id and existing["buy_price"] != bp:
        existing["buy_price"] = bp
        changed = True
    for k, val in supplied.items():
        if existing.get(k) != val:
            existing[k] = val
            changed = True
    return existing, "updated" if changed else "unchanged"


def sell(data: dict, key: str, *, price, fee=None, fee_rate: float | None = None, market_rate: float | None = None,
         market: str | None = None, shipping=None, sold_date: str | None = None,
         today: date | None = None) -> tuple[dict, str]:
    item = find(data, key)
    sp = parse_money(price, "판매가")
    if fee is not None:
        sf, basis = parse_money(fee, "판매 수수료"), "직접 입력"
    elif fee_rate is not None:
        if not 0 <= fee_rate < 100:
            raise LedgerError("수수료율은 0 이상 100 미만 퍼센트로 쓰세요 (예: 3.5)")
        sf, basis = int(round(sp * fee_rate / 100.0)), "%.2f%% 입력" % fee_rate
    elif market_rate is not None:
        sf, basis = int(round(sp * market_rate)), "%s 설정 수수료율 %.2f%%(근사치)" % (market or "시장", market_rate * 100)
    else:
        sf, basis = 0, "수수료 없음으로 계산"
    old = item.get("sold") or {}
    new = {
        "price": sp, "fee": sf, "fee_basis": basis,
        "shipping": parse_money(shipping, "택배비") if shipping is not None else old.get("shipping", 0),
        "market": market if market is not None else old.get("market", ""),
        "date": parse_date(sold_date).isoformat() if sold_date else old.get("date") or (today or date.today()).isoformat(),
    }
    if parse_date(new["date"]) < parse_date(item["buy_date"]):
        raise LedgerError("판매일(%s)이 매입일(%s)보다 빠릅니다" % (new["date"], item["buy_date"]))
    if old == new:
        return item, "unchanged"
    item["sold"] = new
    return item, "updated" if old else "sold"


# ---------------------------------------------------------------- 계산
def cost_total(i: dict) -> int:
    return int(i["buy_price"]) + int(i.get("buy_fee", 0)) + int(i.get("repair", 0)) + int(i.get("other_cost", 0))


def metrics(i: dict, today: date) -> dict:
    cost = cost_total(i)
    s = i.get("sold")
    m = {"cost_total": cost, "hold_days": None, "profit": None, "roi_pct": None, "hourly": None}
    bd = date.fromisoformat(i["buy_date"])
    if s:
        profit = int(s["price"]) - cost - int(s.get("fee", 0)) - int(s.get("shipping", 0))
        m["profit"] = profit
        m["roi_pct"] = round(profit * 100.0 / cost, 1) if cost else None
        m["hold_days"] = (date.fromisoformat(s["date"]) - bd).days
        if i.get("hours"):
            m["hourly"] = int(round(profit / i["hours"]))
    else:
        m["hold_days"] = (today - bd).days
    return m


def rows(data: dict, today: date) -> list[dict]:
    out = []
    for i in sorted(data["items"], key=lambda x: (x.get("buy_date", ""), x["id"])):
        m = metrics(i, today)
        s = i.get("sold") or {}
        out.append({
            "id": i["id"], "name": i["name"], "buy_date": i["buy_date"], "buy_price": i["buy_price"],
            "buy_fee": i.get("buy_fee", 0), "repair": i.get("repair", 0), "other_cost": i.get("other_cost", 0),
            "cost_total": m["cost_total"], "sold_date": s.get("date", ""), "sell_price": s.get("price", ""),
            "sell_fee": s.get("fee", ""), "shipping": s.get("shipping", ""),
            "profit": "" if m["profit"] is None else m["profit"],
            "roi_pct": "" if m["roi_pct"] is None else m["roi_pct"],
            "hold_days": "" if m["hold_days"] is None else m["hold_days"],
            "hours": i.get("hours") or "", "hourly": "" if m["hourly"] is None else m["hourly"],
            "market": s.get("market", ""), "note": i.get("note", ""), "sold": bool(s),
        })
    return out


def totals(rs: list[dict]) -> dict:
    sold = [r for r in rs if r["sold"]]
    open_ = [r for r in rs if not r["sold"]]
    cost_sold = sum(r["cost_total"] for r in sold)
    profit = sum(r["profit"] for r in sold)
    holds = [r["hold_days"] for r in sold if r["hold_days"] != ""]
    hours = sum(float(r["hours"]) for r in sold if r["hours"])
    prof_h = sum(r["profit"] for r in sold if r["hours"])
    return {
        "items": len(rs), "sold": len(sold), "open": len(open_),
        "revenue": sum(r["sell_price"] for r in sold), "cost_sold": cost_sold,
        "fees": sum(r["sell_fee"] for r in sold) , "shipping": sum(r["shipping"] for r in sold),
        "profit": profit, "roi_pct": round(profit * 100.0 / cost_sold, 1) if cost_sold else None,
        "avg_hold_days": round(sum(holds) / len(holds), 1) if holds else None,
        "losses": [r["id"] for r in sold if r["profit"] < 0],
        "open_cost": sum(r["cost_total"] for r in open_),
        "hourly": int(round(prof_h / hours)) if hours else None,
    }


def _won(n) -> str:
    return "" if n == "" or n is None else format(int(n), ",")


def render_report(data: dict, today: date) -> list[str]:
    rs = rows(data, today)
    if not rs:
        return ["장부가 비어 있습니다. `ledger add --name ... --buy ...` 로 시작하세요."]
    t = totals(rs)
    body = []
    for r in rs:
        body.append([r["id"], r["name"], _won(r["cost_total"]), _won(r["sell_price"]) or "-",
                     _won(r["profit"]) if r["profit"] != "" else "보유중",
                     "" if r["roi_pct"] == "" else "%s%%" % r["roi_pct"],
                     ("%s일" % r["hold_days"]) if r["hold_days"] != "" else ""])
    lines = tw.table(body, ["ID", "이름", "총비용", "판매가", "순이익", "수익률", "보유"],
                     right_cols=(2, 3, 4, 5, 6), max_width={1: 28})
    lines.append("")
    lines.append("합계: %d건 (판매 %d, 보유 %d)" % (t["items"], t["sold"], t["open"]))
    if t["sold"]:
        lines.append("  판매액 %s원, 판매 건 비용 %s원, 수수료 %s원, 택배비 %s원"
                     % (_won(t["revenue"]), _won(t["cost_sold"]), _won(t["fees"]), _won(t["shipping"])))
        roi = "" if t["roi_pct"] is None else " (수익률 %s%%)" % t["roi_pct"]
        lines.append("  순이익 합계 %s원%s" % (_won(t["profit"]), roi))
        if t["avg_hold_days"] is not None:
            lines.append("  평균 보유일 %s일" % t["avg_hold_days"])
        if t["hourly"] is not None:
            lines.append("  시급(소요 시간을 적은 건만) %s원/시간" % _won(t["hourly"]))
        if t["losses"]:
            lines.append("  손해 본 거래: %s" % ", ".join(t["losses"]))
    if t["open"]:
        lines.append("  보유 재고 비용 %s원 (미판매)" % _won(t["open_cost"]))
    lines.append("")
    lines.append(TAX_NOTE)
    return lines


def write_csv(path: str | os.PathLike, rs: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:  # 엑셀에서 한글이 안 깨지게 BOM
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rs:
            w.writerow(r)


def write_json(path: str | os.PathLike, rs: list[dict], tot: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    clean = [{k: v for k, v in r.items() if k != "sold"} for r in rs]
    p.write_text(json.dumps({"totals": tot, "items": clean}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")
