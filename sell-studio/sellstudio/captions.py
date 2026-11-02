"""사진 캡션: 로컬 VLM(jjkit llm vision) 또는 `--no-llm` 템플릿.

VLM 주소는 로컬/사설망만 허용된다(jjkit). 사진 바이트는 보내기 전에 APP1(EXIF/GPS)을 뺀다.
"""
from __future__ import annotations

import collections
from pathlib import Path

from . import jpegmeta
from ._jjkit import llm

PROMPT = (
    "중고 판매용 물품 사진입니다. 사진에 실제로 보이는 것만 한국어로 적으세요. 보이지 않는 사양·상태는 지어내지 마세요. "
    "사람 얼굴이나 신원은 적지 마세요. 택배 송장, 주소, 전화번호, 이름표, 영수증, 카드, 신분증처럼 개인정보로 보이는 것이 "
    "사진에 있으면 privacy_flags 에 짧게 적으세요.\n"
    "JSON 하나만 출력하세요(코드블록·설명 금지):\n"
    '{"item":"물품 종류와 모델(모르면 종류만)","color":"","condition":"겉보기 상태 한 줄","defects":["보이는 흠집·파손"],'
    '"visible_text":["제품에 인쇄된 모델명 등"],"caption":"사진 설명 한 문장","privacy_flags":[]}'
)

CAPTION_FIELDS = ("item", "color", "condition", "defects", "visible_text", "caption", "privacy_flags")


def template_caption(entry: dict, index: int, total: int, name: str = "") -> dict:
    """VLM 없이 쓰는 기계적 캡션. 사실(순서, 형식, 크기)만 적는다."""
    size = ""
    if entry.get("width") and entry.get("height"):
        size = ", %dx%d" % (entry["width"], entry["height"])
    what = name or "물품"
    return {
        "caption": "%s 사진 %d/%d (%s%s)" % (what, index, total, (entry.get("format") or "?").upper(), size),
        "source": "template",
    }


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v)]


def normalize_vlm(obj) -> dict:
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        obj = obj[0]
    if not isinstance(obj, dict):
        raise llm.LLMError("VLM 응답이 JSON 객체가 아닙니다")
    out = {
        "item": str(obj.get("item") or "").strip(),
        "color": str(obj.get("color") or "").strip(),
        "condition": str(obj.get("condition") or "").strip(),
        "defects": _as_list(obj.get("defects")),
        "visible_text": _as_list(obj.get("visible_text")),
        "caption": str(obj.get("caption") or "").strip(),
        "privacy_flags": _as_list(obj.get("privacy_flags")),
        "source": "vlm",
    }
    if not out["caption"]:
        out["caption"] = out["item"] or out["condition"]
    if not out["caption"]:
        raise llm.LLMError("VLM 응답에 쓸 내용이 없습니다")
    return out


def vlm_caption(client, photo_path: Path) -> dict:
    raw = Path(photo_path).read_bytes()
    if jpegmeta.sniff_format(raw) == "jpeg":
        try:
            raw = jpegmeta.strip_app1(raw)  # 좌표가 서버 로그에도 남지 않게
        except jpegmeta.JpegError:
            pass
    return normalize_vlm(client.vision_json(PROMPT, [raw], max_tokens=600))


def caption_item(root: Path, data: dict, client=None) -> list[str]:
    """data["photos"][*] 에 캡션을 채운다. client가 None이면 템플릿. 경고 문장 목록을 돌려준다."""
    warns: list[str] = []
    photos = data.get("photos", [])
    total = len(photos)
    name = data.get("name", "")
    for i, ph in enumerate(photos, 1):
        result = None
        if client is not None:
            try:
                result = vlm_caption(client, Path(root) / "photos" / ph["file"])
            except (llm.LLMError, OSError) as e:
                warns.append("%s: VLM 실패, 이후 사진은 템플릿으로 대체 (%s)" % (ph["file"], e))
                client = None  # 서버가 죽었으면 사진마다 기다리지 않는다
        if result is None:
            result = template_caption(ph, i, total, name)
        ph["caption"] = result["caption"]
        ph["caption_source"] = result["source"]
        for k in ("item", "color", "condition", "defects", "visible_text", "privacy_flags"):
            if k in result:
                ph[k] = result[k]
            else:
                ph.pop(k, None)
    return warns


def guess_from_captions(data: dict) -> dict:
    """사진별 VLM 결과에서 가장 많이 나온 물품명/하자를 모은다 (사용자 입력이 우선이므로 보조용)."""
    items = collections.Counter(p.get("item") for p in data.get("photos", []) if p.get("item"))
    defects: list[str] = []
    for p in data.get("photos", []):
        for d in p.get("defects") or []:
            if d not in defects:
                defects.append(d)
    return {"name": items.most_common(1)[0][0] if items else "", "defects": defects}
