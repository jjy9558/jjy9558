"""세션 간 반복된 사용자 지시 찾기: 스킬/슬래시 커맨드/훅 후보."""
import re
from dataclasses import dataclass, field

from ._jjkit import claude_logs
from .report import Report, clip, est_tokens

_NORM = re.compile(r"[0-9]+|[`'\"“”‘’()\[\]{}<>.,!?:;~*#]|(?:[\w./\\-]*[/\\][\w./\\-]+)")
STOP = {"해줘", "해주세요", "좀", "그리고", "the", "a", "to", "please", "and", "for", "of"}


def tokens(text: str) -> frozenset:
    t = _NORM.sub(" ", text.lower())
    return frozenset(w for w in t.split() if w not in STOP and len(w) > 1)


def jaccard(a, b) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass
class Cluster:
    rep: str
    toks: frozenset
    items: list = field(default_factory=list)       # (session_id, text)

    @property
    def sessions(self):
        return {s for s, _ in self.items}


def collect_prompts(root, min_chars: int = 8) -> list:
    out = []
    for m in claude_logs.load_messages(root):
        if m.role == "user" and m.text and len(m.text) >= min_chars:
            out.append((m.session_id, m.text))
    return out


def cluster(prompts: list, threshold: float = 0.6) -> list:
    cl: list = []
    for sid, text in prompts:
        tk = tokens(text)
        if len(tk) < 2:
            continue
        best, bs = None, 0.0
        for c in cl:
            s = jaccard(tk, c.toks)
            if s > bs:
                best, bs = c, s
        if best is not None and bs >= threshold:
            best.items.append((sid, text))
        else:
            cl.append(Cluster(text, tk, [(sid, text)]))
    return cl


def suggest(c: Cluster) -> str:
    t = c.rep.lower()
    if re.search(r"테스트|test|lint|린트|빌드|build|커밋|commit", t):
        return "훅 또는 슬래시 커맨드 (자동 실행 가능한 절차)"
    if re.search(r"리뷰|review|요약|정리|summar|보고", t):
        return "스킬 (반복되는 출력 형식과 절차)"
    return "슬래시 커맨드 또는 CLAUDE.md 한 줄 규칙"


def build_report(prompts: list, *, min_count: int = 3, min_sessions: int = 2, threshold: float = 0.6) -> Report:
    rep = Report("반복 프롬프트 감별기")
    rep.intro.append("비슷한 사용자 지시를 단어 집합 유사도(Jaccard)로 묶었습니다. 원문은 120자까지만 보여 주며 비밀값은 마스킹됩니다.")
    cs = [c for c in cluster(prompts, threshold) if len(c.items) >= min_count and len(c.sessions) >= min_sessions]
    cs.sort(key=lambda c: -len(c.items))
    rows, saved = [], 0
    for c in cs:
        per = est_tokens(sum(len(t) for _, t in c.items) // len(c.items))
        sv = per * (len(c.items) - 1)
        saved += sv
        rows.append([len(c.items), len(c.sessions), clip(c.rep), suggest(c), f"{sv} (추정)"])
    rep.add("요약", [f"사용자 지시 {len(prompts)}건 분석, 군집 {len(cs)}개 (조건: {min_count}회 이상, {min_sessions}개 세션 이상)",
                   f"입력 토큰 절감 추정 합계: {saved} (문자수/4, 추정치). 진짜 이득은 타이핑 시간과 일관성입니다."])
    rep.add("스킬/훅 후보", [], ["횟수", "세션 수", "대표 지시(120자)", "제안", "프롬프트 토큰 절감"], rows or [["-", "-", "반복 군집 없음", "", ""]])
    return rep
