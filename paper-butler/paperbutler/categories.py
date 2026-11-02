"""가맹점 이름 기반 규칙 카테고리 (식비/교통/구독/쇼핑/공과금/의료/문화/기타)."""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_CATEGORY = "기타"

# 앞에 있을수록 우선. (예: 쿠팡이츠는 식비, 쿠팡와우는 구독, 쿠팡은 쇼핑)
CATEGORY_RULES: List[Tuple[str, List[str]]] = [
    ("구독", ["넷플릭스", "netflix", "유튜브프리미엄", "youtube premium", "유튜브 프리미엄", "스포티파이", "spotify",
             "멜론", "왓챠", "티빙", "웨이브", "wavve", "디즈니플러스", "디즈니+", "쿠팡와우", "와우멤버십",
             "네이버플러스", "네이버멤버십", "멤버십", "정기결제", "정기구독", "구독", "chatgpt", "openai",
             "claude", "anthropic", "github", "notion", "icloud", "apple.com/bill", "google storage",
             "google one", "밀리의서재", "리디셀렉트", "지니뮤직", "플로", "라프텔"]),
    ("식비", ["쿠팡이츠", "배달의민족", "배민", "요기요", "스타벅스", "투썸", "이디야", "메가커피", "메가mgc", "컴포즈",
             "빽다방", "할리스", "커피", "카페", "맥도날드", "롯데리아", "버거킹", "kfc", "맘스터치", "서브웨이",
             "파리바게뜨", "파리바게트", "뚜레쥬르", "베이커리", "치킨", "피자", "김밥", "분식", "식당", "한식",
             "중식", "일식", "횟집", "고깃집", "삼겹살", "국밥", "냉면", "곱창", "족발", "보쌈", "도시락", "푸드",
             "gs25", "cu", "세븐일레븐", "이마트24", "미니스톱", "편의점", "이마트", "홈플러스", "롯데마트",
             "하나로마트", "하나로클럽", "농협마트", "트레이더스", "마트", "식자재", "정육", "수산", "과일",
             "컬리", "oasis", "오아시스"]),
    ("교통", ["택시", "카카오t", "카카오 t", "타다", "우티", "티머니", "t-money", "tmoney", "캐시비", "코레일",
             "ktx", "srt", "지하철", "서울교통공사", "버스", "고속버스", "시외버스", "하이패스", "도로공사",
             "주유", "gs칼텍스", "sk에너지", "s-oil", "에쓰오일", "현대오일뱅크", "알뜰주유", "주차", "파킹",
             "쏘카", "그린카", "따릉이", "항공", "대한항공", "아시아나", "진에어", "티웨이", "제주항공", "에어부산",
             "충전소", "세차"]),
    ("의료", ["병원", "의원", "약국", "치과", "한의원", "클리닉", "의료", "안과", "피부과", "정형외과", "내과",
             "소아과", "이비인후과", "검진", "메디"]),
    ("공과금", ["한국전력", "한전", "도시가스", "가스공사", "수도사업", "상수도", "관리비", "국민연금", "건강보험",
              "통신요금", "sk텔레콤", "skt", "lg유플러스", "lgu+", "u+", "kt", "알뜰폰", "지방세", "자동차세",
              "재산세", "주민세", "과태료", "세금", "국세", "보험료", "인터넷요금", "전기요금", "가스요금",
              "수도요금"]),
    ("문화", ["영화", "cgv", "메가박스", "롯데시네마", "서점", "교보문고", "영풍문고", "예스24", "yes24", "알라딘",
             "노래방", "공연", "콘서트", "티켓", "인터파크티켓", "멜론티켓", "전시", "박물관", "미술관", "pc방",
             "게임", "steam", "스팀", "닌텐도", "플레이스테이션", "볼링", "스크린골프", "헬스", "필라테스", "요가"]),
    ("교육", ["학원", "교육", "인강", "클래스", "아카데미", "스터디", "강의"]),
    ("쇼핑", ["쿠팡", "11번가", "지마켓", "g마켓", "옥션", "ssg", "쓱닷컴", "무신사", "올리브영", "다이소",
             "네이버쇼핑", "네이버페이", "위메프", "티몬", "인터파크", "알리익스프레스", "aliexpress", "테무",
             "temu", "백화점", "아울렛", "유니클로", "자라", "zara", "h&m", "이케아", "ikea", "하이마트",
             "전자랜드", "롯데온", "현대백화점", "신세계", "당근", "번개장터", "에이블리", "지그재그", "아마존",
             "amazon"]),
]

_ASCII_ONLY = re.compile(r"^[\x20-\x7e]+$")


def _compile(rules: List[Tuple[str, List[str]]]) -> List[Tuple[str, "re.Pattern[str]"]]:
    out = []
    for cat, kws in rules:
        parts = []
        for kw in kws:
            esc = re.escape(kw.lower())
            if _ASCII_ONLY.match(kw) and kw[0].isalnum():
                # 영문 키워드는 다른 영문자에 붙어 있으면 불일치 (예: 'cu' vs 'cube')
                esc = r"(?<![a-z0-9])" + esc + r"(?![a-z0-9])"
            parts.append(esc)
        out.append((cat, re.compile("|".join(parts))))
    return out


_COMPILED = _compile(CATEGORY_RULES)


def categorize(merchant: str, extra_rules: Optional[Dict[str, Iterable[str]]] = None) -> str:
    """가맹점 이름 -> 카테고리. extra_rules 가 있으면 먼저 검사."""
    if not merchant:
        return DEFAULT_CATEGORY
    name = merchant.lower()
    if extra_rules:
        for cat, kws in _compile([(c, list(k)) for c, k in extra_rules.items()]):
            if kws.search(name):
                return cat
    for cat, rx in _COMPILED:
        if rx.search(name):
            return cat
    return DEFAULT_CATEGORY


ONLINE_MERCHANTS = ["쿠팡", "11번가", "지마켓", "g마켓", "옥션", "ssg", "쓱닷컴", "무신사", "올리브영온라인",
                    "네이버쇼핑", "위메프", "티몬", "컬리", "인터파크", "알리익스프레스", "테무", "롯데온",
                    "에이블리", "지그재그", "아마존", "스마트스토어", "카카오톡 선물하기", "오늘의집", "ohou"]


def is_online_merchant(merchant: str) -> bool:
    m = (merchant or "").lower()
    return any(k in m for k in ONLINE_MERCHANTS)
