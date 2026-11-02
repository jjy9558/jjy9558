"""스팀덱 호환 등급 / 안티치트 / 핸드헬드 프리셋 메모. 네트워크 조회 없이 JSON 파일만 쓴다.

번들 예시(steam_buddy/data/compat.json)를 사용자 파일(<데이터 폴더>/compat.json)이 덮어쓴다.
"""

import json
import re
from pathlib import Path

SHIPPED = Path(__file__).parent / "data" / "compat.json"

DECK_LABELS = {"verified": "인증됨", "playable": "플레이 가능", "unsupported": "미지원", "unknown": "확인 필요"}
SUPPORT_LABELS = {"yes": "지원", "no": "미지원", "unknown": "확인 필요"}
FSR_VALUES = ("off", "quality", "balanced", "performance", "ultra-performance")
PRESET_KEYS = ("tdp_w", "fps_cap", "resolution", "fsr", "graphics_notes")
PRESET_FORMAT = 1
MAX_TEXT = 500


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def validate_preset(p):
    """프리셋 dict를 검사해 정리된 사본을 돌려준다. 잘못되면 ValueError."""
    if not isinstance(p, dict):
        raise ValueError("preset은 객체(dict)여야 해요.")
    extra = set(p) - set(PRESET_KEYS)
    if extra:
        raise ValueError(f"알 수 없는 preset 항목: {', '.join(sorted(extra))}")
    out = {}
    if p.get("tdp_w") is not None:
        v = p["tdp_w"]
        if not _is_int(v) or not 3 <= v <= 30:
            raise ValueError("tdp_w는 3~30 사이 정수(W)여야 해요.")
        out["tdp_w"] = v
    if p.get("fps_cap") is not None:
        v = p["fps_cap"]
        if not _is_int(v) or not 15 <= v <= 240:
            raise ValueError("fps_cap은 15~240 사이 정수여야 해요.")
        out["fps_cap"] = v
    if p.get("resolution") is not None:
        v = p["resolution"]
        m = re.fullmatch(r"(\d{3,5})x(\d{3,5})", v) if isinstance(v, str) else None
        if not m:
            raise ValueError("resolution은 '1280x800' 같은 형식이어야 해요.")
        out["resolution"] = v
    if p.get("fsr") is not None:
        v = p["fsr"]
        if v not in FSR_VALUES:
            raise ValueError(f"fsr은 {'/'.join(FSR_VALUES)} 중 하나여야 해요.")
        out["fsr"] = v
    if p.get("graphics_notes") is not None:
        v = p["graphics_notes"]
        if not isinstance(v, str) or len(v) > MAX_TEXT:
            raise ValueError(f"graphics_notes는 {MAX_TEXT}자 이하 문자열이어야 해요.")
        out["graphics_notes"] = v
    return out


def validate_entry(e):
    if not isinstance(e, dict):
        raise ValueError("항목은 객체(dict)여야 해요.")
    allowed = {"name", "deck", "anticheat", "note", "preset", "example"}
    extra = set(e) - allowed
    if extra:
        raise ValueError(f"알 수 없는 항목: {', '.join(sorted(extra))}")
    out = {}
    for k in ("name", "note"):
        if e.get(k) is not None:
            if not isinstance(e[k], str) or len(e[k]) > MAX_TEXT:
                raise ValueError(f"{k}는 {MAX_TEXT}자 이하 문자열이어야 해요.")
            out[k] = e[k]
    if e.get("deck") is not None:
        if e["deck"] not in DECK_LABELS:
            raise ValueError(f"deck은 {'/'.join(DECK_LABELS)} 중 하나여야 해요.")
        out["deck"] = e["deck"]
    if e.get("example") is not None:
        if not isinstance(e["example"], bool):
            raise ValueError("example은 true/false여야 해요.")
        out["example"] = e["example"]
    ac = e.get("anticheat")
    if ac is not None:
        if not isinstance(ac, dict) or set(ac) - {"kind", "linux", "deck"}:
            raise ValueError("anticheat는 kind/linux/deck만 가진 객체여야 해요.")
        o = {}
        if ac.get("kind") is not None:
            if not isinstance(ac["kind"], str) or not ac["kind"].strip() or len(ac["kind"]) > 100:
                raise ValueError("anticheat.kind는 100자 이하 문자열이어야 해요.")
            o["kind"] = ac["kind"].strip()
        for k in ("linux", "deck"):
            if ac.get(k) is not None:
                if ac[k] not in SUPPORT_LABELS:
                    raise ValueError(f"anticheat.{k}는 {'/'.join(SUPPORT_LABELS)} 중 하나여야 해요.")
                o[k] = ac[k]
        out["anticheat"] = o
    if e.get("preset") is not None:
        out["preset"] = validate_preset(e["preset"])
    return out


def _read(path, label):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as ex:
        raise ValueError(f"{label} 파일을 읽을 수 없어요 ({path}): {ex}")
    games = data.get("games", {}) if isinstance(data, dict) else None
    if not isinstance(games, dict):
        raise ValueError(f"{label} 파일 형식이 올바르지 않아요 ({path}): 'games' 객체가 필요해요.")
    out = {}
    for k, v in games.items():
        if not str(k).isdigit():
            raise ValueError(f"{label} 파일의 appid가 숫자가 아니에요: {k}")
        try:
            out[str(k)] = validate_entry(v)
        except ValueError as ex:
            raise ValueError(f"{label} 파일 {k}번 항목: {ex}")
    return out


class CompatDB:
    """번들 예시 + 사용자 파일. 쓰기는 사용자 파일에만 한다."""

    def __init__(self, user_path, shipped=SHIPPED):
        self.user_path = Path(user_path)
        self.shipped_path = Path(shipped) if shipped else None
        self.shipped = _read(self.shipped_path, "예시 compat") if self.shipped_path and self.shipped_path.exists() else {}
        self.user = _read(self.user_path, "compat") if self.user_path.exists() else {}

    def get(self, appid):
        """필드 단위로 사용자 값이 예시를 덮어쓴 항목. 없으면 None."""
        k = str(appid)
        if k not in self.shipped and k not in self.user:
            return None
        e = dict(self.shipped.get(k, {}))
        u = self.user.get(k, {})
        for f, v in u.items():
            if f in ("anticheat", "preset") and isinstance(e.get(f), dict):
                e[f] = {**e[f], **v}
            else:
                e[f] = v
        if u:
            e.pop("example", None)  # 사용자가 손댄 항목은 예시가 아님
            if "example" in u:
                e["example"] = u["example"]
        return e

    def all_ids(self):
        return sorted(set(self.shipped) | set(self.user), key=int)

    def update(self, appid, **fields):
        """사용자 항목에 필드를 합쳐 검사 후 저장한다."""
        k = str(appid)
        cur = dict(self.user.get(k, {}))
        for f, v in fields.items():
            if v is None:
                continue
            if f in ("anticheat", "preset"):
                cur[f] = {**cur.get(f, {}), **v}
            else:
                cur[f] = v
        self.user[k] = validate_entry(cur)
        self.save()

    def set_preset(self, appid, preset):
        k = str(appid)
        cur = dict(self.user.get(k, {}))
        cur["preset"] = validate_preset(preset)
        self.user[k] = validate_entry(cur)
        self.save()

    def save(self):
        self.user_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.user_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"games": self.user}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.user_path)


def export_preset(appid, name, preset):
    return {"steam_buddy_preset": PRESET_FORMAT, "appid": int(appid), "name": name or "", "preset": validate_preset(preset)}


def parse_preset_doc(text):
    """공유용 JSON 문서를 검사해 (appid, name, preset)을 돌려준다."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as ex:
        raise ValueError(f"프리셋 JSON을 읽을 수 없어요: {ex}")
    if not isinstance(doc, dict) or doc.get("steam_buddy_preset") != PRESET_FORMAT:
        raise ValueError(f"steam_buddy_preset: {PRESET_FORMAT} 형식의 프리셋 파일이 아니에요.")
    extra = set(doc) - {"steam_buddy_preset", "appid", "name", "preset"}
    if extra:
        raise ValueError(f"알 수 없는 항목: {', '.join(sorted(extra))}")
    if not _is_int(doc.get("appid")) or doc["appid"] <= 0:
        raise ValueError("appid는 양의 정수여야 해요.")
    name = doc.get("name", "")
    if not isinstance(name, str) or len(name) > MAX_TEXT:
        raise ValueError("name은 문자열이어야 해요.")
    if "preset" not in doc:
        raise ValueError("preset 항목이 없어요.")
    return doc["appid"], name, validate_preset(doc["preset"])


def summary_lines(e):
    """check/show용 (라벨, 값) 목록."""
    rows = []
    tag = " (예시 데이터)" if e.get("example") else ""
    if e.get("deck"):
        rows.append(("스팀덱", DECK_LABELS[e["deck"]] + tag))
    ac = e.get("anticheat")
    if ac:
        parts = [ac.get("kind", "종류 확인 필요")]
        parts.append("리눅스 " + SUPPORT_LABELS[ac.get("linux", "unknown")])
        parts.append("덱 " + SUPPORT_LABELS[ac.get("deck", "unknown")])
        rows.append(("안티치트", " / ".join(parts) + ("" if e.get("deck") else tag)))
    p = e.get("preset")
    if p:
        bits = []
        if "tdp_w" in p:
            bits.append(f"TDP {p['tdp_w']}W")
        if "fps_cap" in p:
            bits.append(f"{p['fps_cap']}fps 제한")
        if "resolution" in p:
            bits.append(p["resolution"])
        if "fsr" in p:
            bits.append(f"FSR {p['fsr']}")
        if p.get("graphics_notes"):
            bits.append(p["graphics_notes"])
        rows.append(("핸드헬드", ", ".join(bits) or "(비어 있음)"))
    if e.get("note"):
        rows.append(("호환 메모", e["note"]))
    return rows
