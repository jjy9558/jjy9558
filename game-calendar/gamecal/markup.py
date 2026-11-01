"""Steam BBCode / 공지 마크업을 일반 텍스트로 변환."""
import html
import re

_TAGS = (r"h[1-6]|b|i|u|s|strike|url|img|list|olist|quote|code|spoiler|noparse|table|tr|th|td|hr|p|"
         r"previewyoutube|carousel|expand|dynamiclink|video|center|left|right|sup|sub|tooltip|url")

_BLOCK_RE = [
    (re.compile(r"\[img[^\]]*\].*?\[/img\]", re.I | re.S), ""),
    (re.compile(r"\[previewyoutube[^\]]*\].*?\[/previewyoutube\]", re.I | re.S), ""),
    (re.compile(r"\[carousel[^\]]*\].*?\[/carousel\]", re.I | re.S), ""),
    (re.compile(r"\[video[^\]]*\].*?\[/video\]", re.I | re.S), ""),
]
_URL_RE = re.compile(r"\[url=([^\]]*)\](.*?)\[/url\]", re.I | re.S)
_HEADING_CLOSE = re.compile(r"\[/h[1-6]\]", re.I)
_HEADING_OPEN = re.compile(r"\[h[1-6]\]", re.I)
_BULLET = re.compile(r"\[\*\]")
_BR_TAGS = re.compile(r"\[/?(?:list|olist|table|tr|quote|p)\]|\[hr\]|\[/(?:th|td)\]", re.I)
_ANY_TAG = re.compile(r"\[/?(?:%s)(?:=[^\]]*)?\]" % _TAGS, re.I)
_STEAM_VAR = re.compile(r"\{STEAM_CLAN_(?:LOC_)?IMAGE\}\S*", re.I)
_HTML_TAG = re.compile(r"</?(?:br|p|b|i|u|a|img|div|span|ul|ol|li|h[1-6])\b[^>]*>", re.I)
_BARE_URL_IMG = re.compile(r"https?://\S+\.(?:png|jpe?g|gif|webp)\S*", re.I)


def strip_markup(text):
    if not text:
        return ""
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    for rx, rep in _BLOCK_RE:
        s = rx.sub(rep, s)
    s = _STEAM_VAR.sub("", s)
    s = _URL_RE.sub(lambda m: m.group(2).strip() or m.group(1), s)
    s = _HEADING_OPEN.sub("\n", s)
    s = _HEADING_CLOSE.sub("\n", s)
    s = _BULLET.sub("\n- ", s)
    s = re.sub(r"\[/?\*\]", "", s)
    s = _BR_TAGS.sub("\n", s)
    s = re.sub(r"\[(?:th|td)\]", " ", s, flags=re.I)
    s = _ANY_TAG.sub("", s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = _HTML_TAG.sub("", s)
    s = _BARE_URL_IMG.sub("", s)
    s = html.unescape(s)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    out = []
    blank = 0
    for ln in lines:
        if not ln or ln == "-":
            blank += 1
            if blank == 1 and out:
                out.append("")
            continue
        blank = 0
        out.append(ln)
    return "\n".join(out).strip()
