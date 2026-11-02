"""명령줄 진입점: python -m tastemap <명령>."""
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO

from . import __version__, model, render, stats
from ._jjkit import llm, textwidth
from .importers import ImportFailed, import_diary, import_steam, import_youtube, watch_count
from .store import Store, data_dir
from .tagdict import TagDict

PRIVACY = ("[개인정보] 모든 처리는 이 컴퓨터 안에서 해요. 시청 기록은 제목과 시각만 저장하고 URL, 채널, 계정 정보는 버려요. "
           "game-diary는 캡션 원문 없이 태그 집계만 가져와요.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="taste-map", description="취향 지도: 스팀 구매, 게임 스크린샷 캡션, 유튜브 시청 기록을 태그 벡터로 바꿔 지도, 프로필, 후보 순위를 만든다")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    p.add_argument("--home", help="데이터 폴더 (기본 TASTEMAP_HOME 또는 ~/.taste-map)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import", help="데이터 가져오기")
    isub = s.add_subparsers(dest="what", required=True)
    x = isub.add_parser("steam", help="steam-buddy data.json")
    x.add_argument("path")
    x.add_argument("--compat", help="compat.json 위치 (기본: data.json 옆)")
    x = isub.add_parser("diary", help="game-diary sqlite (읽기 전용)")
    x.add_argument("path")
    x = isub.add_parser("youtube", help="구글 테이크아웃 watch-history.json")
    x.add_argument("path")
    x.add_argument("--since", help="이 날짜(YYYY-MM-DD) 이후만 (기본 전부)")

    s = sub.add_parser("tags", help="태그 사전과 수동 태그")
    tsub = s.add_subparsers(dest="what", required=True)
    x = tsub.add_parser("list", help="가져온 항목과 붙은 태그 보기")
    x.add_argument("--untagged", action="store_true", help="태그 없는 항목만")
    x = tsub.add_parser("edit", help="수동 태그 붙이기/빼기")
    x.add_argument("item", help="appid, 항목 키(g:123, y:...), 또는 이름 일부")
    x.add_argument("--add", default="", help="쉼표로 구분한 태그")
    x.add_argument("--remove", default="")
    x.add_argument("--set", dest="set_", default=None, help="수동 태그를 이 목록으로 통째로 바꿈")
    tsub.add_parser("dict", help="사전의 태그 목록")

    s = sub.add_parser("map", help="한 장짜리 취향 지도 HTML")
    s.add_argument("--out", default="map.html")
    s.add_argument("--k", type=int, help="군집 수 (기본 자동)")
    s.add_argument("--seed", type=int, default=model.DEFAULT_SEED)
    s.add_argument("--max-videos", type=int, default=120)
    s.add_argument("--llm", action="store_true", help="프로필 말투를 로컬 LLM으로 다듬기 (TASTEMAP_LLM_URL 필요)")

    s = sub.add_parser("profile", help="'나는 이런 걸 좋아한다' 5줄")
    s.add_argument("--llm", action="store_true")

    s = sub.add_parser("rank", help="다음에 살 후보 순위")
    s.add_argument("--candidates", required=True, help="appid 목록 파일 (한 줄에 appid[,이름[,태그1|태그2]])")
    s.add_argument("--top", type=int, default=20)
    s.add_argument("--handheld", action="store_true", help="스팀덱 미지원(compat.json 기준) 후보 감점")

    s = sub.add_parser("wrapped", help="연말결산 카드 HTML")
    s.add_argument("--year", type=int, help="구매 연도 (기본: 전체)")
    s.add_argument("--out", default="wrapped.html")

    s = sub.add_parser("career", help="게임 경력 증명서 (재미용 HTML 한 장)")
    s.add_argument("--name", default="무명 게이머", help="증명서에 쓸 닉네임 (실명 대신 닉네임 권장)")
    s.add_argument("--out", default="career.html")

    sub.add_parser("status", help="가져온 데이터 요약")
    return p


class Ctx:
    def __init__(self, args, out, now):
        home = Path(args.home) if args.home else data_dir()
        self.home = home
        self.store = Store(home / "data.json")
        self.tagdict = TagDict.load(home / "tags.json")
        self.out = out
        self.now = now

    def say(self, s=""):
        print(s, file=self.out)

    def units(self):
        return model.build_units(self.store, self.tagdict)


def _write(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _llm_client():
    try:
        return llm.client_from_env("TASTEMAP", timeout=60.0)
    except llm.LLMError:
        return None


def _profile(ctx, units, use_llm):
    lines = model.profile_lines(units, ctx.tagdict, ctx.store)
    if use_llm:
        client = _llm_client()
        if client is None:
            ctx.say("(TASTEMAP_LLM_URL 이 없어서 LLM 없이 만들었어요.)")
        else:
            lines = model.polish_with_llm(lines, client)
    return lines


def cmd_import(a, ctx):
    try:
        if a.what == "steam":
            r = import_steam(ctx.store, a.path, a.compat)
            ctx.say(f"스팀 게임 {r['imported']}개를 가져왔어요. (건너뜀 {r['skipped']}, 환불 제외 {r['refunded']}"
                    f"{', 호환 정보 사용' if r['compat'] else ''})")
        elif a.what == "diary":
            r = import_diary(ctx.store, a.path)
            ctx.say(f"스크린샷 {r['shots']}장, 게임 {r['games']}개를 집계했어요. (캡션 원문은 저장하지 않아요.)")
        else:
            r = import_youtube(ctx.store, a.path, a.since)
            ctx.say(f"시청 {r['entries']}건 -> 서로 다른 영상 {len(ctx.store.youtube)}개 (건너뜀 {r['skipped']}). "
                    f"제목과 시각만 저장했어요.")
    except ImportFailed as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    ctx.store.save()
    return 0


def _find_items(ctx, query):
    units = ctx.units()
    q = query.strip()
    if q.isdigit():
        hit = [u for u in units if u.key == f"g:{q}"]
        if hit:
            return hit
    hit = [u for u in units if u.key == q]
    if hit:
        return hit
    q = q.lower()
    return [u for u in units if q in u.label.lower()]


def _tag_ids(ctx, text):
    out = []
    for w in text.split(","):
        w = w.strip()
        if w:
            out.append(ctx.tagdict.resolve(w) or w.lower())
    return out


def cmd_tags(a, ctx):
    if a.what == "dict":
        rows = [(tid, t.get("label", ""), t.get("group", ""), len(t.get("aliases", [])))
                for tid, t in sorted(ctx.tagdict.tags.items())]
        for line in textwidth.table(rows, ["id", "이름", "그룹", "별칭 수"]):
            ctx.say(line)
        return 0
    if a.what == "list":
        rows = []
        for u in ctx.units():
            if a.untagged and u.vec:
                continue
            tags = ", ".join(ctx.tagdict.label(t) for t, _ in sorted(u.vec.items(), key=lambda kv: (-kv[1], kv[0]))[:5])
            manual = "*" if ctx.store.manual_tags.get(u.key) else ""
            rows.append((u.key, u.kind, textwidth.truncate(u.label, 30), tags + manual))
        for line in textwidth.table(rows, ["키", "종류", "이름", "태그 (*=수동)"]):
            ctx.say(line)
        return 0
    hits = _find_items(ctx, a.item)
    if not hits:
        print("그런 항목이 없어요. `tags list` 로 키를 확인하세요.", file=sys.stderr)
        return 1
    if len(hits) > 1:
        print("여러 항목이 걸려요. 키로 지정해 주세요: " + ", ".join(f"{u.key}({u.label})" for u in hits[:6]), file=sys.stderr)
        return 1
    u = hits[0]
    cur = list(ctx.store.manual_tags.get(u.key, []))
    if a.set_ is not None:
        cur = _tag_ids(ctx, a.set_)
    for t in _tag_ids(ctx, a.add):
        if t not in cur:
            cur.append(t)
    for t in _tag_ids(ctx, a.remove):
        if t in cur:
            cur.remove(t)
    if cur:
        ctx.store.manual_tags[u.key] = cur
    else:
        ctx.store.manual_tags.pop(u.key, None)
    ctx.store.save()
    ctx.say(f"{u.label}: 수동 태그 = {', '.join(ctx.tagdict.label(t) for t in cur) or '(없음)'}")
    return 0


def cmd_status(a, ctx):
    s = ctx.store
    ctx.say(f"스팀 게임 {len(s.steam)}개, 다이어리 게임 {len(s.diary)}개, "
            f"영상 {len(s.youtube)}개(시청 {sum(watch_count(r) for r in s.youtube.values())}회), 수동 태그 {len(s.manual_tags)}개")
    units = ctx.units()
    un = [u for u in units if not u.vec]
    if un:
        ctx.say(f"태그 없는 항목 {len(un)}개: `tags list --untagged` 로 보고 `tags edit` 로 붙이세요.")
    ctx.say(PRIVACY)
    return 0


def cmd_map(a, ctx):
    units = ctx.units()
    if not units:
        print("가져온 데이터가 없어요. `import steam|diary|youtube` 부터 하세요.", file=sys.stderr)
        return 1
    m = model.build_map(units, a.k, a.seed, a.max_videos)
    if not m["units"]:
        print("태그가 붙은 항목이 없어요. `tags edit` 로 태그를 붙이거나 태그 사전을 늘려 보세요.", file=sys.stderr)
        return 1
    lines = _profile(ctx, units, a.llm)
    _write(a.out, render.render_map(m, lines, ctx.tagdict))
    ctx.say(f"지도를 만들었어요: {a.out} (항목 {len(m['units'])}개, 군집 {m['k']}개)")
    return 0


def cmd_profile(a, ctx):
    units = ctx.units()
    for i, line in enumerate(_profile(ctx, units, a.llm), 1):
        ctx.say(f"{i}. {line}")
    return 0


def cmd_rank(a, ctx):
    try:
        text = Path(a.candidates).read_text(encoding="utf-8")
    except OSError as e:
        print(f"후보 파일을 읽지 못했어요: {e}", file=sys.stderr)
        return 1
    cands = model.parse_candidates(text, ctx.tagdict)
    if not cands:
        print("후보 appid 가 없어요. 한 줄에 appid 하나씩 적어 주세요.", file=sys.stderr)
        return 1
    units = ctx.units()
    rows = model.rank_candidates(cands, units, ctx.tagdict, ctx.store, handheld=a.handheld)
    table = []
    for i, r in enumerate(rows[: a.top], 1):
        sc = "태그 없음" if r["score"] is None else f"{r['score']:+.2f}"
        why = ", ".join(ctx.tagdict.label(t) for t in r["why"])
        table.append((i, r["appid"], textwidth.truncate(r["name"] or "-", 28), sc, why + (" (보유)" if r["owned"] else "")))
    for line in textwidth.table(table, ["#", "appid", "이름", "점수", "이유"], right_cols=[0, 1, 3]):
        ctx.say(line)
    if any(r["score"] is None for r in rows):
        ctx.say("태그 없음: 후보 파일에 `appid,이름,태그1|태그2` 로 적거나 `tags edit g:<appid> --add ...` 를 쓰세요.")
    return 0


def cmd_wrapped(a, ctx):
    if not ctx.store.steam:
        print("스팀 기록이 없어요. `import steam` 부터 하세요.", file=sys.stderr)
        return 1
    s = stats.wrapped_stats(ctx.store, a.year, ctx.now)
    _write(a.out, render.render_wrapped(s))
    ctx.say(f"연말결산을 만들었어요: {a.out} (게임 {s['count']}개, 총 {s['hours']:.1f}시간)")
    return 0


def cmd_career(a, ctx):
    if not ctx.store.steam:
        print("스팀 기록이 없어요. `import steam` 부터 하세요.", file=sys.stderr)
        return 1
    c = stats.career_stats(ctx.store, ctx.units(), ctx.tagdict, a.name, ctx.now)
    _write(a.out, render.render_career(c))
    ctx.say(f"경력 증명서를 만들었어요: {a.out} ({c['rank']}, {c['hours']:.0f}시간)")
    return 0


COMMANDS = {"import": cmd_import, "tags": cmd_tags, "map": cmd_map, "profile": cmd_profile, "rank": cmd_rank,
            "wrapped": cmd_wrapped, "career": cmd_career, "status": cmd_status}


def main(argv: list[str] | None = None, out: TextIO | None = None, now: datetime | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # cp949 콘솔에서 한글로 죽지 않게
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    ctx = Ctx(args, out or sys.stdout, now or datetime.now())
    return COMMANDS[args.cmd](args, ctx)
