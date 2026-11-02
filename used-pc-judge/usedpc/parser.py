"""매물 글 파서 진입점: parse_listing(text) -> Parsed."""

from . import listing_info, parts_parser
from .aliases import Doc, normalize
from .models import Parsed


def parse_listing(text: str, today=None, llm=None) -> Parsed:
    """규칙 기반 파싱. llm 클라이언트를 주면 빈칸/저신뢰 필드만 LLM으로 보충해 병합한다."""
    today_d = listing_info._parse_today(today)
    norm = normalize(text)
    doc = Doc(norm)
    first = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    p = Parsed(text=text, title=first, today=today_d.isoformat())

    parts_parser.extract_exclusions(doc, p)
    listing_info.extract_price(doc, p)
    parts_parser.extract_cpu(doc, p)
    parts_parser.extract_gpu(doc, p)
    parts_parser.extract_ram(doc, p)
    parts_parser.extract_storage(doc, p)
    parts_parser.extract_psu(doc, p)
    parts_parser.extract_board(doc, p)
    parts_parser.extract_bare_numbers(doc, p)
    parts_parser.bare_ram_guess(doc, p)
    parts_parser.extract_case(doc, p)
    parts_parser.extract_cooler(doc, p)
    parts_parser.extract_monitor(doc, p)
    parts_parser.extract_peripherals(doc, p)
    listing_info.extract_warranty(doc, p, today_d)
    listing_info.extract_age(doc, p, today_d)
    listing_info.extract_flags(doc, p)
    if llm is not None:
        from . import llm as llm_mod
        llm_mod.fill_gaps(p, llm)
    listing_info.classify_kind(doc, p)
    return p


def split_listings(text: str):
    """'---' 한 줄로 구분된 여러 매물 텍스트를 나눈다."""
    import re
    parts = re.split(r"(?m)^\s*-{3,}\s*$", text)
    return [x.strip("\n") for x in parts if x.strip()]
