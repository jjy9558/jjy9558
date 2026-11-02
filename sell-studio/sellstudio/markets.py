"""플랫폼별 판매글 초안. 제목 길이·입력칸·설명 구성은 config 데이터(근사치)를 따른다."""
from __future__ import annotations

import re

from . import captions as cap

PLACEHOLDER = re.compile(r"\[[^\]\n]{1,60}\]")


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def fmt_won(n: int | None) -> str:
    return "%s원" % format(int(n), ",") if n is not None else "[가격 입력]"


def _cut_title(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut and text[limit] != " ":
        cut = cut.rsplit(" ", 1)[0]
    return cut.strip() or text[:limit]


def item_values(data: dict) -> dict:
    """사용자 입력 우선, 비면 사진 분석 결과로 보충."""
    guess = cap.guess_from_captions(data)
    name = _clean(data.get("name")) or _clean(guess["name"])
    return {
        "name": name,
        "spec": _clean(data.get("spec")),
        "condition": _clean(data.get("condition")),
        "grade": _clean(data.get("grade")),
        "used": "중고",
        "defects": [_clean(d) for d in data.get("defects", []) if _clean(d)],
        "defects_guess": guess["defects"],
        "included": [_clean(d) for d in data.get("included", []) if _clean(d)],
        "trade": _clean(data.get("trade")),
        "notes": _clean(data.get("notes")),
        "photo_count": len(data.get("photos", [])),
    }


def make_title(values: dict, parts: list[str], limit: int) -> tuple[str, list[str]]:
    notes: list[str] = []
    name = values["name"] or "[상품명 입력]"
    chosen: list[str] = []
    for p in parts:
        v = name if p == "name" else values.get(p, "")
        v = _clean(v)
        if not v:
            continue
        low = " ".join(chosen).lower()
        if p != "name" and v.lower() in low:
            continue  # 이미 제목에 있는 말은 반복하지 않는다
        chosen.append(v)
    title = " ".join(chosen)
    while len(title) > limit and len(chosen) > 1:
        dropped = chosen.pop()  # 뒤쪽(덜 중요한) 조각부터 뺀다
        notes.append("제목 길이 제한(%d자) 때문에 '%s' 를 뺐습니다" % (limit, dropped))
        title = " ".join(chosen)
    if len(title) > limit:
        title = _cut_title(title, limit)
        notes.append("상품명이 길어 %d자에서 잘랐습니다. 직접 줄여 쓰는 편이 좋습니다" % limit)
    return title, notes


def _section_lines(key: str, v: dict, extra: dict) -> list[str]:
    if key == "summary":
        what = " ".join(x for x in (v["name"] or "[상품명 입력]", v["spec"]) if x)
        line = "%s 판매합니다." % what
        if v["photo_count"]:
            line += " 사진 %d장 참고해 주세요." % v["photo_count"]
        return [line]
    if key == "condition":
        return ["상태: " + (v["condition"] or "[사용 기간과 전체 상태 직접 입력]")]
    if key == "defects":
        if v["defects"]:
            return ["하자: " + ", ".join(v["defects"])]
        if v["defects_guess"]:
            return ["하자: [사진 분석 참고용: %s - 실물 확인 후 직접 입력]" % ", ".join(v["defects_guess"])]
        return ["하자: [있으면 구체적으로, 없으면 '확인한 범위에서 하자 없음'이라고 직접 입력]"]
    if key == "included":
        return ["구성품: " + (", ".join(v["included"]) if v["included"] else "[구성품 직접 입력]")]
    if key == "trade":
        return ["거래 방법: " + (v["trade"] or "[직거래 지역 / 택배 가능 여부 직접 입력]"),
                "연락은 플랫폼 채팅으로 부탁드립니다."]
    if key == "notes":
        return [v["notes"]] if v["notes"] else []
    return []


def build_description(v: dict, sections: list[str], desc_max: int | None) -> tuple[str, list[str]]:
    notes: list[str] = []
    blocks = [("\n".join(_section_lines(k, v, {})), k) for k in sections]
    blocks = [(b, k) for b, k in blocks if b]
    text = "\n\n".join(b for b, _ in blocks)
    while desc_max and len(text) > desc_max and len(blocks) > 2:
        b, k = blocks.pop()
        notes.append("설명 길이 제한(%d자) 때문에 '%s' 항목을 뺐습니다" % (desc_max, k))
        text = "\n\n".join(b for b, _ in blocks)
    if desc_max and len(text) > desc_max:
        notes.append("설명이 %d자 제한(근사치)을 넘습니다: 직접 줄이세요" % desc_max)
    return text, notes


def suggested_price(data: dict, override: int | None = None) -> int | None:
    if override is not None:
        return override
    if data.get("price_final") is not None:
        return int(data["price_final"])
    pr = data.get("price")
    if isinstance(pr, dict) and pr.get("median"):
        return int(pr["median"])
    return None


def build_draft(data: dict, market: str, conf: dict, price: int | None = None) -> dict:
    v = item_values(data)
    title, tnotes = make_title(v, conf.get("title_parts", ["name"]), int(conf["title_max"]))
    desc, dnotes = build_description(v, conf.get("desc_sections", []), conf.get("desc_max"))
    price = suggested_price(data, price)
    text_all = "\n".join([title, desc])
    todo = sorted(set(PLACEHOLDER.findall(text_all)))
    fields = dict(conf.get("fields", {}))
    todo += [("%s: %s" % (k, val)) for k, val in fields.items() if PLACEHOLDER.search(str(val))]
    notes = tnotes + dnotes
    if price is None:
        notes.append("가격 힌트가 없습니다: `price --paste` 로 시세를 넣거나 --price 로 직접 정하세요")
    return {
        "market": market,
        "label": conf.get("label", market),
        "title": title,
        "title_max": int(conf["title_max"]),
        "price": price,
        "description": desc,
        "fields": fields,
        "notes": notes,
        "todo": todo,
        "approximate": bool(conf.get("approximate", True)),
    }


def render_draft(d: dict) -> str:
    """복사해서 붙여넣기 좋은 텍스트. 자동 게시는 하지 않는다."""
    approx = " (근사치 규칙)" if d.get("approximate") else ""
    lines = ["== %s%s: 제목 %d자 이내 ==" % (d["label"], approx, d["title_max"]),
             "제목: " + d["title"],
             "가격: " + fmt_won(d["price"]),
             "",
             "[설명]",
             d["description"],
             ""]
    if d["fields"]:
        lines.append("[입력칸]")
        lines += ["%s: %s" % (k, v) for k, v in d["fields"].items()]
    return "\n".join(lines).rstrip() + "\n"
