"""CLAUDE.md 린터: 크기, 중복 규칙, 모순 키워드 쌍, 빈 섹션, 낡은 경로."""
import re
from pathlib import Path

from .report import Report, clip, est_tokens

MAX_LINES = 200
MAX_TOKENS = 4000            # 추정(문자/4)
MAX_LINE_CHARS = 300
NEG = re.compile(r"하지 마|하지 않|금지|말 것|말고|마라|마세요|절대|never|don't|do not|avoid|must not|않는다|안 ?됨", re.I)
POS = re.compile(r"항상|반드시|해야|must|always|should|use |사용한다|사용할 것", re.I)
STRIP = re.compile(r"[`*_>#\-\[\]()]|^\s*\d+\.|하지 마|하지 않|금지|말 것|마라|마세요|절대|never|don't|do not|always|항상|반드시|must|not", re.I)
PATH_RE = re.compile(r"`([~./\w-][\w./~-]*[/.][\w./-]*\w)`")
STOP = {"the", "a", "to", "is", "and", "of", "in", "을", "를", "은", "는", "이", "가", "에", "한다", "할", "것"}


_PARTICLE = re.compile(r"(에서|으로|에게|은|는|이|가|을|를|의|로|와|과|도|에)$")


def _stem(w: str) -> str:
    """아주 거친 어간 처리: 한글 단어는 조사를 떼고 앞 2음절만 쓴다(휴리스틱)."""
    if re.search(r"[가-힣]", w):
        w = _PARTICLE.sub("", w) if len(w) > 2 else w
        return w[:2]
    return w


def _words(line: str) -> frozenset:
    ws = (w for w in re.split(r"[\s,.:;/]+", STRIP.sub(" ", line.lower())) if len(w) > 1 and w not in STOP)
    return frozenset(_stem(w) for w in ws)


def _jac(a, b) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def rules(text: str) -> list:
    """(줄 번호, 규칙 텍스트). 코드블록 제외, 목록/문장 줄만."""
    out, in_code = [], False
    for i, raw in enumerate(text.splitlines(), 1):
        if raw.strip().startswith("```"):
            in_code = not in_code
            continue
        s = raw.strip()
        if in_code or not s or s.startswith("#") or s.startswith("|") or len(s) < 6:
            continue
        out.append((i, re.sub(r"^([-*]|\d+\.)\s+", "", s)))
    return out


def lint(path, root=None) -> list:
    """[(심각도, 종류, 줄, 설명)]"""
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    res = []
    toks = est_tokens(len(text))
    if len(lines) > MAX_LINES or toks > MAX_TOKENS:
        res.append(("경고", "크기", 0, f"{len(lines)}줄, 약 {toks} 토큰(추정). 매 세션 컨텍스트에 실리므로 {MAX_LINES}줄/{MAX_TOKENS} 토큰 이하 권장. 세부는 스킬이나 하위 폴더 CLAUDE.md로 분리."))
    for i, l in enumerate(lines, 1):
        if len(l) > MAX_LINE_CHARS:
            res.append(("정보", "긴 줄", i, f"{len(l)}자: {clip(l, 40)}"))
    rl = rules(text)
    ws = [(i, t, _words(t)) for i, t in rl]
    for a in range(len(ws)):
        for b in range(a + 1, len(ws)):
            (i, ti, wi), (j, tj, wj) = ws[a], ws[b]
            if ti.strip().lower() == tj.strip().lower() or (len(wi) >= 3 and _jac(wi, wj) >= 0.8):
                if bool(NEG.search(ti)) == bool(NEG.search(tj)):
                    res.append(("경고", "중복 규칙", j, f"{i}줄과 거의 같음: {clip(tj, 60)}"))
                    continue
            if len(wi) >= 2 and _jac(wi, wj) >= 0.5 and bool(NEG.search(ti)) != bool(NEG.search(tj)) and (POS.search(ti) or POS.search(tj)):
                res.append(("경고", "모순 의심", j, f"{i}줄 '{clip(ti, 40)}' vs '{clip(tj, 40)}' (같은 주제에 긍정/부정 키워드)"))
    # 빈 섹션
    heads = [(i, len(m.group(1)), m.group(2)) for i, l in enumerate(lines, 1) if (m := re.match(r"^(#{1,6})\s+(.*)", l))]
    in_code = False
    for k, (i, lvl, title) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines) + 1
        body = [l for l in lines[i:end - 1] if l.strip()]
        if not body:
            res.append(("정보", "빈 섹션", i, f"'{title}' 아래에 내용이 없음"))
    # 낡은 경로
    base = Path(root) if root else p.resolve().parent
    seen = set()
    for i, l in enumerate(lines, 1):
        for m in PATH_RE.finditer(l):
            ref = m.group(1)
            if ref in seen or ref.startswith(("http", "~")) or "*" in ref or "://" in ref:
                continue
            seen.add(ref)
            if "/" not in ref and not re.search(r"\.\w{1,5}$", ref):
                continue
            if not (base / ref).exists() and not Path(ref).is_absolute():
                res.append(("정보", "낡은 경로 의심", i, f"`{ref}` 가 {base} 아래에 없음"))
            elif Path(ref).is_absolute() and not Path(ref).exists():
                res.append(("정보", "낡은 경로 의심", i, f"`{ref}` 없음"))
    return res


def build_report(path, root=None) -> Report:
    rep = Report("CLAUDE.md 린트")
    rep.intro.append("휴리스틱 검사입니다. 모순/중복은 의심 목록이며 사람이 확인하세요. 파일은 읽기만 했습니다.")
    f = lint(path, root)
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    rep.add("요약", [f"파일: {path}", f"{len(text.splitlines())}줄, 약 {est_tokens(len(text))} 토큰(추정), 지적 {len(f)}건"])
    rep.add("지적 사항", [], ["심각도", "종류", "줄", "설명"], [list(x) for x in f] or [["-", "문제 없음", "", ""]])
    return rep
