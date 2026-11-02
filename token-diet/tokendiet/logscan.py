"""Claude Code jsonl 스캔: 같은 파일 반복 읽기, 큰 출력, 비싼 툴, 실패 재시도 루프, 실측 usage."""
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ._jjkit import claude_logs
from .report import Report, clip, est_tokens, fmt_tok

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
TRIM_TO_TOKENS = 500       # 가정: 큰 출력을 줄이면 이 정도까지는 줄일 수 있다(추정 상한 계산용)
IMAGE_CHARS = 6000         # 가정: 이미지 1장 = 약 1500 토큰(문자 환산)
RETRY_RUN = 3              # 같은 도구가 연속 N회 실패하면 재시도 루프로 본다


@dataclass
class Call:
    id: str
    name: str
    inp: dict
    ts: str
    in_chars: int
    out_chars: int = 0
    error: bool = False
    has_result: bool = False


@dataclass
class Session:
    sid: str
    path: str = ""
    calls: list = field(default_factory=list)
    usage: Counter = field(default_factory=Counter)
    models: Counter = field(default_factory=Counter)
    cwd: str = ""


def _result_chars(content) -> int:
    if isinstance(content, str):
        return len(claude_logs.strip_reminders(content))
    n = 0
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                n += len(claude_logs.strip_reminders(b["text"]))
            elif isinstance(b, str):
                n += len(b)
            elif isinstance(b, dict) and b.get("type") == "image":
                n += IMAGE_CHARS
    return n


def load_sessions(root, stats=None) -> list:
    """sidechain(서브에이전트) 줄은 제외. 같은 message.id의 usage는 한 번만 센다."""
    sessions: dict = {}
    byid: dict = {}
    seen_msg = set()
    for path, obj in claude_logs.iter_records(root, stats=stats):
        if obj.get("isSidechain"):
            continue
        typ = obj.get("type")
        msg = obj.get("message")
        if typ not in ("user", "assistant") or not isinstance(msg, dict):
            continue
        sid = obj.get("sessionId") if isinstance(obj.get("sessionId"), str) else path.stem
        s = sessions.setdefault(sid, Session(sid, str(path), cwd=obj.get("cwd") or ""))
        content = msg.get("content")
        if typ == "assistant":
            if msg.get("model") == "<synthetic>":
                continue
            mid = msg.get("id")
            u = msg.get("usage")
            if isinstance(u, dict) and not (mid and mid in seen_msg):
                if mid:
                    seen_msg.add(mid)
                for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                    v = u.get(k)
                    if isinstance(v, int):
                        s.usage[k] += v
                s.usage["_turns"] += 1
                if msg.get("model"):
                    s.models[msg["model"]] += 1
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and isinstance(b.get("name"), str):
                        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                        c = Call(str(b.get("id")), b["name"], inp, str(obj.get("timestamp", "")), len(str(inp)))
                        s.calls.append(c)
                        byid[(sid, c.id)] = c
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = byid.get((sid, str(b.get("tool_use_id"))))
                    if c is not None:
                        c.out_chars += _result_chars(b.get("content"))
                        c.error = c.error or bool(b.get("is_error"))
                        c.has_result = True
    return [s for s in sessions.values() if s.calls or s.usage]


def _key(c: Call):
    i = c.inp
    return (str(i.get("file_path") or i.get("path") or ""), i.get("offset"), i.get("limit"))


def find_repeat_reads(s: Session) -> list:
    """(경로, 읽은 횟수, 불필요한 재읽기 수, 낭비 추정 토큰). 사이에 수정이 있으면 정당한 재읽기."""
    seen: dict = {}
    stats = defaultdict(lambda: [0, 0, 0])
    for c in s.calls:
        if c.name == "Read":
            k = _key(c)
            if not k[0]:
                continue
            st = stats[k[0]]
            st[0] += 1
            if k in seen:
                st[1] += 1
                st[2] += est_tokens(c.out_chars)
            seen[k] = True
        elif c.name in EDIT_TOOLS:
            p = str(c.inp.get("file_path") or c.inp.get("notebook_path") or "")
            for k in [k for k in seen if k[0] == p]:
                del seen[k]
    return sorted(((p, a, b, t) for p, (a, b, t) in stats.items() if b > 0), key=lambda x: -x[3])


def find_big_outputs(s: Session, min_tokens: int) -> list:
    out = []
    for c in s.calls:
        t = est_tokens(c.out_chars)
        if t >= min_tokens:
            out.append((c, t, max(t - TRIM_TO_TOKENS, 0)))
    return sorted(out, key=lambda x: -x[1])


def find_retry_loops(s: Session) -> list:
    loops, run = [], []
    for c in s.calls + [None]:
        if c is not None and c.error and (not run or run[-1].name == c.name):
            run.append(c)
            continue
        if len(run) >= RETRY_RUN:
            loops.append(run)
        run = [c] if (c is not None and c.error) else []
    return loops


def call_label(c: Call) -> str:
    i = c.inp
    detail = i.get("file_path") or i.get("command") or i.get("pattern") or i.get("url") or i.get("query") or ""
    return clip(f"{c.name} {detail}", 80)


def build_report(sessions: list, *, min_tokens: int = 2000, top: int = 10, source: str = "") -> Report:
    rep = Report("토큰 다이어트 리포트: 세션 로그 스캔")
    rep.intro.append("토큰 수는 '문자 수 / 4' 추정치입니다. 정확한 토큰이 아니며, 표시된 '실측'만 message.usage 값입니다.")
    rep.intro.append("서브에이전트(sidechain) 줄은 제외했습니다. 원본 로그는 읽기만 했습니다.")
    n_calls = sum(len(s.calls) for s in sessions)
    rep.add("요약", [f"입력: {clip(source, 100)}", f"세션 {len(sessions)}개, 도구 호출 {n_calls}회"])

    tot = Counter()
    for s in sessions:
        tot.update(s.usage)
    if tot:
        rep.add("실측 사용량 (message.usage, 중복 메시지 제외)",
                ["입력/캐시 토큰은 컨텍스트가 길수록 턴마다 반복 계산되므로, 불필요한 출력이 남기는 비용은 아래 추정보다 클 수 있습니다."],
                ["항목", "토큰(실측)"],
                [["input_tokens", fmt_tok(tot["input_tokens"])],
                 ["cache_creation_input_tokens", fmt_tok(tot["cache_creation_input_tokens"])],
                 ["cache_read_input_tokens", fmt_tok(tot["cache_read_input_tokens"])],
                 ["output_tokens", fmt_tok(tot["output_tokens"])],
                 ["어시스턴트 턴 수", tot["_turns"]]])

    rr_rows, saved_rr = [], 0
    for s in sessions:
        for p, n, extra, t in find_repeat_reads(s):
            rr_rows.append([s.sid[:8], clip(p, 70), n, extra, fmt_tok(t) + " (추정)"])
            saved_rr += t
    rr_rows.sort(key=lambda r: -r[3])
    rep.add("1. 같은 파일 반복 읽기", ["사이에 수정이 없는데 같은 범위를 다시 읽은 경우만 셉니다. 절감 포인트: 읽은 내용을 재사용하거나, 필요한 부분만 offset/limit로 읽기."]
            + [f"반복 읽기로 낭비된 추정 합계: {fmt_tok(saved_rr)} 토큰 (추정)"],
            ["세션", "파일", "읽은 횟수", "불필요한 재읽기", "낭비 추정"], rr_rows[:top] or [["-", "없음", "", "", ""]])

    big_rows, saved_big = [], 0
    allbig = []
    for s in sessions:
        for c, t, sv in find_big_outputs(s, min_tokens):
            allbig.append((s, c, t, sv))
    allbig.sort(key=lambda x: -x[2])
    for s, c, t, sv in allbig:
        saved_big += sv
    for s, c, t, sv in allbig[:top]:
        big_rows.append([s.sid[:8], call_label(c), fmt_tok(t) + " (추정)", fmt_tok(sv) + " (상한 추정)"])
    rep.add("2. 큰 출력의 툴 호출", [f"출력이 {min_tokens} 토큰(추정) 이상인 호출 {len(allbig)}건. 절감 포인트: head/tail, grep -n, limit, 출력 리다이렉트 후 필요한 줄만 읽기.",
                              f"절감 상한 추정: {fmt_tok(saved_big)} 토큰 (가정: 각 출력을 {TRIM_TO_TOKENS} 토큰까지 줄일 수 있다고 봄, 추정)"],
            ["세션", "호출", "출력 크기", "줄이면"], big_rows or [["-", "없음", "", ""]])

    agg = defaultdict(lambda: [0, 0, 0])
    for s in sessions:
        for c in s.calls:
            a = agg[c.name]
            a[0] += 1
            a[1] += est_tokens(c.out_chars)
            a[2] += est_tokens(c.in_chars)
    ex = sorted(agg.items(), key=lambda kv: -(kv[1][1] + kv[1][2]))[:top]
    rep.add("3. 비싼 도구 순위 (결과+입력 추정 토큰)", ["결과 크기 기준입니다. 비싼 것이 나쁜 것은 아니며, 호출 횟수와 평균을 같이 보세요."],
            ["도구", "호출", "결과 토큰(추정)", "입력 토큰(추정)", "호출당 평균 결과"],
            [[n, a[0], fmt_tok(a[1]), fmt_tok(a[2]), fmt_tok(a[1] // max(a[0], 1))] for n, a in ex] or [["-", "", "", "", ""]])

    loop_rows = []
    for s in sessions:
        for run in find_retry_loops(s):
            wasted = sum(est_tokens(c.out_chars + c.in_chars) for c in run)
            loop_rows.append([s.sid[:8], run[0].name, len(run), call_label(run[0]), fmt_tok(wasted) + " (추정)"])
    rep.add("4. 실패 후 재시도 루프", [f"같은 도구가 연속 {RETRY_RUN}회 이상 오류로 끝난 구간. 절감 포인트: 3회째 실패하면 원인을 먼저 확인하거나 접근을 바꾸기."],
            ["세션", "도구", "연속 실패", "첫 호출", "소모 추정"], loop_rows[:top] or [["-", "없음", "", "", ""]])

    rep.add("절감 요약 (모두 추정)", ["서로 겹칠 수 있어 단순 합산은 상한에 가깝습니다."],
            ["항목", "토큰(추정)"], [["반복 읽기", fmt_tok(saved_rr)], ["큰 출력 줄이기(상한)", fmt_tok(saved_big)],
                                  ["재시도 루프", fmt_tok(sum(est_tokens(c.out_chars + c.in_chars) for s in sessions for r in find_retry_loops(s) for c in r))]])
    return rep
