"""가명화 + 민감 주제 필터.

1단계 jjkit ``Redactor``(전화/이메일/경로/토큰 등) -> 2단계 이름·지명 가명화 -> 3단계 민감 주제 판정.
정규식 기반 best-effort 입니다. 확실하게 가리려면 ``--name``/``--place`` 로 실명을 알려 주세요.
가명화 뒤에도 원본 이름이 남아 있으면(``leaks``) 그 카드는 후보에서 빠집니다.
"""
from __future__ import annotations

import random
import re

from ._jjkit.redact import Redactor

PSEUDO_NAMES = ["가온", "나래", "다솜", "라온", "마루", "보람", "새봄", "아름", "온유", "하늘", "두리", "소담"]
BIG_PLACES = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "수원", "성남", "고양", "용인",
              "창원", "청주", "전주", "천안", "제주", "강남", "홍대", "판교", "분당", "일산", "송도"]
PLACE_PLACEHOLDER = "○○"

# 호칭이 붙은 이름: 홍길동 님/씨 등. 일반명사는 제외.
_NOT_NAME = {"고객", "선생", "사장", "부장", "팀장", "과장", "차장", "대리", "이사", "교수", "주인", "하느", "부모",
             "어머", "아버", "시청자", "구독자", "여러분", "회원", "독자", "손", "신", "형", "누나", "오빠", "언니",
             "할머", "할아버", "아저", "아줌", "이웃", "아가", "친구"}
_SURNAMES = ("김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민나진지엄채원천"
             "방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부가")
_AFTER = r"(?=[은는이가을를의도과와께만]|[^가-힣]|$)"
_TITLED_SSI = re.compile(r"(?<![가-힣])([가-힣]{2,3})\s?씨" + _AFTER)
_TITLED_NIM = re.compile(r"(?<![가-힣])([%s][가-힣]{2})\s?(?:님|군|양)" % _SURNAMES + _AFTER)
_PLACE_RE = re.compile(r"(?<![가-힣])([가-힣]{2,6}(?:초등학교|중학교|고등학교|대학교|아파트))(?![가-힣]{3,})")

# ---------------------------------------------------------------- 민감 주제
_PARTICLE = r"(?:[이가을를은는의과와도에로]|에게|한테|이랑|이라)?"


def _term(words: str) -> str:
    return r"(?<![가-힣])(?:%s)%s(?![가-힣])" % (words, _PARTICLE)


_MEDICAL = re.compile("|".join([
    r"우울증|불안장애|공황장애|공황|조현|정신과|정신병|상담치료|항우울|수면제|자살|자해|죽고\s?싶|극단적\s?선택",
    r"진단\s?(?:받|을|서)|처방|복용|입원|퇴원|수술|응급실|병원|치료|재활|질환|증상|통증|골절|임신|유산|당뇨|고혈압|adhd|ADHD",
    _term(r"(?:유방|위|대장|폐|간|갑상선|췌장|전립선)?암"),
]))
_MINOR = re.compile("|".join([
    r"초등학생|중학생|고등학생|미성년|청소년|어린이집|유치원|초딩|중딩|고딩|신생아|영유아|초등학교|중학교|고등학교|교복|학부모|담임",
    _term(r"아기|애기|갓난아기"),
    r"\d{1,2}\s?학년",
]))
_AGE = re.compile(r"(?<![\d.])(\d{1,2})\s?(?:살|세)(?![가-힣]{2,})")
_DEFAME = re.compile("|".join([
    r"사기꾼|쓰레기|먹튀|불륜|바람\s?(?:피|났|난)|폭행|폭력|성추행|성희롱|갑질|도둑|거짓말쟁이|병신|미친\s?(?:놈|년)|양아치",
    r"범죄자|스토커|횡령|뒷담|저격|개새|나쁜\s?(?:놈|년)",
]))
_RELATION = re.compile("|".join([
    r"전\s?(?:남친|여친|애인|남편|아내|와이프|남자친구|여자친구)|상사|사장|팀장|부장|과장|동료|선배|후배|룸메|이웃",
    r"시어머니|장모|시누이|올케|집주인|사수|교수님|담당자",
]))


def minor_hit(text: str) -> bool:
    if _MINOR.search(text):
        return True
    return any(int(m.group(1)) < 19 for m in _AGE.finditer(text))


def sensitive_reasons(text: str, known_names=()) -> list[str]:
    """['defamation','medical','minor'] 중 해당하는 것. 가명화 전 원문에도, 후에도 쓴다."""
    reasons = []
    low = text
    has_person = bool(_RELATION.search(low)) or bool(_TITLED_SSI.search(low)) or bool(_TITLED_NIM.search(low)) \
        or any(n and n in low for n in known_names)
    if _DEFAME.search(low) and has_person:
        reasons.append("defamation")
    if _MEDICAL.search(low):
        reasons.append("medical")
    if minor_hit(low):
        reasons.append("minor")
    return reasons


REASON_LABELS = {"defamation": "실존 인물 비방 우려", "medical": "의료·건강 주제", "minor": "미성년자 단서",
                 "leak": "가명화 후에도 실명 잔존"}


class Scrubber:
    def __init__(self, names=(), places=(), seed: int = 0):
        self.names = [n.strip() for n in names if n and len(n.strip()) >= 2]
        self.places = [p.strip() for p in places if p and len(p.strip()) >= 2]
        self.seed = seed
        self._map: dict[str, str] = {}
        self._alias: dict[str, str] = {}
        self._originals: set[str] = set()
        order = list(PSEUDO_NAMES)
        random.Random(seed).shuffle(order)
        self._pool = order
        self._redactor = Redactor()
        for n in self.names:
            self._add_original(n)
        for p in self.places:
            self._originals.add(p)

    # -- 원본 이름 관리 ------------------------------------------------------
    def _add_original(self, name: str):
        self._originals.add(name)
        if len(name) == 3 and re.fullmatch(r"[가-힣]{3}", name):
            self._originals.add(name[1:])      # 성을 뗀 이름도 같은 사람
            self._alias.setdefault(name[1:], name)

    @property
    def originals(self) -> set[str]:
        return set(self._originals)

    def _detect(self, text: str) -> None:
        for rx in (_TITLED_SSI, _TITLED_NIM):
            for m in rx.finditer(text):
                cand = m.group(1)
                if cand in _NOT_NAME or any(cand.startswith(x) and len(x) >= 2 for x in _NOT_NAME):
                    continue
                self._add_original(cand)
        for m in _PLACE_RE.finditer(text):
            self._originals.add(m.group(1))

    def _pseudo_for(self, name: str) -> str:
        canon = self._alias.get(name, name)      # 성을 뗀 이름은 전체 이름과 같은 가명
        if canon in self._map:
            return self._map[canon]
        for cand in self._pool:
            if any(o in cand or cand in o for o in self._originals):
                continue
            if cand in self._map.values():
                continue
            self._map[canon] = cand
            return cand
        ps = "사람%d" % (len(self._map) + 1)
        self._map[canon] = ps
        return ps

    def learn(self, text: str) -> None:
        """호칭이 붙은 이름/학교·아파트 이름을 미리 인식해 둔다(민감 주제 판정 전에 호출)."""
        self._detect(text)

    # -- 본 처리 -------------------------------------------------------------
    def scrub(self, text: str) -> str:
        if not text:
            return text
        self._detect(text)
        out = self._redactor.redact(text)
        names = sorted((o for o in self._originals if o not in self.places and not _is_place_like(o)),
                       key=lambda s: (-len(s), s))
        # 긴 이름(전체 이름)부터 치환해 같은 가명이 부분 치환에 깨지지 않게 한다
        for n in names:
            if n in out:
                out = out.replace(n, self._pseudo_for(n))
        for p in sorted(set(self.places) | {o for o in self._originals if _is_place_like(o)} | set(BIG_PLACES),
                        key=lambda s: (-len(s), s)):
            out = out.replace(p, PLACE_PLACEHOLDER)
        return out

    def leaks(self, text: str) -> list[str]:
        return sorted(o for o in self._originals if o and o in text)


def _is_place_like(s: str) -> bool:
    return bool(re.search(r"(?:초등학교|중학교|고등학교|대학교|아파트)$", s))
