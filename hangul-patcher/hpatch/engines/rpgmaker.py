"""RPG Maker MV / MZ (data/*.json) 추출 및 적용."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..backup import BackupStore
from ..masking import Masker, rpgmaker_masker
from ..units import ApplyReport, ExtractResult, Unit
from .base import ApplyOptions, Engine

# 파일별 (필드, 종류)
DB_FIELDS: Dict[str, List[Tuple[str, str]]] = {
    "Actors.json": [("name", "name"), ("nickname", "name"), ("profile", "description")],
    "Classes.json": [("name", "name")],
    "Items.json": [("name", "name"), ("description", "description")],
    "Weapons.json": [("name", "name"), ("description", "description")],
    "Armors.json": [("name", "name"), ("description", "description")],
    "Skills.json": [("name", "name"), ("description", "description"), ("message1", "message"), ("message2", "message")],
    "States.json": [("name", "name"), ("message1", "message"), ("message2", "message"),
                    ("message3", "message"), ("message4", "message")],
    "Enemies.json": [("name", "name")],
}
SYSTEM_SCALARS = [("gameTitle", "title"), ("currencyUnit", "term")]
SYSTEM_LISTS = ["armorTypes", "elements", "equipTypes", "skillTypes", "weaponTypes"]
TERM_LISTS = ["basic", "commands", "params"]
MAP_RE = re.compile(r"^Map\d+\.json$", re.I)

FONT_EXTS = (".ttf", ".otf", ".woff", ".woff2")
GAMEFONT_CSS_TEMPLATE = '@font-face {\n    font-family: GameFont;\n    src: url("%s");\n}\n'


def load_json(raw: bytes) -> Tuple[Any, bool]:
    bom = raw.startswith(b"\xef\xbb\xbf")
    return json.loads(raw.decode("utf-8-sig")), bom


def dump_json(data: Any, bom: bool) -> bytes:
    s = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (b"\xef\xbb\xbf" if bom else b"") + s.encode("utf-8")


def _get(data: Any, path: List[Any]) -> Any:
    for k in path:
        data = data[k]
    return data


class RPGMakerEngine(Engine):
    name = "rpgmaker"

    def __init__(self, game_dir, base_rel: str, variant: str) -> None:
        super().__init__(game_dir)
        self.base_rel = base_rel  # "www" (MV 배포본) 또는 "" (MZ / 프로젝트 폴더)
        self.variant = variant  # "mv" | "mz"
        self.label = f"RPG Maker {variant.upper()}"

    # -- 감지 ---------------------------------------------------------
    @classmethod
    def detect(cls, game_dir: Path) -> Optional["RPGMakerEngine"]:
        game_dir = Path(game_dir)
        for base in ("www", ""):
            root = game_dir / base if base else game_dir
            sysfile = root / "data" / "System.json"
            if not sysfile.is_file():
                continue
            if (root / "js" / "rmmz_core.js").exists():
                variant = "mz"
            elif (root / "js" / "rpg_core.js").exists():
                variant = "mv"
            else:
                try:
                    data, _ = load_json(sysfile.read_bytes())
                    variant = "mz" if isinstance(data, dict) and "advanced" in data else "mv"
                except (ValueError, OSError):
                    variant = "mv"
            return cls(game_dir, base, variant)
        return None

    def describe(self) -> str:
        return f"{self.label} ({self._data_rel()})"

    def _rel(self, *parts: str) -> str:
        return "/".join(p for p in ((self.base_rel,) + parts) if p)

    def _data_rel(self) -> str:
        return self._rel("data")

    def masker(self) -> Masker:
        return rpgmaker_masker()

    # -- 추출 ---------------------------------------------------------
    def _files(self) -> List[str]:
        d = self.game_dir / self._data_rel()
        names = sorted(p.name for p in d.glob("*.json"))
        return [n for n in names if self._handler_name(n)]

    @staticmethod
    def _handler_name(name: str) -> Optional[str]:
        if MAP_RE.match(name):
            return "map"
        if name in ("CommonEvents.json", "Troops.json"):
            return name[:-5].lower()
        if name in DB_FIELDS:
            return "db"
        if name == "System.json":
            return "system"
        return None

    def extract(self, store: BackupStore) -> ExtractResult:
        res = ExtractResult()
        for name in self._files():
            rel = self._rel("data", name)
            raw = store.original_bytes(rel)
            if raw is None:
                continue
            try:
                data, _ = load_json(raw)
            except ValueError as e:
                res.notes.append(f"{rel}: JSON 파싱 실패, 건너뜀 ({e})")
                continue
            res.files += 1
            kind = self._handler_name(name)
            if kind == "map":
                self._map(res.units, rel, data)
            elif kind == "commonevents":
                for i, ev in enumerate(data):
                    if isinstance(ev, dict):
                        self._commands(res.units, rel, ev.get("list"), [i, "list"])
            elif kind == "troops":
                for i, tr in enumerate(data):
                    if isinstance(tr, dict):
                        for p, page in enumerate(tr.get("pages") or []):
                            self._commands(res.units, rel, page.get("list"), [i, "pages", p, "list"])
            elif kind == "db":
                self._db(res.units, rel, data, DB_FIELDS[name])
            elif kind == "system":
                self._system(res.units, rel, data)
        return res

    @staticmethod
    def _add(units: List[Unit], rel: str, kind: str, text: Any, path: List[Any], **kw) -> None:
        if isinstance(text, str) and text.strip():
            units.append(Unit(file=rel, kind=kind, source=text, path=path, **kw))

    def _map(self, units: List[Unit], rel: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self._add(units, rel, "title", data.get("displayName"), ["displayName"])
        for ei, ev in enumerate(data.get("events") or []):
            if not isinstance(ev, dict):
                continue
            for pi, page in enumerate(ev.get("pages") or []):
                self._commands(units, rel, page.get("list"), ["events", ei, "pages", pi, "list"])

    def _db(self, units: List[Unit], rel: str, data: Any, fields) -> None:
        for i, rec in enumerate(data):
            if not isinstance(rec, dict):
                continue
            for fld, kind in fields:
                self._add(units, rel, kind, rec.get(fld), [i, fld])

    def _system(self, units: List[Unit], rel: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        for fld, kind in SYSTEM_SCALARS:
            self._add(units, rel, kind, data.get(fld), [fld])
        for fld in SYSTEM_LISTS:
            for i, v in enumerate(data.get(fld) or []):
                self._add(units, rel, "term", v, [fld, i])
        terms = data.get("terms") or {}
        for fld in TERM_LISTS:
            for i, v in enumerate(terms.get(fld) or []):
                self._add(units, rel, "term", v, ["terms", fld, i])
        msgs = terms.get("messages") or {}
        if isinstance(msgs, dict):
            for k, v in msgs.items():
                self._add(units, rel, "term", v, ["terms", "messages", k])

    def _commands(self, units: List[Unit], rel: str, cmds: Any, list_path: List[Any]) -> None:
        if not isinstance(cmds, list):
            return
        speaker = ""
        prev = ""
        i = 0
        while i < len(cmds):
            c = cmds[i]
            code = c.get("code") if isinstance(c, dict) else None
            params = c.get("parameters") if isinstance(c, dict) else None
            if not isinstance(params, list):
                params = []
            if code == 101:
                name = params[4] if len(params) > 4 and isinstance(params[4], str) else ""
                speaker = name
                self._add(units, rel, "name", name, list_path + [i, "parameters", 4])
                i += 1
            elif code in (401, 405):
                j = i
                lines: List[str] = []
                while j < len(cmds) and isinstance(cmds[j], dict) and cmds[j].get("code") == code:
                    p = cmds[j].get("parameters")
                    if not (isinstance(p, list) and p and isinstance(p[0], str)):
                        break
                    lines.append(p[0])
                    j += 1
                if j == i:  # 비정상 명령: 건너뜀
                    i += 1
                    continue
                text = "\n".join(lines)
                if text.strip():
                    units.append(Unit(file=rel, kind="dialogue" if code == 401 else "scroll_text", source=text,
                                      path=list(list_path), block=[i, j - i, code],
                                      speaker=speaker if code == 401 else "", prev=prev))
                    prev = text
                i = j
            elif code == 102:
                choices = params[0] if params and isinstance(params[0], list) else []
                for k, ch in enumerate(choices):
                    self._add(units, rel, "choice", ch, list_path + [i, "parameters", 0, k], speaker="", prev=prev)
                i += 1
            elif code == 402:
                if len(params) > 1:
                    self._add(units, rel, "choice", params[1], list_path + [i, "parameters", 1])
                i += 1
            else:
                i += 1

    # -- 적용 ---------------------------------------------------------
    def apply(self, store: BackupStore, translations: Dict[str, str], options: ApplyOptions) -> ApplyReport:
        rep = ApplyReport()
        units = self.extract(store).units
        by_file: Dict[str, List[Unit]] = {}
        for u in units:
            by_file.setdefault(u.file, []).append(u)
        sys_rel = self._rel("data", "System.json")
        font_name = None
        if options.font:
            font_name = self._install_font(store, options.font, rep)
        want_sys_edit = bool(options.locale_ko or (font_name and self.variant == "mz"))
        if want_sys_edit:
            by_file.setdefault(sys_rel, [])
        for rel, us in sorted(by_file.items()):
            raw = store.original_bytes(rel)
            if raw is None:
                continue
            data, bom = load_json(raw)
            changed = 0
            simple = [u for u in us if u.block is None]
            blocks = [u for u in us if u.block is not None]
            for u in simple:
                tr = translations.get(u.source)
                if tr is None:
                    rep.untranslated += self._needs(u.source)
                    continue
                parent = _get(data, u.path[:-1])
                if parent[u.path[-1]] != u.source:
                    rep.notes.append(f"{rel}: 위치가 맞지 않아 건너뜀 {u.path}")
                    continue
                parent[u.path[-1]] = tr
                changed += 1
            # 구조가 바뀔 수 있는 대사 묶음은 뒤에서부터 처리
            for u in sorted(blocks, key=lambda x: -x.block[0]):
                tr = translations.get(u.source)
                if tr is None:
                    rep.untranslated += self._needs(u.source)
                    continue
                if self._replace_block(data, u, tr):
                    changed += 1
                else:
                    rep.notes.append(f"{rel}: 대사 묶음 위치가 맞지 않아 건너뜀 {u.path} {u.block}")
            if rel == sys_rel and isinstance(data, dict):
                if options.locale_ko and data.get("locale") != "ko_KR":
                    data["locale"] = "ko_KR"
                    changed += 1
                if font_name and self.variant == "mz":
                    adv = data.setdefault("advanced", {})
                    adv["mainFontFilename"] = font_name
                    changed += 1
            if changed:
                store.write(rel, dump_json(data, bom))
                rep.written.append(rel)
                rep.replaced += changed
        return rep

    def _needs(self, source: str) -> int:
        m = self.masker()
        return 1 if m.needs_translation(m.mask(source)) else 0

    @staticmethod
    def _replace_block(data: Any, u: Unit, translated: str) -> bool:
        lst = _get(data, u.path)
        start, n, code = u.block
        orig = lst[start:start + n]
        if len(orig) != n or "\n".join(c["parameters"][0] for c in orig) != u.source:
            return False
        lines = translated.replace("\r\n", "\n").split("\n")
        new = []
        for i, line in enumerate(lines):
            cmd = copy.deepcopy(orig[min(i, n - 1)])
            cmd["parameters"][0] = line
            new.append(cmd)
        lst[start:start + n] = new
        return True

    # -- 폰트 ---------------------------------------------------------
    def _install_font(self, store: BackupStore, font_path: str, rep: ApplyReport) -> Optional[str]:
        fp = Path(font_path)
        if not fp.is_file() or fp.suffix.lower() not in FONT_EXTS:
            rep.notes.append(f"폰트 파일을 찾을 수 없거나 확장자가 올바르지 않습니다: {font_path}")
            return None
        name = fp.name
        store.write(self._rel("fonts", name), fp.read_bytes())
        rep.written.append(self._rel("fonts", name))
        if self.variant == "mv":
            css_rel = self._rel("fonts", "gamefont.css")
            raw = store.original_bytes(css_rel)
            css = raw.decode("utf-8-sig") if raw is not None else ""
            store.write(css_rel, patch_gamefont_css(css, name).encode("utf-8"))
            rep.written.append(css_rel)
        else:
            rep.notes.append("MZ: System.json 의 advanced.mainFontFilename 을 교체합니다.")
        return name


_FACE_RE = re.compile(r"@font-face\s*\{[^}]*font-family\s*:\s*[\"']?GameFont[\"']?[^}]*\}", re.I)


def patch_gamefont_css(css: str, font_file: str) -> str:
    """gamefont.css 의 GameFont 정의가 ``font_file`` 을 가리키도록 한다."""
    block = GAMEFONT_CSS_TEMPLATE % font_file
    if _FACE_RE.search(css):
        return _FACE_RE.sub(lambda m: block.rstrip("\n"), css, count=1)
    return (css.rstrip("\n") + "\n\n" if css.strip() else "") + block
