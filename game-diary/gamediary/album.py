"""정적 HTML 앨범 생성. 이미지는 상대경로(또는 file://)로만 참조한다."""
import base64
import html
import json
import os
import re

from .diary import image_ref
from .sources import mime_for

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "album_template.html")


def _safe_json(obj):
    s = json.dumps(obj, ensure_ascii=False)
    return (s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def thumb_data_uri(path):
    with open(path, "rb") as f:
        return "data:%s;base64,%s" % (mime_for(path), base64.b64encode(f.read()).decode("ascii"))


def build_items(shots, out_dir, embed_thumbs=False, max_thumb_bytes=300000):
    items = []
    for s in shots:
        it = {"src": image_ref(s["path"], out_dir), "game": s["game"],
              "ts": s["ts"].strftime("%Y-%m-%d %H:%M:%S"), "caption": s.get("caption", ""),
              "mood": s.get("mood", ""), "tags": s.get("tags", []),
              "notable": bool(s.get("notable")), "w": s.get("width"), "h": s.get("height")}
        if embed_thumbs and s.get("thumb") and os.path.isfile(s["thumb"]) \
                and os.path.getsize(s["thumb"]) <= max_thumb_bytes:
            it["thumb"] = thumb_data_uri(s["thumb"])
        items.append(it)
    return items


def render_album(shots, out_dir, title="게임 앨범", embed_thumbs=False):
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        tpl = f.read()
    items = build_items(shots, out_dir, embed_thumbs)
    vals = {"TITLE": html.escape(title), "COUNT": str(len(items)), "DATA": _safe_json(items)}
    return re.sub(r"\{\{(TITLE|COUNT|DATA)\}\}", lambda m: vals[m.group(1)], tpl)
