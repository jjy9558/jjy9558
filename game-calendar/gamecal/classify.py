"""키워드 규칙 기반 공지 분류 (영어 + 한국어)."""
import re

PATCH = "패치노트"
MAINT = "점검"
EVENT = "이벤트"
SHUTDOWN = "서비스 종료"
OTHER = "기타"
ALL = (PATCH, MAINT, EVENT, SHUTDOWN, OTHER)
ALARM_CATEGORIES = (MAINT, SHUTDOWN)

_I = re.I
_SHUTDOWN = re.compile(
    r"\b(?:shut\s?down|shutting\s+down|end[\s-]of[\s-]service|sunset(?:ting)?|delist(?:ed|ing)?|"
    r"discontinu\w+|end\s+of\s+life|servers?\s+(?:will\s+)?(?:be\s+)?(?:closed|closing)|final\s+day\s+of\s+service)\b"
    r"|서비스\s*(?:를\s*)?종료|서버\s*종료|운영\s*종료|종료\s*안내|판매\s*종료", _I)
_MAINT = re.compile(
    r"\b(?:maintenance|downtime|server\s+(?:restart|update)s?|scheduled\s+(?:restart|outage)|emergency\s+(?:fix|work))\b"
    r"|점검|서버\s*작업|긴급\s*작업", _I)
_PATCH_STRONG = re.compile(
    r"\b(?:patch\s*notes?|hot\s?fix(?:es)?|change\s?log|release\s+notes?)\b|패치\s*노트|핫픽스|변경\s*사항", _I)
_EVENT = re.compile(
    r"\b(?:events?|seasons?|giveaways?|festival|tournament|free\s+weekend|double\s+xp|community\s+day|"
    r"livestream|sale)\b|이벤트|시즌|축제|세일|무료\s*플레이|대회", _I)
_PATCH_WEAK = re.compile(r"\b(?:patch|update[sd]?|v?\d+\.\d+\.\d+)\b|패치|업데이트", _I)


def classify(title, body=""):
    t = title or ""
    if _SHUTDOWN.search(t):
        return SHUTDOWN
    if _MAINT.search(t):
        return MAINT
    if _PATCH_STRONG.search(t):
        return PATCH
    if _EVENT.search(t):
        return EVENT
    if _PATCH_WEAK.search(t):
        return PATCH
    head = (body or "")[:1500]
    if _SHUTDOWN.search(head):
        return SHUTDOWN
    if _MAINT.search(head):
        return MAINT
    return OTHER
