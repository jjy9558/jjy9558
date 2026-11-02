"""가격, 보증, 사용 연수, 매물 종류, 위험 신호(red flag) 추출."""

import re
from datetime import date
from typing import List, Optional, Tuple

from . import catalog
from .aliases import Doc
from .models import Flag, Parsed

# ---------------------------------------------------------------------------
# 가격
# ---------------------------------------------------------------------------
_SINO_NUM = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_PRICE_LABEL = re.compile(r"(?:가격|판매가|판매\s*금액|희망가|희망\s*가격|금액|price|₩)\s*[:\-=은는]?\s*$")
_REF_WORDS = re.compile(r"신품|새제품|새\s*제품|구매가|구입가|정가|출시가|샀|당시|견적|원래|시세|신품가|새거|합계|합산|부품\s*값|개별")
_EXTRA_WORDS = re.compile(r"모니터|추가|별도|옵션|포함\s*시|포함시|배송비|택배비|택배\s*비|입금|보증금|예약금|그래픽\s*만|그래픽카드\s*만|네고\s*시|할인")
_SELL_SUFFIX = re.compile(r"^\s*(?:에|으로|원에)?\s*(?:판매|팝니다|팔아요|판매합니다|급처|처분|드립니다|드려요|내놓|넘깁니다|양도)")


def parse_sino_number(s: str) -> Optional[int]:
    """'팔십오' -> 85, '이백삼십' -> 230. 십/백/천 포함 시만 사용."""
    total = 0
    cur = 0
    for ch in s:
        if ch in _SINO_NUM:
            cur = _SINO_NUM[ch]
        elif ch == "십":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "백":
            total += (cur or 1) * 100
            cur = 0
        elif ch == "천":
            total += (cur or 1) * 1000
            cur = 0
        else:
            return None
    return total + cur


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_price_text(s: str, bare_man: bool = True) -> Optional[int]:
    """짧은 가격 문자열 하나를 원 단위 정수로. 'prices set' 등에서도 쓴다.

    '85만', '85만원', '85.5만', '85만5천원', '850,000원', '850000', '850k', '85'(=85만원 간주; bare_man=False 면 85원)
    """
    t = s.strip().lower().replace(" ", "")
    if not t:
        return None
    m = re.fullmatch(r"([일이삼사오육칠팔구십백천]+)만원?", t)
    if m:
        n = parse_sino_number(m.group(1))
        return n * 10000 if n else None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)만(?:원)?(?:(\d{1,2})천(?:원)?)?", t)
    if m:
        v = float(m.group(1)) * 10000
        if m.group(2):
            v += int(m.group(2)) * 1000
        return int(round(v))
    m = re.fullmatch(r"(\d+(?:\.\d+)?)만(\d{3,4})원?", t)
    if m:
        return int(round(float(m.group(1)) * 10000 + int(m.group(2))))
    m = re.fullmatch(r"₩?(\d{1,3}(?:,\d{3})+|\d+)원?", t)
    if m:
        v = int(m.group(1).replace(",", ""))
        if bare_man and "," not in m.group(1) and v < 10000 and "원" not in t:
            return v * 10000  # '85' -> 85만원
        return v
    m = re.fullmatch(r"(\d+(?:\.\d+)?)k", t)
    if m:
        return int(round(float(m.group(1)) * 1000))
    return None


_P_MAN = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)\s*만\s*(?:원)?(?:\s*(\d{1,2})\s*천\s*(?:원)?)?")
_P_MAN_TAIL = re.compile(r"(?<![\d.,])(\d+(?:\.\d+)?)\s*만\s*(\d{3,4})\s*원")
_P_WON = re.compile(r"₩?\s*(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d{4,9})\s*원")
_P_WON_SYM = re.compile(r"₩\s*(\d{1,3}(?:,\d{3})+|\d{4,9})")
_P_COMMA = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3}){1,2})(?![\d,])")
_P_K = re.compile(r"(?<![\d.,a-z])(\d{2,4}(?:\.\d)?)\s*k(?![a-z0-9])")
_P_SINO = re.compile(r"([일이삼사오육칠팔구십백천]{2,6})\s*만\s*원?")
_P_LABEL_BARE = re.compile(
    r"(?:가격|판매가|판매\s*금액|희망가|희망\s*가격|금액|price)\s*[:\-=은는]?\s*(\d{2,3}|\d{5,7})"
    r"(?![\d,.]*\s*(?:gb|g\b|만|원|k\b|hz|w\b|인치|mm|%|tb))")
_P_SELL_BARE = re.compile(r"(?<![\d.,a-z])(\d{2,3})\s*(?:에|으로)\s*(?:판매|팝니다|팔아요|판매합니다|급처|처분|드립니다|드려요|내놓|넘깁니다)")
_P_TITLE_BARE = re.compile(r"(?:^|[\s\[\(])(\d{2,3})\s*[\]\)]?\s*$")


def _cand(value: int, s: int, e: int, text: str, kind: str, explicit_unit: bool) -> dict:
    before = text[max(0, s - 16):s]
    after = text[e:e + 14]
    line_start = text.rfind("\n", 0, s) + 1
    first_line_end = text.find("\n")
    in_title = (first_line_end == -1 or s < first_line_end) and line_start == 0
    score = 0.0
    if _PRICE_LABEL.search(before):
        score += 5
    if re.search(r"(?:가격|판매가|희망가|금액)", text[line_start:s]):
        score += 2
    if in_title:
        score += 2
    if explicit_unit:
        score += 1
    if _SELL_SUFFIX.match(after):
        score += 3
    ref = bool(_REF_WORDS.search(before[-8:])) or bool(re.match(r"\s*(?:에\s*)?(?:샀|구매|구입|주고|주고\s*샀)", after))
    extra = bool(_EXTRA_WORDS.search(before[-10:])) or bool(re.search(r"\+\s*$", before))
    if ref:
        score -= 10
    if extra:
        score -= 6
    if re.match(r"\s*(?:네고|흥정)", after) is None and re.search(r"네고\s*(?:가능|사절|x|불가)", after):
        pass
    if value < 20000 or value > 30000000:
        score -= 5
    return {"value": value, "start": s, "end": e, "kind": kind, "score": score,
            "reference": ref, "extra": extra, "raw": text[s:e].strip()}


def extract_price(doc: Doc, p: Parsed) -> None:
    text = doc.text
    cands: List[dict] = []
    spans: List[Tuple[int, int]] = []

    def overlaps(s, e):
        return any(s < b and a < e for a, b in spans)

    def add(c):
        if not overlaps(c["start"], c["end"]):
            cands.append(c)
            spans.append((c["start"], c["end"]))

    for m in _P_MAN_TAIL.finditer(text):
        v = int(round(float(m.group(1)) * 10000 + int(m.group(2))))
        add(_cand(v, m.start(), m.end(), text, "man", True))
    for m in _P_MAN.finditer(text):
        v = float(m.group(1)) * 10000
        if m.group(2):
            v += int(m.group(2)) * 1000
        # '만' 뒤가 '원' 없이 한글이 붙는 일상어("만약", "만족")는 제외
        tail = text[m.end():m.end() + 2]
        if m.group(0).rstrip().endswith("만") and re.match(r"[가-힣]", tail) and not re.match(r"(?:원|에|대|이|으|정|쯤|부터|까지|입|입니다|인)", tail):
            if re.match(r"(?:약|족|들|큼|일|나|세|드|원)", tail):
                continue
        add(_cand(int(round(v)), m.start(), m.end(), text, "man", True))
    for m in _P_SINO.finditer(text):
        if not re.search(r"[십백천]", m.group(1)):
            continue
        n = parse_sino_number(m.group(1))
        if n:
            add(_cand(n * 10000, m.start(), m.end(), text, "sino", True))
    for m in _P_WON.finditer(text):
        v = int(m.group(1).replace(",", ""))
        add(_cand(v, m.start(), m.end(), text, "won", True))
    for m in _P_WON_SYM.finditer(text):
        v = int(m.group(1).replace(",", ""))
        add(_cand(v, m.start(), m.end(), text, "won", True))
    for m in _P_K.finditer(text):
        # '245K'(Ultra 5 245K), '8700K'(CPU 모델명)는 가격이 아니라 CPU 접미사
        num = m.group(1)
        if num.split(".")[0] in catalog.KNOWN_NUMBERS or re.search(r"(?:ultra|i[3579])\s*[3579]?[\s\-]*$", text[max(0, m.start() - 12):m.start()]):
            continue
        add(_cand(int(round(float(num) * 1000)), m.start(), m.end(), text, "k", True))
    for m in _P_COMMA.finditer(text):
        v = int(m.group(1).replace(",", ""))
        if 100000 <= v <= 20000000:
            add(_cand(v, m.start(), m.end(), text, "comma", False))
    for m in _P_LABEL_BARE.finditer(text):
        n = int(m.group(1))
        v = n * 10000 if n < 1000 else n
        c = _cand(v, m.start(1), m.end(1), text, "bare_label", False)
        c["score"] += 2
        if not overlaps(c["start"], c["end"]):
            cands.append(c)
            spans.append((c["start"], c["end"]))
    for m in _P_SELL_BARE.finditer(text):
        n = int(m.group(1))
        if 20 <= n <= 800:
            add(_cand(n * 10000, m.start(1), m.end(1), text, "bare_sell", False))
    if not cands:
        # 제목 줄 끝의 단독 숫자 ("... 본체 85")
        title = doc.title
        m = _P_TITLE_BARE.search(title)
        if m and 30 <= int(m.group(1)) <= 500:
            before = title[:m.start(1)]
            if not re.search(r"(?:램|ram|ssd|nvme|hdd|메모리|하드|m\.2)\s*$", before):
                s = text.find(title) + m.start(1)
                c = _cand(int(m.group(1)) * 10000, s, s + len(m.group(1)), text, "bare_title", False)
                cands.append(c)

    cands.sort(key=lambda c: (-c["score"], c["start"]))
    p.price_candidates = [
        {"value": c["value"], "raw": c["raw"], "kind": c["kind"], "reference": c["reference"], "extra": c["extra"]}
        for c in sorted(cands, key=lambda c: c["start"])
    ]

    sale = [c for c in cands if not c["reference"] and not c["extra"]]
    if sale:
        best = sale[0]
        conf = 0.9 if best["score"] >= 4 else 0.75
        if best["kind"] in ("bare_title", "bare_label", "bare_sell"):
            conf = min(conf, 0.6)
        p.set("price", best["value"], conf, best["raw"], kind=best["kind"])
        doc.claim(best["start"], best["end"])
    elif cands:
        # 참고/추가금액뿐이면 신뢰도 낮게 사용
        non_ref = [c for c in cands if not c["reference"]]
        if non_ref:
            b = non_ref[0]
            p.set("price", b["value"], 0.4, b["raw"], kind=b["kind"], uncertain=True)
            p.notes.append("가격 후보가 모두 추가금액/참고가격처럼 보임: {}".format(b["raw"]))
    refs = [c for c in cands if c["reference"]]
    if refs:
        p.set("original_price", refs[0]["value"], 0.7, refs[0]["raw"])
    if re.search(r"네고\s*(?:가능|환영|ok|오케이|조금|약간|소폭)|가격\s*제안|제안\s*(?:가능|환영)", text):
        p.set("negotiable", True, 0.8, "네고 가능")
    elif re.search(r"네고\s*(?:불가|x|사절|없|안\s*됨|안됩니다)|노네고|정찰제|가격\s*고정", text):
        p.set("negotiable", False, 0.8, "네고 불가")
    if re.search(r"택포|택배비\s*포함|배송비\s*포함", text):
        p.notes.append("택배비 포함 가격으로 보임")


# ---------------------------------------------------------------------------
# 보증 / 사용 연수
# ---------------------------------------------------------------------------
def _parse_today(today) -> date:
    if isinstance(today, date):
        return today
    if isinstance(today, str):
        y, m, d = (int(x) for x in today.split("-"))
        return date(y, m, d)
    return date.today()


_WARR_KW = re.compile(r"무상\s*a/?s|무상\s*보증|무상\s*수리|a/?s|에이에스|보증|워런티|warranty|제조사\s*보증|정품\s*등록|무상")


def extract_warranty(doc: Doc, p: Parsed, today: date) -> None:
    text = doc.text
    info = {"has": None, "until": None, "months_left": None, "receipt": False, "text": ""}
    neg = re.search(r"(?:a/?s|에이에스|보증|워런티)\s*(?:기간)?\s*(?:만료|끝|끝남|지남|지났|없|불가|x|종료|소멸)|무\s*보증|"
                    r"보증\s*(?:은|이)?\s*(?:없|끝|만료)|(?:만료|끝)\s*(?:됨|났|되었|됐)|a/?s\s*불가", text)
    if neg:
        info.update(has=False, text=neg.group(0))
        info["months_left"] = 0
    pos_kw = list(_WARR_KW.finditer(text))
    best = None
    def clip(t: str) -> str:
        return t.split("\n")[0].strip()

    for m in pos_kw:
        ctx_after = text[m.end():m.end() + 28].split("\n")[0]
        ctx_before = text[max(0, m.start() - 26):m.start()].split("\n")[-1]
        # 2027년 3월까지 / 2027.03 / 2027-03
        mm = re.search(r"(20\d{2})\s*(?:년|[.\-/])\s*(\d{1,2})?\s*(?:월)?", ctx_after) or \
            re.search(r"(20\d{2})\s*(?:년|[.\-/])\s*(\d{1,2})?\s*(?:월)?\s*(?:까지|만료|말|전까지)?\s*$", ctx_before)
        if mm:
            y = int(mm.group(1))
            mo = int(mm.group(2)) if mm.group(2) and 1 <= int(mm.group(2)) <= 12 else 6
            best = ("until", y, mo, clip(text[m.start():m.end() + 20]))
            break
        # 27년 3월까지
        mm = re.search(r"(?<![\d])(2\d)\s*년\s*(\d{1,2})?\s*월?\s*(?:까지|만료)", ctx_after) or \
            re.search(r"(?<![\d])(2\d)\s*년\s*(\d{1,2})?\s*월?\s*(?:까지|만료)\s*$", ctx_before)
        if mm:
            y = 2000 + int(mm.group(1))
            mo = int(mm.group(2)) if mm.group(2) and 1 <= int(mm.group(2)) <= 12 else 6
            best = ("until", y, mo, clip(text[m.start():m.end() + 20]))
            break
        # 1년 남음 / 6개월 남음 / 보증 6개월
        mm = re.search(r"(\d+(?:\.\d)?)\s*(년|개월|달)\s*(?:정도\s*)?(남|잔여|이상\s*남)?", ctx_after) or \
            re.search(r"(\d+(?:\.\d)?)\s*(년|개월|달)\s*(?:정도\s*)?(남|잔여)", ctx_before + text[m.start():m.end()])
        if mm:
            n = float(mm.group(1))
            months = n * 12 if mm.group(2) == "년" else n
            remaining = bool(mm.group(3)) or bool(re.search(r"남", ctx_after[:mm.end() + 4]))
            best = ("months", months, remaining, clip(text[m.start():m.end() + mm.end()]))
            break
        # 남은 보증 (기간 없음)
        if best is None and (re.search(r"남(?:아|음|았|아있|아 있)", ctx_after[:12]) or
                             re.search(r"(?:가능|있음|있어요|있습니다|유효)", ctx_after[:10])):
            best = ("has", None, None, clip(text[m.start():m.end() + 8]))
    if best and not neg:
        if best[0] == "until":
            y, mo = best[1], best[2]
            left = (y - today.year) * 12 + (mo - today.month)
            info.update(has=left > 0, until="{:04d}-{:02d}".format(y, mo),
                        months_left=max(0, left), text=best[3])
        elif best[0] == "months":
            info.update(has=best[1] > 0, months_left=best[1], text=best[3])
        else:
            info.update(has=True, months_left=None, text=best[3])
    elif best and neg:
        # '2025년 만료' 처럼 부정과 날짜가 함께: 날짜가 미래면 유효
        if best[0] == "until":
            y, mo = best[1], best[2]
            left = (y - today.year) * 12 + (mo - today.month)
            if left > 0 and not re.search(r"만료|끝|지남|지났|종료", neg.group(0)):
                info.update(has=True, until="{:04d}-{:02d}".format(y, mo), months_left=left, text=best[3])
    if re.search(r"(?:영수증|구매\s*내역|구매내역|인보이스|거래\s*명세|정품\s*등록|구매\s*인증)\s*(?:있|가능|보유|드림|첨부|인증)", text) \
            and not re.search(r"(?:영수증|구매\s*내역|구매내역)\s*(?:없|분실|x)", text):
        info["receipt"] = True
    if info["has"] is not None or info["receipt"]:
        p.set("warranty", info, 0.75 if info["months_left"] is not None else 0.55, info["text"])


def extract_age(doc: Doc, p: Parsed, today: date) -> None:
    text = doc.text
    age = None
    raw = ""
    m = re.search(r"(20\d{2})\s*년\s*(?:에\s*)?(?:(\d{1,2})\s*월\s*)?(?:에\s*)?(?:구매|구입|조립|출시|맞춤|산|샀|맞췄|제작)", text) or \
        re.search(r"(?:구매|구입|조립|제작)\s*(?:시기|연도|일)?\s*[:\-]?\s*(20\d{2})\s*(?:년)?\s*(?:[.\-/]?\s*(\d{1,2}))?", text)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2)) if m.group(2) and 1 <= int(m.group(2)) <= 12 else 6
        if 2010 <= y <= today.year:
            age = max(0.0, (today.year - y) + (today.month - mo) / 12.0)
            raw = m.group(0).strip()
    if age is None:
        m = re.search(r"(?<![\d.])(\d+(?:\.\d)?)\s*년\s*(?:정도\s*|가량\s*|쯤\s*)?(?:사용|썼|쓴|쓰던|써서|사용함)", text)
        if m:
            age = float(m.group(1))
            raw = m.group(0).strip()
    if age is None:
        m = re.search(r"(?<![\d.])(\d+)\s*개월\s*(?:정도\s*|가량\s*|쯤\s*)?(?:사용|썼|쓴|쓰던|써서)", text)
        if m:
            age = int(m.group(1)) / 12.0
            raw = m.group(0).strip()
    if age is None:
        if re.search(r"작년\s*(?:에\s*)?(?:구매|구입|조립)", text):
            age, raw = 1.0, "작년 구매"
        elif re.search(r"올해\s*(?:에\s*)?(?:구매|구입|조립)", text):
            age, raw = 0.3, "올해 구매"
        elif re.search(r"미개봉|새\s*제품|새제품|신품\s*급|신품급", text):
            age, raw = 0.1, "신품급"
    if age is not None:
        p.set("age_years", round(age, 2), 0.7, raw)


# ---------------------------------------------------------------------------
# 매물 종류
# ---------------------------------------------------------------------------
def classify_kind(doc: Doc, p: Parsed) -> None:
    text = doc.text
    cats = [k for k in ("cpu", "gpu", "ram_gb", "storage", "board", "psu_watt") if p.has(k)]
    if re.search(r"노트북|랩탑|laptop|맥북|macbook|갤럭시\s*북|씽크패드|thinkpad|그램\s*\d|lg\s*gram", text):
        p.set("kind", "laptop", 0.8, "노트북")
        return
    if re.search(r"올인원|미니\s*pc|미니pc|맥\s*미니|mac\s*mini", text):
        p.notes.append("올인원/미니 PC 로 보임: 부품 합산 방식의 정확도가 떨어집니다")
    single_kw = re.search(r"(?:그래픽\s*카드|그래픽|vga|gpu|cpu|램|파워|ssd|보드|메인보드|쿨러|케이스)\s*(?:단품|만\s*판매|만\s*팝니다|판매\s*합니다만)|단품|(?:그래픽카드|그래픽|cpu|램|파워|ssd|보드|메인보드)\s*만\s*(?:팝니다|판매|팔아요|처분)", text)
    full_kw = re.search(r"본체|완본체|조립\s*pc|조립pc|데스크탑|데스크톱|컴퓨터|풀세트|풀 세트|게이밍\s*pc|게이밍pc|사무용\s*pc", text)
    if single_kw and not full_kw:
        p.set("kind", "single_part", 0.8, single_kw.group(0))
    elif len(cats) >= 3 or (full_kw and len(cats) >= 2):
        p.set("kind", "full_pc", 0.85, full_kw.group(0) if full_kw else "구성 다수")
    elif len(cats) <= 1 and p.has("price") and not full_kw:
        p.set("kind", "single_part", 0.6, "단일 부품으로 추정")
    else:
        p.set("kind", "full_pc", 0.5, "추정")
    if re.search(r"삼성\s*(?:전자)?\s*(?:데스크탑|올인원)|lg\s*(?:전자)?\s*(?:데스크탑|올인원)|hp\s*(?:omen|pavilion|데스크탑)|"
                 r"델\s*(?:옵티플렉스|xps)|dell|레노버|lenovo|삼보|주연|한성|trigem|브랜드\s*pc|브랜드pc", text):
        p.set("brand_pc", True, 0.7, "브랜드 PC")


# ---------------------------------------------------------------------------
# 위험 신호 / 긍정 신호
# ---------------------------------------------------------------------------
_NEG_AFTER = re.compile(r"^\s*(?:은|는|이|가|도|를|을|난|났던)?\s*(?:적|일|경우|흔적)?\s*(?:이|은|도)?\s*(?:전혀\s*)?(?:없|아님|아니|아닙|안\s?함|안\s?했|안\s?썼|안\s?써|x(?![a-z])|무(?![a-z가-힣])|제로|절대|없습니다|없어요)")
_NEG_BEFORE = re.compile(r"(?:비|무|안|노)\s*$|(?:no|not|절대)\s*$")


def _negated(text: str, m: "re.Match") -> bool:
    after = text[m.end():m.end() + 12]
    before = text[max(0, m.start() - 4):m.start()]
    if _NEG_AFTER.match(after):
        return True
    if re.search(r"(?:비|무)\s*$", before) and m.group(0)[:1] not in "비무":
        return True
    return False


# code, severity, label, regex, scam, defect, positive-when-negated message
_FLAG_RULES = [
    ("mining", 2, "채굴 이력 언급", r"채굴|마이닝|광산|mining|코인\s*용", False, False, "채굴 이력 없다고 함 (확인 필요)"),
    ("refurb", 1, "리퍼/재생품", r"리퍼비시|리퍼|refurb|재생품|a/?s\s*교환품|as\s*교환품", False, False, None),
    ("boot_fail", 3, "부팅/작동 불량 언급", r"부팅\s*(?:이\s*)?(?:안\s?됨|안\s?돼|안\s?됩|불가|불량|안\s?되)|안\s?켜(?:짐|져|집)|전원\s*(?:이\s*)?(?:안\s*들어|안\s*켜|불량)|"
     r"화면\s*(?:이\s*)?(?:안\s*나|안\s*뜸|안\s*뜬)|무\s*화면|먹통|부품\s*용|수리\s*용|고장|불량품|작동\s*(?:이\s*)?(?:안|불)|불량", False, True, "불량/고장 없다고 함 (직접 확인 필요)"),
    ("unstable", 2, "불안정 증상 (블루스크린/재부팅/꺼짐/아티팩트 등)", r"(?:가끔|종종|간헐적으?로?)\s*부팅\s*(?:이\s*)?(?:안|실패)|부팅\s*(?:이\s*)?(?:가끔|종종|간헐적으?로?)\s*(?:안|실패)|블루\s*스크린|bsod|재부팅|랜덤\s*(?:으로\s*)?(?:꺼|종료)|갑자기\s*꺼|튕김|튕겨|꺼짐|아티팩트|화면\s*깨|줄\s*(?:이|가)\s*생|가끔\s*(?:꺼|튕|멈)|써멀\s*(?:교체\s*)?(?:필요|굳)", False, True, "불안정 증상 없다고 함"),
    ("psu_unknown", 2, "파워 출처 불명/무명", r"파워\s*(?:는\s*)?(?:무명|노네임|no\s*name|모름|잘\s*모르|불명|출처\s*불명|정체\s*불명)|무명\s*파워|노\s*네임\s*파워|정체\s*불명\s*파워|파워\s*(?:오래|구형)", False, False, None),
    ("cooler_noise", 1, "쿨러/팬 소음", r"(?:쿨러|팬|펌프|수랭|그래픽(?:\s*카드)?|본체)\s*(?:에서\s*)?(?:소음|소리|웅웅|떨림|시끄)|소음\s*(?:이\s*)?(?:있|좀|약간|심|조금|남)|코일\s*(?:울음|우는\s*소리)", False, False, "소음 없다고 함"),
    ("prepay_direct", 3, "직거래라면서 선입금/예약금 요구", r"선\s*입금|먼저\s*입금|입금\s*먼저|예약금|선금|계좌\s*이체\s*먼저|보증금\s*먼저", True, False, None),
    ("no_safe_pay", 2, "안전결제 거부", r"안전\s*(?:결제|거래|페이)\s*(?:불가|x|안\s?됨|안\s?받|안\s?함|거부|싫)|(?:네이버|네이버\s*페이|중고나라\s*안전)\s*(?:페이)?\s*불가|안전결제\s*수수료\s*(?:때문|싫)|수수료\s*아까", True, False, None),
    ("messenger", 1, "외부 메신저로 유도", r"(?:텔레그램|telegram|카톡|카카오톡|오픈\s*카톡|오픈\s*채팅|라인|위챗|왓츠앱|whatsapp)\s*(?:으로|로|id|아이디|연락|문의|주세요|\s*:)|(?:연락|문의|거래)\s*(?:는|은)?\s*(?:카톡|텔레그램)", True, False, None),
    ("external_link", 3, "외부 결제/입금 링크", r"(?:https?://|bit\.ly|me2\.do|url\.kr)\S*", True, False, None),
    ("too_good", 2, "'시세보다 너무 싸다' 식 과장 문구", r"시세\s*(?:보다|이하|대비)\s*(?:너무\s*)?(?:싸|저렴|낮|훨씬)|너무\s*싸|엄청\s*싸|파격|헐값|반값|최저가|손해\s*보고|출혈|떨이|무조건\s*이득", True, False, None),
    ("no_box", 1, "박스 없음", r"박스\s*(?:는\s*)?(?:없|x|분실|버렸)|박스x|풀박\s*(?:아님|x)|구성품\s*(?:없|x)|설명서\s*없", False, False, None),
    ("no_receipt", 1, "영수증/구매내역 없음", r"(?:영수증|구매\s*내역|구매내역|보증서)\s*(?:은\s*)?(?:없|분실|x|못)", False, False, None),
    ("urgent", 1, "급처 (가격 사유 확인)", r"급\s*처|급처분|급전|급하게|급히|오늘\s*만|빨리\s*(?:팔|처분)|이사\s*(?:가|때문|로\s*인해)", False, False, None),
    ("no_test", 2, "테스트/환불 불가 문구", r"테스트\s*(?:불가|못|안\s?해)|확인\s*(?:불가|못)|시연\s*(?:불가|안)|현상\s*그대로|as\s*is|노\s*클레임|환불\s*불가|교환\s*불가|반품\s*불가|사용\s*후\s*반품\s*불가", False, False, None),
    ("third_party", 2, "대리/지인 판매 (본인 소유 확인 필요)", r"대리\s*판매|대신\s*(?:팔|판매)|지인\s*(?:꺼|것|물건|부탁)|친구\s*(?:꺼|것|물건)", True, False, None),
    ("cosmetic", 1, "외관 흠집/냄새 등", r"기스|스크래치|흠집|찍힘|파손|금\s*가|변색|누렇|담배\s*(?:냄새|찌든)|흡연|냄새|애완|고양이|강아지", False, False, None),
    ("heavy_use", 1, "상시 구동/서버/렌더링 용도 사용", r"24\s*시간|상시\s*(?:켜|구동|가동)|서버\s*(?:용|로)|렌더링\s*용|렌더\s*팜", False, False, None),
]
_FLAG_RE = [(c, sev, label, re.compile(rx), scam, defect, pos) for c, sev, label, rx, scam, defect, pos in _FLAG_RULES]

_POSITIVE_RULES = [
    (r"풀박", "풀박스"),
    (r"(?:영수증|구매\s*내역|구매내역|정품\s*등록)\s*(?:있|가능|보유|드림|첨부|인증)", "영수증/구매내역 있음"),
    (r"시리얼\s*(?:번호\s*)?(?:확인|인증|가능)", "시리얼 확인 가능"),
    (r"벤치\s*(?:마크)?\s*(?:인증|가능|캡처|점수)|벤치\s*돌려|3dmark|cinebench|시네벤치", "벤치 인증 가능/제시"),
    (r"직거래\s*(?:시|때)?\s*(?:현장|확인|테스트|시연)|현장\s*(?:테스트|확인|시연)|테스트\s*가능|시연\s*가능", "현장 테스트 가능"),
    (r"안전\s*(?:결제|거래|페이)\s*(?:가능|ok|오케이|환영)", "안전결제 가능"),
    (r"(?:이상|하자|문제)\s*(?:전혀\s*)?(?:없|무)|정상\s*(?:작동|동작|사용)|풀\s*테스트\s*완료", "정상 작동 주장"),
    (r"금연|비흡연|펫\s*(?:없|프리)", "금연/펫 없음 주장"),
    (r"미개봉|새\s*제품|새제품", "미개봉/신품급 주장"),
]


def extract_flags(doc: Doc, p: Parsed) -> None:
    text = doc.text
    # 직거래 언급 여부 (prepay_direct 판정용)
    has_direct = bool(re.search(r"직거래|대면\s*거래|만나서", text))
    for code, sev, label, rx, scam, defect, pos_msg in _FLAG_RE:
        for m in rx.finditer(text):
            if _negated(text, m) and code not in ("external_link", "prepay_direct", "no_safe_pay", "messenger", "too_good"):
                if pos_msg and pos_msg not in p.positives:
                    p.positives.append(pos_msg)
                continue
            # 문맥 예외
            if code == "prepay_direct" and not has_direct:
                # 택배 거래의 일반적인 선입금은 위험 신호까지는 아님: 주의(2)로 낮춤
                p.add_flag(Flag("prepay", 2, "선입금/예약금 언급 (택배라면 안전결제 이용)", m.group(0), True, False))
                break
            if code == "mining" and re.search(r"비\s*채굴|무\s*채굴|채굴\s*(?:이력|흔적)?\s*(?:없|아님|x)|채굴\s*용?\s*(?:이?\s*)?아", text[max(0, m.start() - 3):m.end() + 12]):
                if "채굴 이력 없다고 함 (확인 필요)" not in p.positives:
                    p.positives.append("채굴 이력 없다고 함 (확인 필요)")
                break
            if code == "boot_fail" and m.group(0) == "불량" and re.search(r"불량\s*(?:없|x|이\s*없)|무\s*불량|불량\s*0", text[max(0, m.start() - 3):m.end() + 8]):
                continue
            if code == "external_link" and not re.search(r"결제|입금|예약|송금|계좌", text):
                # 단순 링크(사진 등)는 참고 정도
                p.add_flag(Flag("link", 1, "외부 링크 포함 (사진/상세 링크라도 클릭 주의)", m.group(0)[:40], False, False))
                break
            p.add_flag(Flag(code, sev, label, m.group(0).strip()[:40], scam, defect))
            break
    for rx, label in _POSITIVE_RULES:
        m = re.search(rx, text)
        if m and label not in p.positives:
            # 부정 표현 필터 ("풀박 아님")
            if label == "풀박스" and re.search(r"풀박\s*(?:아님|x)", text):
                continue
            p.positives.append(label)
