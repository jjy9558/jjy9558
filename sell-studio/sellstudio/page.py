"""혼자 완결되는 상세페이지 HTML. CSS는 인라인, 스크립트·외부 주소 없음, 이미지는 상대경로."""
from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import quote

from . import markets as mk

CSS = """
:root{--bg:#fff;--fg:#1d2330;--mut:#5b6475;--line:#e3e6ee;--card:#f6f7fb;--warn:#fff4e5;--warnfg:#8a4b00}
@media (prefers-color-scheme:dark){:root{--bg:#14171f;--fg:#e7eaf2;--mut:#a3abbd;--line:#2a3040;--card:#1c212c;--warn:#3a2c14;--warnfg:#ffd699}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.65 -apple-system,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif}
main{max-width:760px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.5rem;line-height:1.35;margin:.2em 0 .3em}
h2{font-size:1.1rem;margin:1.8em 0 .5em;padding-bottom:.3em;border-bottom:2px solid var(--line)}
.price{font-size:1.3rem;font-weight:700}
.lead{color:var(--mut)}
table{width:100%;border-collapse:collapse;margin:.4em 0}
th,td{text-align:left;padding:.55em .7em;border-bottom:1px solid var(--line);vertical-align:top}
th{width:7.5em;color:var(--mut);font-weight:600;background:var(--card)}
figure{margin:1em 0}
img{display:block;width:100%;height:auto;border-radius:8px;background:var(--card)}
figcaption{color:var(--mut);font-size:.9rem;margin-top:.35em}
.note{background:var(--warn);color:var(--warnfg);padding:.7em .9em;border-radius:8px}
ul{padding-left:1.2em}
footer{margin-top:2.5em;color:var(--mut);font-size:.85rem}
"""


def _e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def choose_photo_dir(root: Path, data: dict) -> tuple[str, bool]:
    """(상대 폴더 이름, 정리본 사용 여부). 정리본(photos_clean)이 모든 사진에 대해 있을 때만 쓴다."""
    clean = Path(root) / "photos_clean"
    files = [p["file"] for p in data.get("photos", [])]
    if files and all((clean / f).is_file() for f in files):
        return "photos_clean", True
    return "photos", False


def build_page(root: Path, data: dict, price: int | None = None) -> tuple[str, list[str]]:
    v = mk.item_values(data)
    warns: list[str] = []
    price = mk.suggested_price(data, price)
    folder, clean = choose_photo_dir(root, data)
    if not clean:
        warns.append("위치 정보를 뺀 사진(photos_clean)이 없어 원본 사본(photos)을 참조합니다. `privacy-check --strip` 을 먼저 하세요")
    name = v["name"] or "[상품명 입력]"
    title = " ".join(x for x in (name, v["spec"]) if x)
    rows = []
    for label, val in (("상품명", name), ("사양", v["spec"]), ("상태", v["condition"] or "[직접 입력]"),
                       ("구성품", ", ".join(v["included"]) or "[직접 입력]"),
                       ("거래 방법", v["trade"] or "[직접 입력]")):
        if val:
            rows.append("<tr><th>%s</th><td>%s</td></tr>" % (_e(label), _e(val)))
    defects = v["defects"]
    if defects:
        defect_html = "<ul>%s</ul>" % "".join("<li>%s</li>" % _e(d) for d in defects)
    elif v["defects_guess"]:
        defect_html = '<p class="note">[사진 분석 참고용: %s - 실물 확인 후 직접 입력]</p>' % _e(", ".join(v["defects_guess"]))
    else:
        defect_html = '<p class="note">[하자 여부를 직접 입력하세요]</p>'
    figs = []
    for i, ph in enumerate(data.get("photos", []), 1):
        src = "%s/%s" % (folder, quote(ph["file"]))
        cap = ph.get("caption") or "사진 %d" % i
        loading = "" if i == 1 else ' loading="lazy"'
        figs.append('<figure><img src="%s" alt="%s"%s><figcaption>%s</figcaption></figure>'
                    % (src, _e(cap), loading, _e(cap)))
    price_html = '<div class="price">%s</div>' % _e(mk.fmt_won(price)) if price is not None else ""
    notes = '<p>%s</p>' % _e(v["notes"]) if v["notes"] else ""
    doc = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<style>%(css)s</style>
</head>
<body>
<main>
<h1>%(title)s</h1>
%(price)s
<p class="lead">중고 상품입니다. 아래 사진과 상태 설명을 확인해 주세요.</p>
<h2>상품 정보</h2>
<table>
%(rows)s
</table>
<h2>하자 및 특이사항</h2>
%(defects)s
%(notes)s
<h2>사진</h2>
%(figs)s
<h2>거래 안내</h2>
<ul>
<li>문의와 연락은 플랫폼 채팅으로 부탁드립니다.</li>
<li>실물 확인 후 거래하시는 것을 권합니다.</li>
</ul>
<footer>이 페이지는 판매자가 직접 올리는 중고 상품 안내입니다.</footer>
</main>
</body>
</html>
""" % {"title": _e(title), "css": CSS, "price": price_html, "rows": "\n".join(rows),
       "defects": defect_html, "notes": notes, "figs": "\n".join(figs) or "<p>[사진 없음]</p>"}
    return doc, warns


def self_contained_problems(doc: str) -> list[str]:
    """외부 의존이 있는지 거칠게 검사 (테스트와 --check 용)."""
    import re
    probs = []
    low = doc.lower()
    if "<script" in low:
        probs.append("script 태그")
    if "<link" in low:
        probs.append("link 태그")
    if re.search(r"(?:src|href)\s*=\s*[\"'](?:https?:)?//", low):
        probs.append("외부 src/href")
    if re.search(r"url\(\s*[\"']?(?:https?:)?//", low) or "@import" in low:
        probs.append("CSS 외부 참조")
    for m in re.finditer(r'<img[^>]*\ssrc="([^"]*)"', doc):
        s = m.group(1)
        if s.startswith(("/", "http", "data:", "file:")) or ":" in s.split("/")[0]:
            probs.append("상대경로가 아닌 이미지: " + s)
    return probs
