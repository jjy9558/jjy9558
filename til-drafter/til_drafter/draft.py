# -*- coding: utf-8 -*-
"""엔트리 -> 프로젝트별 TIL 초안(Markdown, 한국어)."""
import re
from collections import Counter, OrderedDict

from .redact import RULE_LABELS

WEEKDAYS = "월화수목금토일"
FILE_PATH_KEYS = ("file_path", "path", "notebook_path")
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
READ_TOOLS = ("Read",)

PROMPT_LIMIT = 150
SUMMARY_LIMIT = 300
CANDIDATE_LIMIT = 180
MAX_PROMPTS = 6
MAX_FILES = 8
MAX_TOOLS = 10
MAX_CANDIDATES = 3

_KEYWORDS = re.compile(
    r"원인|해결|이유|때문|문제는|알고\s*보니|깨달|"
    r"\bbecause\b|\bfix(?:ed|es)?\b|\broot\s+cause\b|\bsolved\b|\bresolved\b|\bturns?\s+out\b",
    re.I,
)
_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
_SENT_SPLIT = re.compile(r"(?<=[.!?。])\s+|\n+")
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")


def project_name(cwd):
    """cwd 의 마지막 폴더명 (Windows/POSIX 경로 모두 지원)."""
    if not cwd:
        return "(알 수 없음)"
    parts = [p for p in re.split(r"[\\/]+", cwd) if p]
    if not parts:
        return "(루트)"
    last = parts[-1]
    if re.fullmatch(r"[A-Za-z]:", last):
        return "(루트)"
    return last


def basename(path):
    parts = [p for p in re.split(r"[\\/]+", str(path)) if p]
    return parts[-1] if parts else ""


def trim(text, limit):
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) <= limit:
        return t
    return t[: limit - 1].rstrip() + "…"


def _clean_sentence(s):
    s = _BULLET.sub("", s.strip())
    s = s.lstrip("#> ").strip()
    s = s.replace("**", "").replace("__", "")
    return s.strip()


def candidate_sentences(texts):
    """어시스턴트 텍스트에서 '배운 점' 후보 문장을 점수순으로 추려 시간순으로 반환."""
    scored = []
    seen = set()
    idx = 0
    for text in texts:
        body = _FENCE.sub(" ", text)
        for raw in _SENT_SPLIT.split(body):
            s = _clean_sentence(raw)
            if not (12 <= len(s) <= 400) or s.startswith("|"):
                continue
            hits = len(set(m.group(0).lower() for m in _KEYWORDS.finditer(s)))
            if not hits:
                continue
            key = re.sub(r"\W+", "", s.lower())
            if key in seen:
                continue
            seen.add(key)
            # 점수: 키워드 수, 너무 길지 않은 문장 선호, 나중에 나온 것 선호
            brevity = 1 if len(s) <= CANDIDATE_LIMIT else 0
            scored.append((hits, brevity, idx, s))
            idx += 1
    best = sorted(scored, key=lambda x: (x[0], x[1], x[2]), reverse=True)[:MAX_CANDIDATES]
    best.sort(key=lambda x: x[2])
    return [b[3] for b in best]


class _Project(object):
    def __init__(self, name):
        self.name = name
        self.prompts = []
        self.sessions = OrderedDict()  # sid -> 마지막 assistant 텍스트
        self.assistant_texts = []
        self.tools = Counter()
        self.writes = Counter()
        self.reads = Counter()
        self.n_entries = 0


def group_entries(entries):
    projects = OrderedDict()
    for e in entries:
        name = project_name(e.cwd)
        p = projects.get(name)
        if p is None:
            p = projects[name] = _Project(name)
        p.n_entries += 1
        if e.role == "user":
            p.prompts.extend(e.texts)
            p.sessions.setdefault(e.session_id, None)
        else:
            p.sessions.setdefault(e.session_id, None)
            if e.texts:
                p.assistant_texts.extend(e.texts)
                p.sessions[e.session_id] = e.texts[-1]
            for tname, tinput in e.tools:
                p.tools[tname] += 1
                fname = ""
                for k in FILE_PATH_KEYS:
                    v = tinput.get(k)
                    if isinstance(v, str) and v:
                        fname = basename(v)
                        break
                if not fname:
                    continue
                if tname in WRITE_TOOLS:
                    p.writes[fname] += 1
                elif tname in READ_TOOLS:
                    p.reads[fname] += 1
    return projects


def learn_from_entries(entries, redactor):
    """본문이 마스킹되기 전에 경로들에서 OS 사용자명을 학습."""
    for e in entries:
        redactor.learn(e.cwd)
        for t in e.texts:
            redactor.learn(t)
        for _, tinput in e.tools:
            for k in FILE_PATH_KEYS:
                v = tinput.get(k)
                if isinstance(v, str):
                    redactor.learn(v)


def _code_list(counter, limit, redact):
    items = counter.most_common(limit)
    parts = ["`%s` ×%d" % (redact(name).replace("`", "'"), n) for name, n in items]
    rest = len(counter) - len(items)
    s = ", ".join(parts)
    if rest > 0:
        s += " 외 %d개" % rest
    return s


def render_footer(redactor):
    lines = [
        "---",
        "",
        "> **게시 전 확인**",
        "> - 이 초안은 자동 생성되었고, 민감정보 마스킹은 정규식 기반 *최선 노력* 입니다. "
        "올리기 전에 처음부터 끝까지 직접 읽어 보세요.",
        "> - **장소**(동네·회사·집 주변)와 **시간**(몇 시에 작업했는지)처럼 생활 패턴이 드러나는 "
        "표현은 흐리게 바꾸거나 지우세요.",
        "> - 고객·회사·지인 이름, 내부 서비스명, 스크린샷에 찍힌 정보도 함께 확인하세요.",
    ]
    summary = redactor.summary()
    if summary:
        total = sum(n for _, _, n in summary)
        detail = ", ".join("%s %d" % (label.split(" (")[0], n) for _, label, n in summary)
        lines.append("> - 자동 마스킹 %d건: %s" % (total, detail))
    lines.append("")
    return lines


def build_draft(entries, day, redactor):
    """TIL 초안 Markdown 문자열을 만든다. 본문 문자열은 모두 redactor 를 거친다."""
    rd = redactor.redact
    learn_from_entries(entries, redactor)
    projects = group_entries(entries)
    wd = WEEKDAYS[day.weekday()]
    date_label = "%s (%s)" % (day.isoformat(), wd)

    names = OrderedDict((p.name, rd(p.name)) for p in projects.values())
    ordered = sorted(projects.values(), key=lambda p: -p.n_entries)
    main = ordered[0] if ordered else None

    # 본문 조각을 먼저 만들고(마스킹 포함) 제목 후보에 재사용한다.
    sections = []
    first_prompt = ""
    first_learn = ""
    for p in projects.values():
        disp = names[p.name]
        prompts = [trim(rd(x), PROMPT_LIMIT) for x in p.prompts]
        prompts = [x for x in prompts if len(x) >= 2]
        summaries = []
        for sid, last in p.sessions.items():
            if last:
                summaries.append(trim(rd(last), SUMMARY_LIMIT))
        cands = [trim(rd(c), CANDIDATE_LIMIT) for c in candidate_sentences(p.assistant_texts)]
        if main is p and prompts:
            first_prompt = prompts[0]
        if main is p and cands:
            first_learn = cands[0]

        s = ["## %s" % disp, ""]
        s.append("### 요청한 것")
        if prompts:
            for x in prompts[:MAX_PROMPTS]:
                s.append("- %s" % x)
            if len(prompts) > MAX_PROMPTS:
                s.append("- … 외 %d건" % (len(prompts) - MAX_PROMPTS))
        else:
            s.append("- (기록된 요청 없음)")
        s.append("")
        s.append("### 한 일")
        if summaries:
            for x in summaries:
                s.append("- %s" % x)
        else:
            s.append("- (어시스턴트 요약 없음)")
        s.append("")
        s.append("### 사용한 도구 / 파일")
        if p.tools:
            s.append("- 도구: %s" % _code_list(p.tools, MAX_TOOLS, rd))
        else:
            s.append("- 도구: (없음)")
        if p.writes:
            s.append("- 수정·생성한 파일: %s" % _code_list(p.writes, MAX_FILES, rd))
        if p.reads:
            s.append("- 읽은 파일: %s" % _code_list(p.reads, MAX_FILES, rd))
        s.append("")
        s.append("### 오늘 배운 것")
        if cands:
            for c in cands:
                s.append("- (후보) %s" % c)
        else:
            s.append("- (자동 추출된 후보가 없어요. 직접 적어 보세요)")
        s.append("- (직접 작성) ")
        s.append("")
        sections.append(s)

    n_sessions = len({e.session_id for e in entries})
    n_prompts = sum(len(p.prompts) for p in projects.values())
    proj_list = ", ".join("`%s`" % v for v in names.values())

    out = []
    if main is not None:
        main_disp = names[main.name]
        multi = "" if len(projects) == 1 else " 외 %d개" % (len(projects) - 1)
        out.append("# [TIL] %s — %s%s" % (day.isoformat(), main_disp, multi))
    else:
        out.append("# [TIL] %s" % day.isoformat())
    out.append("")
    out.append("> %s 자동 생성 초안입니다. 3~5분만 투자해서 다듬어 주세요." % date_label)
    out.append("")
    if main is not None:
        out.append("**제목 후보**")
        main_disp = names[main.name]
        out.append("- [TIL] %s %s 작업 기록" % (day.isoformat(), main_disp))
        if first_prompt:
            out.append("- %s: %s" % (main_disp, trim(first_prompt, 40)))
        if first_learn:
            out.append("- 오늘 배운 것: %s" % trim(first_learn, 40))
        out.append("")
        out.append(
            "**오늘 요약**: 프로젝트 %d개 (%s), 세션 %d개, 요청 %d건"
            % (len(projects), proj_list, n_sessions, n_prompts)
        )
        out.append("")
        for s in sections:
            out.extend(s)
    else:
        out.append("(이 날짜에는 Claude Code 기록이 없습니다.)")
        out.append("")
    out.extend(render_footer(redactor))
    return "\n".join(out).rstrip("\n") + "\n"


def format_check_report(redactor, day, stats):
    """--check 출력용. 규칙 id/설명/횟수만 담고 원본 값은 포함하지 않는다."""
    lines = [
        "[check] 날짜 %s | 파일 %d개 | 줄 %d개 (깨진 줄 %d개) | 해당 날짜 항목 %d개"
        % (day.isoformat(), stats.get("files", 0), stats.get("lines", 0),
           stats.get("malformed", 0), stats.get("entries", 0)),
    ]
    summary = redactor.summary()
    if not summary:
        lines.append("[check] 발동한 마스킹 규칙이 없습니다.")
    else:
        lines.append("[check] 발동한 마스킹 규칙 (원본 값은 표시하지 않음):")
        for rule, label, n in summary:
            lines.append("  - %-12s %s: %d건" % (rule, label, n))
    return "\n".join(lines)


__all__ = ["build_draft", "format_check_report", "project_name", "RULE_LABELS"]
