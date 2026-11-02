"""한국 중고 시장 은어/약어 테이블과 텍스트 정규화.

규칙 + 별칭 테이블 방식이다. 새 은어를 만나면 TEXT_ALIASES 에 한 줄만 추가하면 된다.

해석이 확실하지 않은 은어 (README 에도 적어 둠):
 - 사테 / 사타 / 새타 -> SATA
 - 삼실 -> 삼성 SSD
 - 육사공공 같은 "한글 숫자 읽기" -> 6400 (RAM 클럭 또는 RX 6400)
 - 박격포 -> MSI MAG Mortar 보드
"""

import re
import unicodedata
from typing import List, Tuple

from . import catalog

# 순서가 중요하다 (위에서 아래로 순차 치환). 모두 소문자 기준.
TEXT_ALIASES: List[Tuple[str, str]] = [
    # 단위
    (r"(\d)\s*기가\s*바이트", r"\1gb"),
    (r"기가\s*바이트", "gigabyte"),
    (r"(\d)\s*기가", r"\1gb"),
    (r"(\d)\s*테라\s*바이트", r"\1tb"),
    (r"(\d)\s*테라", r"\1tb"),
    (r"(\d)\s*메가\s*헤르츠", r"\1mhz"),
    # GPU 표기
    (r"알티엑스|알티엑|알텍스|알티x", "rtx"),
    (r"지티엑스|쥐티엑스", "gtx"),
    (r"지포스|제포스", "geforce"),
    (r"라데온|레이데온", "radeon"),
    (r"알엑스", "rx"),
    (r"엔비디아|엔비디어", "nvidia"),
    (r"티아이(?=[^가-힣]|$)", "ti"),
    (r"슈퍼|수퍼|슈펴", "super"),
    (r"엑스티엑스", "xtx"),
    (r"엑스티(?=[^가-힣]|$)", "xt"),
    # CPU 표기
    (r"라이젠|라이즌|라이센", "ryzen"),
    (r"울트라", "ultra"),
    (r"코어", "core"),
    (r"인텔", "intel"),
    (r"에이엠디", "amd"),
    # 저장장치/메모리 은어
    (r"삼실", "삼성 ssd"),
    (r"사테|사타|새타|세타(?=\s*ssd)", "sata"),
    (r"엔비엠이|엔브이엠이", "nvme"),
    (r"엠투|엠\.2", "m.2"),
    (r"에스에스디", "ssd"),
    (r"에이치디디", "hdd"),
    (r"디디알\s*([345])", r"ddr\1"),
    (r"디램", "dram"),
    # 숫자 뒤에 붙은 한글 알파벳 읽기 (12400에프, 13600케이)
    (r"(?<=\d)\s*케이\s*에프(?![가-힣])", "kf"),
    (r"(?<=\d)\s*에프(?![가-힣])", "f"),
    (r"(?<=\d)\s*케이(?![가-힣])", "k"),
    # 상태 표현
    (r"풀박스|풀 박스", "풀박"),
]

_SINO = {"공": "0", "영": "0", "일": "1", "이": "2", "삼": "3", "사": "4",
         "오": "5", "육": "6", "륙": "6", "칠": "7", "팔": "8", "구": "9"}
_SINO_CLASS = "공영일이삼사오육륙칠팔구"
_SINO_RE = re.compile(r"(?<![가-힣])([%s]{4,5})(?![%s])" % (_SINO_CLASS, _SINO_CLASS))


def convert_sino_digits(text: str) -> str:
    """'삼공육공' -> '3060', '육사공공' -> '6400'. 알려진 부품 번호일 때만 치환한다."""
    def repl(m):
        digits = "".join(_SINO[c] for c in m.group(1))
        if digits in catalog.KNOWN_NUMBERS:
            return " " + digits + " "
        return m.group(0)
    return _SINO_RE.sub(repl, text)


def normalize(text: str) -> str:
    """NFKC + 소문자 + 공백 정리 + 은어 치환."""
    t = unicodedata.normalize("NFKC", text or "")
    t = t.replace("​", "").replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    t = t.lower()
    t = re.sub(r"[ \t ]+", " ", t)
    t = convert_sino_digits(t)
    for pat, rep in TEXT_ALIASES:
        t = re.sub(pat, rep, t)
    # 한글 숫자 변환 후 생긴 이중 공백 정리
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t


class Doc:
    """정규화된 텍스트 + '이미 해석한 구간' 마스크.

    앞 단계에서 소비한 구간(예: RAM 용량)을 가려서, 뒤 단계(저장장치/GPU 숫자)가
    같은 숫자를 다시 해석하지 않게 한다.
    """

    def __init__(self, text: str):
        self.text = text
        self._mask = list(text)

    @property
    def masked(self) -> str:
        return "".join(self._mask)

    def finditer(self, pattern, flags: int = 0):
        pat = re.compile(pattern, flags) if isinstance(pattern, str) else pattern
        return list(pat.finditer(self.masked))

    def search(self, pattern, flags: int = 0):
        pat = re.compile(pattern, flags) if isinstance(pattern, str) else pattern
        return pat.search(self.masked)

    def claim(self, start: int, end: int) -> None:
        for i in range(start, min(end, len(self._mask))):
            self._mask[i] = "\x00"

    def window(self, start: int, end: int, before: int = 12, after: int = 12) -> str:
        """마스크와 무관한 원문 주변 텍스트 (문맥 판단용)."""
        return self.text[max(0, start - before):min(len(self.text), end + after)]

    @property
    def title(self) -> str:
        for line in self.text.split("\n"):
            if line.strip():
                return line.strip()
        return ""
