"""작은 한국어/영어 동의어 표와 텍스트 정규화, 문자 bigram 도구."""
import re

# 각 줄의 첫 항목이 대표어
GROUPS = [
    ("리모컨", "리모콘", "리모트", "리모컨", "remote", "remocon", "tv리모컨", "티비리모컨", "에어컨리모컨", "셋톱리모컨"),
    ("지갑", "wallet", "카드지갑", "장지갑", "반지갑"),
    ("열쇠", "키", "key", "keys", "차키", "자동차키", "집열쇠", "현관키", "도어락키", "스마트키"),
    ("택배", "택배상자", "택배박스", "상자", "박스", "소포", "box", "package", "parcel", "배송상자"),
    ("휴대폰", "핸드폰", "폰", "스마트폰", "전화기", "phone", "cellphone", "mobile"),
    ("안경", "glasses", "안경테", "선글라스"),
    ("이어폰", "에어팟", "버즈", "이어버드", "earbuds", "earphone", "earphones", "airpods"),
    ("헤드폰", "헤드셋", "headphone", "headphones", "headset"),
    ("충전기", "충전케이블", "케이블", "어댑터", "charger", "cable", "충전선"),
    ("마우스", "mouse"),
    ("키보드", "keyboard"),
    ("가방", "백팩", "bag", "backpack", "핸드백", "에코백"),
    ("우산", "umbrella", "장우산", "양산"),
    ("노트북", "랩탑", "laptop", "맥북", "notebook"),
    ("책", "book", "도서", "책자"),
    ("컵", "머그컵", "텀블러", "mug", "cup", "머그", "물컵"),
    ("가위", "scissors"),
    ("약", "약통", "medicine", "알약"),
    ("시계", "watch", "손목시계", "스마트워치"),
    ("마스크", "mask"),
    ("모자", "hat", "cap", "캡모자"),
    ("카드", "신용카드", "체크카드", "교통카드", "card"),
    ("펜", "볼펜", "pen", "연필"),
    ("물병", "water bottle", "bottle", "보틀"),
]

_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


def norm(s):
    """소문자 + 공백/기호 제거."""
    return _PUNCT.sub("", (s or "").lower())


_INDEX = {}      # norm(term) -> canonical
_MEMBERS = {}    # canonical -> [terms]
for _g in GROUPS:
    for _t in _g:
        _INDEX.setdefault(norm(_t), _g[0])
    _MEMBERS[_g[0]] = list(_g)


def vocabulary():
    return set(_INDEX)


def canonical(text):
    """text 가 속한 동의어 그룹 대표어 (없으면 None).

    정확 일치를 우선하고, 없으면 2자 이상 동의어가 포함된 가장 긴 것을 씁니다
    (예: '택배 상자'→택배, '거실 리모컨'→리모컨). 1자 동의어('키','책','약','펜','폰')는 정확 일치만.
    """
    n = norm(text)
    if not n:
        return None
    if n in _INDEX:
        return _INDEX[n]
    best = None
    for term, canon in _INDEX.items():
        if len(term) >= 2 and term in n and (best is None or len(term) > len(best[0])):
            best = (term, canon)
    return best[1] if best else None


def expand(text):
    """text 와 같은 그룹의 모든 표현 (text 자신 포함)."""
    c = canonical(text)
    out = [text] if text else []
    if c:
        for t in _MEMBERS[c]:
            if t not in out:
                out.append(t)
    return out


def grams(text):
    """색인용 문자 bigram (1글자는 그대로)."""
    n = norm(text)
    if not n:
        return []
    if len(n) == 1:
        return [n]
    return [n[i:i + 2] for i in range(len(n) - 1)]
