"""입력 -> 태그 벡터 -> 군집 -> 2D 투영 -> 프로필/순위. 전부 결정적이고 순수 파이썬."""
import math
import random
from dataclasses import dataclass, field

from .importers import norm_name, parse_iso, watch_count

DEFAULT_SEED = 20240229
YT_SHARE = 0.35          # 영상 취향이 게임 취향 벡터에 섞이는 비율
VIDEO_GROUP = "video"    # 영상 전용 태그(리뷰, 공략...)는 게임 후보 점수에서 뺀다


@dataclass
class Unit:
    key: str
    kind: str                       # game | video
    label: str
    tags: dict = field(default_factory=dict)     # 태그 id -> 가중치 (정규화 전)
    vec: dict = field(default_factory=dict)      # 정규화 벡터
    aff: float = 0.0                # 만족/선호 신호 (-1 ~ 1.2)
    hours: float = 0.0
    rating: int | None = None
    price: float = 0.0
    shots: int = 0
    count: int = 0
    appid: int | None = None
    owned: bool = False
    deck: str | None = None
    moods: dict = field(default_factory=dict)


# ------------------------------------------------------------------ 단위 만들기
def _add(d, tid, w):
    d[tid] = d.get(tid, 0.0) + w


def _map_word(tagdict, word):
    tid = tagdict.resolve(word)
    if tid:
        return [tid]
    return tagdict.match_text(word)


def _norm(tags: dict) -> dict:
    n = math.sqrt(sum(v * v for v in tags.values()))
    return {k: v / n for k, v in sorted(tags.items()) if v} if n > 0 else {}


def build_units(store, tagdict):
    units: dict[str, Unit] = {}
    manual = store.manual_tags
    by_name = {}
    for appid, g in store.steam.items():
        u = Unit(key=f"g:{appid}", kind="game", label=g["name"], appid=int(appid), owned=True,
                 hours=g.get("minutes", 0) / 60.0, rating=g.get("rating"), price=g.get("price_paid", 0),
                 deck=g.get("deck"))
        for word in g.get("genres", []):
            ids = _map_word(tagdict, word)
            for t in ids:
                _add(u.tags, t, 1.0)
            if not ids:
                _add(u.tags, str(word).strip().lower(), 0.6)
        kg = tagdict.known_game(g.get("appid"), g["name"])
        for t in (kg or {}).get("tags", []):
            _add(u.tags, t, 1.5)
        for t in tagdict.match_text(g["name"]):
            _add(u.tags, t, 0.5)
        units[u.key] = u
        by_name[norm_name(g["name"])] = u
    for dkey, d in store.diary.items():
        u = None
        if d.get("appid") is not None:
            u = units.get(f"g:{d['appid']}")
        if u is None:
            u = by_name.get(norm_name(d.get("game", "")))
        if u is None:
            key = f"g:{d['appid']}" if d.get("appid") is not None else f"g:n:{norm_name(d.get('game', ''))}"
            u = Unit(key=key, kind="game", label=d.get("game") or key, appid=d.get("appid"))
            kg = tagdict.known_game(d.get("appid"), d.get("game"))
            for t in (kg or {}).get("tags", []):
                _add(u.tags, t, 1.5)
            for t in tagdict.match_text(d.get("game", "")):
                _add(u.tags, t, 0.5)
            units[u.key] = u
        u.shots += d.get("shots", 0)
        cap = max(1, d.get("captioned", 0))
        for t, n in d.get("tags", {}).items():
            ids = _map_word(tagdict, t)
            share = min(1.0, n / cap)
            for tid in ids:
                _add(u.tags, tid, 0.5 + 0.5 * share)
            if not ids and n >= 2:
                _add(u.tags, t, 0.4 + 0.4 * share)
        for m, n in d.get("moods", {}).items():
            u.moods[m] = u.moods.get(m, 0) + n
            for tid in _map_word(tagdict, m):
                _add(u.tags, tid, 0.4)
    for rec_key, rec in store.youtube.items():
        u = Unit(key=f"y:{rec_key}", kind="video", label=rec["title"], count=watch_count(rec))
        for t in tagdict.match_text(rec["title"]):
            _add(u.tags, t, 1.0)
        units[u.key] = u
    for u in units.values():
        for t in manual.get(u.key, []):
            _add(u.tags, t, 2.0)
        u.vec = _norm(u.tags)
        u.aff = _affinity(u)
    return [units[k] for k in sorted(units)]


def _affinity(u: Unit) -> float:
    if u.kind == "video":
        return min(0.6, 0.15 * math.log2(1 + u.count))
    h = u.hours
    if u.rating:
        a = (u.rating - 3) / 2.0
        if u.rating >= 3:
            a += 0.25 * min(1.0, h / 50.0)
    elif u.owned:
        a = 0.15 + 0.6 * min(1.0, h / 30.0) if h >= 0.1 else 0.0
    else:
        a = 0.3
    if u.shots:
        a += 0.2 * min(1.0, math.log1p(u.shots) / math.log(11))
    return max(-1.0, min(1.2, a))


# ------------------------------------------------------------------ 군집 / 투영
def dims_of(units):
    return sorted({t for u in units for t in u.vec})


def dense(units, dims):
    idx = {t: i for i, t in enumerate(dims)}
    rows = []
    for u in units:
        r = [0.0] * len(dims)
        for t, w in u.vec.items():
            r[idx[t]] = w
        rows.append(r)
    return rows


def _d2(a, b):
    return sum((x - y) * (x - y) for x, y in zip(a, b))


def auto_k(n):
    return max(1, min(6, round(math.sqrt(n / 2.0)))) if n else 0


def kmeans(rows, k, seed=DEFAULT_SEED, iters=60):
    """k-means++ 초기화 + Lloyd. rows 순서가 같고 seed 가 같으면 결과가 같다. 라벨 목록을 돌려준다."""
    n = len(rows)
    if n == 0:
        return []
    k = max(1, min(k, n))
    rng = random.Random(seed)
    cents = [list(rows[rng.randrange(n)])]
    while len(cents) < k:
        d = [min(_d2(r, c) for c in cents) for r in rows]
        tot = sum(d)
        if tot <= 1e-12:
            break
        x = rng.random() * tot
        acc = 0.0
        pick = n - 1
        for i, v in enumerate(d):
            acc += v
            if acc >= x:
                pick = i
                break
        cents.append(list(rows[pick]))
    k = len(cents)
    labels = [0] * n
    for it in range(iters):
        new = [min(range(k), key=lambda c: (_d2(r, cents[c]), c)) for r in rows]
        if new == labels and it > 0:
            break
        labels = new
        for c in range(k):
            members = [rows[i] for i in range(n) if labels[i] == c]
            if members:
                cents[c] = [sum(col) / len(members) for col in zip(*members)]
            else:  # 빈 군집: 가장 먼 점으로 옮긴다
                far = max(range(n), key=lambda i: (_d2(rows[i], cents[labels[i]]), -i))
                cents[c] = list(rows[far])
    return labels


def canonical_labels(labels, units):
    """군집 번호를 (크기 내림차순, 가장 작은 키)로 다시 매긴다. 초기화 순서에 영향받지 않게."""
    groups = {}
    for i, l in enumerate(labels):
        groups.setdefault(l, []).append(i)
    order = sorted(groups, key=lambda l: (-len(groups[l]), min(units[i].key for i in groups[l])))
    remap = {old: new for new, old in enumerate(order)}
    return [remap[l] for l in labels]


def _matvec_xtx(X, v):
    xv = [sum(a * b for a, b in zip(r, v)) for r in X]
    out = [0.0] * len(v)
    for r, s in zip(X, xv):
        if s:
            for j, a in enumerate(r):
                if a:
                    out[j] += a * s
    return out


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 1e-12 else None


def pca2(rows):
    """중심화한 뒤 거듭제곱법으로 상위 2개 주성분. (좌표 목록, 설명 분산 비율) 을 돌려준다."""
    n = len(rows)
    if n == 0:
        return [], (0.0, 0.0)
    d = len(rows[0])
    if d == 0:
        return [(0.0, 0.0)] * n, (0.0, 0.0)
    mean = [sum(r[j] for r in rows) / n for j in range(d)]
    X = [[r[j] - mean[j] for j in range(d)] for r in rows]
    total = sum(x * x for r in X for x in r)
    if total <= 1e-12:
        return [(0.0, 0.0)] * n, (0.0, 0.0)
    comps = []
    for c in range(2):
        v = _unit([1.0 + ((j * 7 + c * 3) % 11) / 11.0 for j in range(d)])
        for p in comps:
            dot = sum(a * b for a, b in zip(v, p))
            v = _unit([a - dot * b for a, b in zip(v, p)]) or v
        for _ in range(300):
            w = _matvec_xtx(X, v)
            for p in comps:
                dot = sum(a * b for a, b in zip(w, p))
                w = [a - dot * b for a, b in zip(w, p)]
            w = _unit(w)
            if w is None:
                v = None
                break
            diff = sum((a - b) ** 2 for a, b in zip(w, v))
            v = w
            if diff < 1e-14:
                break
        if v is None:
            comps.append([0.0] * d)
            continue
        i = max(range(d), key=lambda j: abs(v[j]))  # 부호 고정
        if v[i] < 0:
            v = [-a for a in v]
        comps.append(v)
    coords = [(sum(a * b for a, b in zip(r, comps[0])), sum(a * b for a, b in zip(r, comps[1]))) for r in X]
    var = [sum(c[i] ** 2 for c in coords) / total for i in (0, 1)]
    return coords, (var[0], var[1])


def build_map(units, k=None, seed=DEFAULT_SEED, max_videos=120):
    """지도에 올릴 단위(태그가 있는 것)와 군집/좌표를 한꺼번에 계산한다."""
    tagged = [u for u in units if u.vec]
    untagged = [u for u in units if not u.vec]
    games = [u for u in tagged if u.kind == "game"]
    videos = sorted((u for u in tagged if u.kind == "video"), key=lambda u: (-u.count, u.key))[:max_videos]
    shown = sorted(games + videos, key=lambda u: u.key)
    dims = dims_of(shown)
    rows = dense(shown, dims)
    kk = k if k else auto_k(len(shown))
    labels = canonical_labels(kmeans(rows, kk, seed), shown) if shown else []
    coords, var = pca2(rows)
    clusters = []
    for c in sorted(set(labels)):
        idx = [i for i, l in enumerate(labels) if l == c]
        cen = {}
        for i in idx:
            for t, w in shown[i].vec.items():
                cen[t] = cen.get(t, 0.0) + w / len(idx)
        top = sorted(cen.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
        clusters.append({"id": c, "size": len(idx), "top": [t for t, _ in top], "members": [shown[i].key for i in idx]})
    return {"units": shown, "labels": labels, "coords": coords, "variance": var, "clusters": clusters,
            "untagged": untagged, "hidden_videos": len([u for u in tagged if u.kind == "video"]) - len(videos),
            "dims": dims, "k": len(clusters), "seed": seed}


# ------------------------------------------------------------------ 프로필 / 순위
def tag_affinity(units, kind=None):
    out = {}
    for u in units:
        if kind and u.kind != kind:
            continue
        for t, w in u.vec.items():
            out[t] = out.get(t, 0.0) + u.aff * w
    return out


def profile_vector(units, tagdict, include_video_only_tags=False):
    """게임 신호 벡터 + 영상 신호 벡터(YT_SHARE)를 합친 취향 벡터. 영상 전용 태그는 기본으로 뺀다."""
    def part(kind):
        acc = tag_affinity(units, kind)
        if not include_video_only_tags:
            acc = {t: v for t, v in acc.items() if tagdict.tags.get(t, {}).get("group") != VIDEO_GROUP}
        n = math.sqrt(sum(v * v for v in acc.values()))
        return {t: v / n for t, v in acc.items()} if n > 1e-12 else {}
    g, v = part("game"), part("video")
    out = dict(g)
    for t, w in v.items():
        out[t] = out.get(t, 0.0) + YT_SHARE * w
    return out


def rank_candidates(cands, units, tagdict, store, handheld=False):
    """cands: [{appid, name, tags}] -> 점수 순 목록. 태그 없는 후보는 맨 아래(score None)."""
    prof = profile_vector(units, tagdict)
    pn = math.sqrt(sum(v * v for v in prof.values()))
    owned = {int(a) for a in store.steam}
    rows = []
    for c in cands:
        tags = {}
        for t in c.get("tags", []):
            tags[t] = tags.get(t, 0.0) + 1.0
        kg = tagdict.known_game(c.get("appid"), c.get("name"))
        for t in (kg or {}).get("tags", []):
            tags[t] = tags.get(t, 0.0) + 1.5
        for t in store.manual_tags.get(f"g:{c['appid']}", []):
            tags[t] = tags.get(t, 0.0) + 2.0
        tags = {t: w for t, w in tags.items() if tagdict.tags.get(t, {}).get("group") != VIDEO_GROUP}
        name = c.get("name") or (store.steam.get(str(c["appid"])) or {}).get("name") or ""
        if not name and kg:
            name = kg["names"][0]
            name = name.title() if name.isascii() else name
        row = {"appid": c["appid"], "name": name or "", "owned": c["appid"] in owned, "score": None,
               "why": [], "deck": c.get("deck"), "tags": sorted(tags)}
        v = _norm(tags)
        if v and pn > 1e-12:
            contrib = {t: prof.get(t, 0.0) * w / pn for t, w in v.items()}
            score = sum(contrib.values())
            deck = c.get("deck") or (store.compat.get(str(c["appid"])) or {}).get("deck") \
                or (store.steam.get(str(c["appid"])) or {}).get("deck")
            row["deck"] = deck
            if handheld and deck == "unsupported":
                score -= 0.2
            row["score"] = score
            row["why"] = [t for t, x in sorted(contrib.items(), key=lambda kv: (-kv[1], kv[0])) if x > 0][:2]
        rows.append(row)
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0.0), r["appid"]))
    return rows


def parse_candidates(text, tagdict):
    """한 줄에 하나: `appid` 또는 `appid,이름,태그1|태그2`. # 주석, 빈 줄은 건너뜀."""
    out, seen = [], set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if not parts[0].isdigit():
            continue
        appid = int(parts[0])
        if appid in seen:
            continue
        seen.add(appid)
        tags = []
        if len(parts) > 2:
            for w in parts[2].replace(";", "|").split("|"):
                tid = tagdict.resolve(w) or (w.strip().lower() if w.strip() else None)
                if tid:
                    tags.append(tid)
        out.append({"appid": appid, "name": parts[1] if len(parts) > 1 else "", "tags": tags})
    return out


def _fmt_list(items):
    return ", ".join(items)


def profile_lines(units, tagdict, store=None):
    """'나는 이런 걸 좋아한다' 5줄. 데이터가 모자라도 항상 5줄을 돌려준다(없는 줄은 안내 문장)."""
    games = [u for u in units if u.kind == "game"]
    videos = [u for u in units if u.kind == "video"]
    L = tagdict.label
    lines = []
    gt = {t: v for t, v in tag_affinity(units, "game").items()
          if tagdict.tags.get(t, {}).get("group") != VIDEO_GROUP}
    pos = [t for t, v in sorted(gt.items(), key=lambda kv: (-kv[1], kv[0])) if v > 0][:3]
    if pos:
        lines.append(f"가장 잘 맞는 결은 {_fmt_list(L(t) for t in pos)}예요.")
    else:
        lines.append("아직 취향을 말해 줄 게임 기록이 없어요. steam-buddy 기록이나 만족도를 가져와 보세요.")
    played = sorted((u for u in games if u.hours > 0), key=lambda u: (-u.hours, u.key))
    if played:
        top = played[0]
        total_h = sum(u.hours for u in games)
        paid = sum(u.price for u in games if u.owned)
        s = f"가장 오래 한 게임은 {top.label}({top.hours:.0f}시간)이고, 모은 게임 {len(games)}개에 총 {total_h:.0f}시간을 썼어요."
        if paid > 0 and total_h >= 1:
            s += f" 평균 시간당 {paid / total_h:,.0f}원이에요."
        lines.append(s)
    else:
        lines.append("플레이 시간 기록이 아직 없어요.")
    vt = {t: v for t, v in tag_affinity(units, "video").items() if v > 0}
    vtop = [t for t, _ in sorted(vt.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    if videos and vtop:
        n = sum(u.count for u in videos)
        lines.append(f"영상은 {_fmt_list(L(t) for t in vtop)} 쪽을 자주 봤어요. (시청 {n}회, 서로 다른 영상 {len(videos)}개)")
    elif videos:
        lines.append(f"영상 {len(videos)}개를 봤지만 태그를 붙일 만한 단서가 없어요. tags edit 로 붙여 보세요.")
    else:
        lines.append("유튜브 시청 기록이 없어요. 테이크아웃 watch-history.json 을 가져오면 채워져요.")
    shots = sum(u.shots for u in games)
    if shots:
        dtag = {}
        for u in games:
            if u.shots:
                for t, w in u.vec.items():
                    dtag[t] = dtag.get(t, 0.0) + w * u.shots
        mood = {}
        for u in games:
            for m, n in u.moods.items():
                mood[m] = mood.get(m, 0) + n
        mtop = [m for m, _ in sorted(mood.items(), key=lambda kv: (-kv[1], kv[0]))[:2]]
        dtop = [t for t, _ in sorted(dtag.items(), key=lambda kv: (-kv[1], kv[0])) if
                tagdict.tags.get(t, {}).get("group") != VIDEO_GROUP][:2]
        bits = [L(t) for t in dtop] + mtop
        lines.append(f"스크린샷으로 남긴 순간은 {_fmt_list(bits) or '여러 장면'} 쪽이에요. (장면 {shots}장)")
    else:
        lines.append("남긴 스크린샷 기록이 없어요. game-diary 를 가져오면 어떤 장면을 찍는지 보여 드려요.")
    neg = [t for t, v in sorted(gt.items(), key=lambda kv: (kv[1], kv[0])) if v < -0.05][:2]
    unplayed = [u for u in games if u.owned and u.hours < 0.1]
    if neg:
        lines.append(f"{_fmt_list(L(t) for t in neg)} 쪽은 만족도가 낮았어요. 다음엔 조심해요.")
    elif unplayed:
        lines.append(f"아직 안 켜 본 게임이 {len(unplayed)}개 쌓여 있어요. 새로 사기 전에 이것부터요.")
    else:
        lines.append("마음에 안 든 장르는 아직 보이지 않아요. 만족도(rate)를 매기면 피할 것도 알려 드려요.")
    return lines


def polish_with_llm(lines, client):
    """선택: 로컬 LLM 으로 5줄의 말투만 다듬는다. 형식이 틀리면 원문을 그대로 쓴다."""
    from ._jjkit import llm
    system = ("다음 5줄 프로필의 말투만 자연스러운 한국어 해요체로 다듬어요. "
              "숫자, 게임 이름, 태그 이름을 바꾸거나 새 사실을 더하지 마세요. "
              '정확히 5개 문자열이 든 JSON {"lines": [...]} 만 답하세요.')
    try:
        data = client.chat_json([{"role": "system", "content": system},
                                 {"role": "user", "content": "\n".join(lines)}])
    except (llm.LLMError, ValueError, OSError):
        return lines
    new = data.get("lines") if isinstance(data, dict) else None
    if isinstance(new, list) and len(new) == 5 and all(isinstance(s, str) and s.strip() for s in new):
        orig_nums = "".join(ch for s in lines for ch in s if ch.isdigit())
        if "".join(ch for s in new for ch in s if ch.isdigit()) == orig_nums:
            return [s.strip() for s in new]
    return lines
