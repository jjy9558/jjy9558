"""실행 파이프라인: 어댑터 수집(실패 격리) -> 합성 -> 파일 출력."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .compose import Composed, compose
from .config import deep_merge
from .model import Context, Item, SourceMissing, SourceSkip
from .render import render_md
from .sources import REGISTRY


def source_cfg(cfg: dict, mod) -> dict:
    return deep_merge(mod.DEFAULTS, (cfg.get("sources") or {}).get(mod.NAME) or {})


def _status(status: str, items: int = 0, note: str = "") -> dict:
    return {"status": status, "ok": status == "ok", "items": items, "note": note}


def gather(cfg: dict, ctx: Context, only: list[str] | None = None) -> tuple[list[Item], dict]:
    """모든 어댑터를 돌려 (항목, {이름: 상태}). 어댑터 하나가 죽어도 나머지는 계속 간다."""
    items: list[Item] = []
    statuses: dict = {}
    for mod in REGISTRY:
        scfg = source_cfg(cfg, mod)
        if only is not None and mod.NAME not in only:
            continue
        if not scfg.get("enabled", True):
            statuses[mod.NAME] = _status("disabled", note="설정에서 껐어요")
            continue
        try:
            got = list(mod.collect(scfg, ctx))
        except SourceSkip as e:
            statuses[mod.NAME] = _status("skipped", note=str(e))
        except SourceMissing as e:
            statuses[mod.NAME] = _status("missing", note=ctx.red(e))
        except Exception as e:   # 어댑터 하나의 실패가 브리핑 전체를 막지 않게
            note = "%s: %s" % (type(e).__name__, " ".join(str(e).split()))
            statuses[mod.NAME] = _status("error", note=ctx.red(note)[:160])
        else:
            for it in got:
                if not it.section:
                    it.section = mod.SECTION
            items.extend(got)
            statuses[mod.NAME] = _status("ok", len(got))
    return items, statuses


def build(cfg: dict, ctx: Context, *, only: list[str] | None = None, seconds: float | None = None,
          cap: bool = True) -> Composed:
    items, statuses = gather(cfg, ctx, only)
    titles = {m.NAME: m.SECTION for m in REGISTRY}
    return compose(items, statuses, titles, cfg, ctx, seconds=seconds, cap=cap)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def write_outputs(result: Composed, cfg: dict, ctx: Context) -> dict[str, Path]:
    """brief-날짜.md/json 과 계약 파일 둘(brief-latest.json, cards-latest.json)을 쓴다."""
    out = ctx.path(cfg["out_dir"])
    date = result.brief["date"]
    paths = {
        "md": out / ("brief-%s.md" % date),
        "json": out / ("brief-%s.json" % date),
        "brief_latest": out / "brief-latest.json",
        "cards_latest": out / "cards-latest.json",
    }
    _atomic_write(paths["md"], render_md(result.brief))
    _atomic_write(paths["json"], dumps(result.brief))
    _atomic_write(paths["brief_latest"], dumps(result.brief))
    _atomic_write(paths["cards_latest"], dumps(result.cards))
    return paths
