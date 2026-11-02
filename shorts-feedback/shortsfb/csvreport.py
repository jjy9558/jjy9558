"""YouTube Studio에서 내보낸 CSV를 주제 유형별로 요약한다.

가정(README에도 적음):
- 내보내기 열 이름은 언어/시기/탭마다 다르므로 **정규화한 열 이름 사전**으로 찾는다
  (소문자, 괄호 안 단위 제거, 공백·기호 제거 후 비교. 한글/영문 둘 다). 못 찾는 열은 비워 두고 리포트에 "없음"으로 표시한다.
  틀리게 찾으면 ``--map 필드=열이름``으로 직접 지정한다.
- 첫 데이터 줄이 "합계/Total" 요약 줄이면 건너뛴다.
- 평균 시청 비율(유지율) 열이 없고 평균 시청 시간과 길이가 있으면 ``평균 시청 시간 / 길이``로 계산한다.
- 주제 유형은 ① 유형/주제 열 ② ``types.json``(유형: [키워드]) 제목 매칭 ③ 제목 앞 ``[유형]`` 순으로 정하고, 없으면 "기타".
- 인코딩은 UTF-8(BOM 허용), 실패하면 CP949. 구분자는 쉼표/탭/세미콜론을 자동 판별한다.
"""
import csv
import io
import json
import re
import statistics
from pathlib import Path

# 필드 -> 정규화된 열 이름 후보
SYNONYMS = {
    "id": ["content", "콘텐츠", "videoid", "동영상id", "id", "영상id"],
    "title": ["videotitle", "동영상제목", "title", "제목", "영상제목", "contenttitle"],
    "published": ["videopublishtime", "동영상게시시간", "publishdate", "publishedat", "게시일", "게시시간", "동영상게시일"],
    "duration": ["duration", "길이", "동영상길이", "영상길이"],
    "views": ["views", "조회수"],
    "watch_hours": ["watchtimehours", "watchtime", "시청시간", "시청시간단위시간"],
    "subs": ["subscribers", "구독자", "subscribersgained", "구독자증가"],
    "impressions": ["impressions", "노출수", "노출"],
    "ctr": ["impressionsclickthroughrate", "impressionsctr", "노출클릭률", "클릭률", "ctr"],
    "avg_duration": ["averageviewduration", "평균시청지속시간", "평균시청시간", "평균조회지속시간", "avgviewduration"],
    "avg_pct": ["averagepercentageviewed", "averageviewpercentage", "평균시청비율", "평균조회율", "평균시청률",
                "평균조회비율", "avgpercentageviewed"],
    "stayed": ["viewedvsswipedaway", "stayedtowatch", "스와이프하지않고시청한비율", "시청vs스와이프", "시청비율스와이프",
               "swipedaway"],
    "likes": ["likes", "좋아요", "좋아요수"],
    "type": ["type", "유형", "주제", "topic", "category", "카테고리", "주제유형"],
}
NUMERIC = ("views", "watch_hours", "subs", "impressions", "ctr", "avg_pct", "stayed", "likes")
TOTAL_WORDS = {"total", "합계", "전체", "총계", "채널전체"}
LABELS = {"id": "콘텐츠 ID", "title": "제목", "published": "게시 시각", "duration": "길이", "views": "조회수",
          "watch_hours": "시청 시간", "subs": "구독자", "impressions": "노출수", "ctr": "노출 클릭률",
          "avg_duration": "평균 시청 시간", "avg_pct": "평균 시청 비율(유지율)", "stayed": "스와이프 안 하고 시청한 비율",
          "likes": "좋아요", "type": "유형"}


def hkey(h: str) -> str:
    s = re.sub(r"[\(（\[].*?[\)）\]]", "", str(h or ""))
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def map_columns(header: list[str], overrides: dict[str, str] | None = None) -> dict[str, int]:
    """필드 -> 열 번호. 정확히 같은 이름을 먼저, 없으면 후보가 열 이름에 들어 있는 경우(4글자 이상)를 쓴다."""
    keys = [hkey(h) for h in header]
    found: dict[str, int] = {}
    for field, wanted in (overrides or {}).items():
        w = hkey(wanted)
        if w in keys:
            found[field] = keys.index(w)
        else:
            raise ValueError("--map %s=%s: 그런 열이 없습니다 (열: %s)" % (field, wanted, ", ".join(header)))
    taken = set(found.values())
    for field, names in SYNONYMS.items():
        if field in found:
            continue
        for nm in names:
            if nm in keys and keys.index(nm) not in taken:
                found[field] = keys.index(nm)
                taken.add(keys.index(nm))
                break
    for field, names in SYNONYMS.items():
        if field in found or field == "id":
            continue
        for i, k in enumerate(keys):
            if i in taken or not k:
                continue
            if any(len(nm) >= 4 and nm in k for nm in names):
                found[field] = i
                taken.add(i)
                break
    return found


def parse_num(s) -> float | None:
    t = str(s if s is not None else "").strip().replace(",", "").replace("%", "").replace(" ", "")
    if t in ("", "-", "—", "N/A", "n/a"):
        return None
    m = re.fullmatch(r"-?\d+(?:\.\d+)?", t)
    return float(t) if m else None


def parse_duration(s) -> float | None:
    """초 단위. ``1:05``, ``0:01:05``, ``65``, ``1분 5초``를 받는다."""
    t = str(s if s is not None else "").strip()
    if not t or t in ("-", "—"):
        return None
    if re.fullmatch(r"\d+(?::\d{1,2}){1,2}", t):
        sec = 0
        for p in t.split(":"):
            sec = sec * 60 + int(p)
        return float(sec)
    m = re.fullmatch(r"(?:(\d+)\s*시간)?\s*(?:(\d+)\s*분)?\s*(?:(\d+)\s*초)?", t)
    if m and any(m.groups()):
        h, mi, se = (int(x or 0) for x in m.groups())
        return float(h * 3600 + mi * 60 + se)
    return parse_num(t)


def read_table(path) -> tuple[list[str], list[list[str]]]:
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("CSV가 비어 있습니다: %s" % path)
    return rows[0], rows[1:]


def load_types(path) -> dict[str, list[str]]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not all(isinstance(v, list) for v in data.values()):
        raise ValueError('types.json 형식: {"유형이름": ["키워드", ...], ...}')
    return {str(k): [str(x).lower() for x in v] for k, v in data.items()}


def classify(title: str, explicit: str, types: dict[str, list[str]] | None) -> str:
    if explicit.strip():
        return explicit.strip()
    low = title.lower()
    if types:
        best, best_hits = None, 0
        for name, kws in types.items():  # 키워드가 가장 많이 걸린 유형, 같으면 파일 앞쪽
            hits = sum(1 for k in kws if k and k in low)
            if hits > best_hits:
                best, best_hits = name, hits
        if best:
            return best
    m = re.match(r"\s*[\[【(]([^\]】)]{1,12})[\]】)]", title)
    return m.group(1).strip() if m else "기타"


def build(path, types_path=None, overrides=None, low_ratio: float = 0.5) -> dict:
    header, body = read_table(path)
    cols = map_columns(header, overrides)
    if "views" not in cols and "avg_pct" not in cols and "stayed" not in cols:
        raise ValueError("조회수/유지율 열을 찾지 못했습니다. 열: %s. --map views=열이름 처럼 지정하세요" % ", ".join(header))
    types = load_types(types_path) if types_path else None

    def cell(row, field):
        i = cols.get(field)
        return row[i].strip() if i is not None and i < len(row) else ""

    videos, seen = [], set()
    for row in body:
        vid, title = cell(row, "id"), cell(row, "title")
        if hkey(vid) in TOTAL_WORDS or hkey(title) in TOTAL_WORDS:
            continue
        if not vid and not title:
            continue
        key = vid or title
        if key in seen:
            continue
        seen.add(key)
        v = {"id": vid, "title": title, "published": cell(row, "published"),
             "duration": parse_duration(cell(row, "duration")), "avg_duration": parse_duration(cell(row, "avg_duration"))}
        for f in NUMERIC:
            v[f] = parse_num(cell(row, f))
        if v["avg_pct"] is None and v["avg_duration"] is not None and v["duration"]:
            v["avg_pct"] = round(100.0 * v["avg_duration"] / v["duration"], 1)
            v["avg_pct_derived"] = True
        v["type"] = classify(title, cell(row, "type"), types)
        videos.append(v)
    views = [v["views"] for v in videos if v["views"] is not None]
    med = statistics.median(views) if views else None
    groups: dict[str, list[dict]] = {}
    for v in videos:
        groups.setdefault(v["type"], []).append(v)

    def mean(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 1) if xs else None

    summary = []
    for name, vs in groups.items():
        vv = [v["views"] for v in vs if v["views"] is not None]
        wr = [(v["avg_pct"], v["views"]) for v in vs if v["avg_pct"] is not None and v["views"]]
        summary.append({"type": name, "n": len(vs), "views_mean": mean(vv),
                        "views_median": statistics.median(vv) if vv else None,
                        "retention_mean": mean([v["avg_pct"] for v in vs]),
                        "retention_weighted": round(sum(a * w for a, w in wr) / sum(w for _, w in wr), 1) if wr else None,
                        "stayed_mean": mean([v["stayed"] for v in vs]), "ctr_mean": mean([v["ctr"] for v in vs]),
                        "small_sample": len(vs) < 3})
    summary.sort(key=lambda r: (r["retention_mean"] is None, -(r["retention_mean"] or 0),
                                -(r["views_mean"] or 0), r["type"]))
    low = []
    if med:
        low = sorted((v for v in videos if v["views"] is not None and v["views"] < low_ratio * med),
                     key=lambda v: (v["views"], v["id"]))[:10]
    return {"file": Path(path).name, "videos": len(videos), "views_total": sum(views) if views else None,
            "views_median": med, "columns": {f: header[i] for f, i in cols.items()},
            "missing": [f for f in SYNONYMS if f not in cols and f in ("views", "avg_pct", "stayed", "ctr", "duration")],
            "types": summary, "low": [{"id": v["id"], "title": v["title"], "views": v["views"], "type": v["type"]}
                                      for v in low], "low_ratio": low_ratio,
            "retention_derived": any(v.get("avg_pct_derived") for v in videos)}


def _f(x, suffix=""):
    if x is None:
        return "-"
    return ("%d" % x if float(x).is_integer() else "%.1f" % x) + suffix


def format_text(rep: dict) -> str:
    from ._jjkit import textwidth
    lines = ["파일: %s, 영상 %d편, 조회수 합계 %s, 중앙값 %s" % (rep["file"], rep["videos"], _f(rep["views_total"]),
                                                       _f(rep["views_median"]))]
    rows = [(t["type"], t["n"] if not t["small_sample"] else "%d*" % t["n"], _f(t["views_mean"]), _f(t["views_median"]),
             _f(t["retention_mean"], "%"), _f(t["stayed_mean"], "%"), _f(t["ctr_mean"], "%")) for t in rep["types"]]
    lines += textwidth.table(rows, header=("유형", "편수", "평균 조회", "중앙 조회", "평균 유지율", "스와이프 안 함", "CTR"),
                             right_cols=(1, 2, 3, 4, 5, 6))
    if any(t["small_sample"] for t in rep["types"]):
        lines.append("* 3편 미만이라 평균을 믿기 어렵습니다")
    if rep["retention_derived"]:
        lines.append("참고: 평균 시청 비율 열이 없는 영상은 평균 시청 시간/길이로 계산했습니다")
    if rep["missing"]:
        lines.append("없는 열: " + ", ".join(LABELS[f] for f in rep["missing"]))
    if rep["low"]:
        lines.append("")
        lines.append("조회가 중앙값의 %d%% 미만인 편 (부검 후보: `autopsy add <ID> --views N --causes ...`)" %
                     round(rep["low_ratio"] * 100))
        for v in rep["low"]:
            lines.append("  %s  조회 %s  [%s] %s" % (v["id"] or "-", _f(v["views"]), v["type"], v["title"]))
    return "\n".join(lines)
