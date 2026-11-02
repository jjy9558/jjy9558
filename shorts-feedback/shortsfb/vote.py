"""썸네일 후보 투표: 투표 만들기, 자기완결 페이지, 서버, 집계.

- 링크 토큰: 주소에 토큰(``/v/<토큰>``)이 없거나 틀리면 403.
- 1인 1표: 서버가 내려 준 쿠키의 무작위 ID를 해시해서 저장한다. 같은 쿠키로 다시 보내도 한 번만 센다.
  쿠키를 지우면 다시 투표할 수 있다(쿠키 방식의 한계). 대신 IP당 속도 제한이 연타를 막는다.
- 개인정보: IP와 쿠키 원문은 저장하지 않는다(쿠키 ID 해시, 선택지, 시각만).
- 페이지는 외부 리소스 없이 이미지를 data URI로 박고, 스크립트 없이 폼 POST로 동작한다.
"""
import base64
import hashlib
import html
import json
import math
import random
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs

from ._jjkit.httpkit import HttpError, TokenHandler, generate_token, make_server, route, token_matches

DEFAULT_PORT = 8791
MIN_SAMPLE = 10
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MIN_CANDIDATES, MAX_CANDIDATES = 2, 8
Z95 = 1.96
COOKIE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


# --------------------------------------------------------------------------
# 만들기
# --------------------------------------------------------------------------
def sniff_image(raw: bytes) -> tuple[str, str]:
    """(MIME, 확장자). 이미지가 아니면 ValueError."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", ".gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp", ".webp"
    raise ValueError("PNG/JPEG/GIF/WEBP 이미지가 아닙니다")


def polls_dir(data: Path) -> Path:
    return Path(data) / "polls"


def create_poll(data: Path, images: list, question: str = "", labels: list[str] | None = None) -> dict:
    if not MIN_CANDIDATES <= len(images) <= MAX_CANDIDATES:
        raise ValueError("후보는 %d~%d장이어야 합니다 (받은 수: %d)" % (MIN_CANDIDATES, MAX_CANDIDATES, len(images)))
    if labels and len(labels) != len(images):
        raise ValueError("--label 개수가 이미지 수와 다릅니다")
    poll_id = secrets.token_hex(4)
    folder = polls_dir(data) / poll_id
    cands, blobs = [], []
    for i, img in enumerate(images):  # 전부 검사한 뒤에 폴더를 만든다(실패하면 흔적을 남기지 않음)
        src = Path(img)
        try:
            raw = src.read_bytes()
        except OSError as e:
            raise ValueError("이미지를 읽을 수 없습니다: %s (%s)" % (src, e)) from e
        if len(raw) > MAX_IMAGE_BYTES:
            raise ValueError("이미지가 너무 큽니다(최대 %dMB): %s" % (MAX_IMAGE_BYTES // 1048576, src.name))
        try:
            mime, ext = sniff_image(raw)
        except ValueError as e:
            raise ValueError("%s: %s" % (src.name, e)) from e
        cid = "c%d" % (i + 1)
        # 라벨은 결과 표에서만 쓴다(원본 파일명에 개인정보가 있을 수 있어 stem만, 길이 제한).
        label = (labels[i] if labels else src.stem)[:40]
        cands.append({"id": cid, "label": label, "file": cid + ext, "mime": mime})
        blobs.append(raw)
    folder.mkdir(parents=True, exist_ok=True)
    for c, raw in zip(cands, blobs):
        (folder / c["file"]).write_bytes(raw)
    poll = {"id": poll_id, "token": generate_token(), "question": (question or "어느 썸네일이 더 눈에 띄나요?")[:120],
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "candidates": cands}
    from .store import write_json
    write_json(folder / "poll.json", poll)
    return poll


def load_poll(data: Path, poll_id: str | None = None) -> dict:
    from .store import read_json
    root = polls_dir(data)
    if poll_id:
        if not re.fullmatch(r"[0-9a-f]{8}", poll_id):
            raise ValueError("투표 ID 형식이 아닙니다: %s" % poll_id)
        poll = read_json(root / poll_id / "poll.json", None)
        if poll is None:
            raise ValueError("투표를 찾을 수 없습니다: %s" % poll_id)
        return poll
    polls = []
    if root.is_dir():
        for d in root.iterdir():
            p = read_json(d / "poll.json", None) if d.is_dir() else None
            if p:
                polls.append(p)
    if not polls:
        raise ValueError("투표가 없습니다. 먼저 `vote create <이미지...>`를 실행하세요")
    return max(polls, key=lambda p: (p.get("created", ""), p["id"]))


def list_polls(data: Path) -> list[dict]:
    from .store import read_json
    out = []
    root = polls_dir(data)
    if root.is_dir():
        for d in sorted(root.iterdir()):
            p = read_json(d / "poll.json", None) if d.is_dir() else None
            if p:
                out.append(p)
    return sorted(out, key=lambda p: (p.get("created", ""), p["id"]))


# --------------------------------------------------------------------------
# 표 저장과 집계
# --------------------------------------------------------------------------
def hash_voter(voter_id: str) -> str:
    return hashlib.sha256(("sfb-voter:" + voter_id).encode("utf-8")).hexdigest()[:20]


class VoteBox:
    """투표 한 건을 줄 단위 JSON으로 덧붙여 저장한다(스레드 안전)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.seen: set[str] = {v["v"] for v in read_votes(self.path)}

    def record(self, voter_id: str, choice: str) -> bool:
        """처음이면 True(집계됨), 이미 투표한 쿠키면 False."""
        h = hash_voter(voter_id)
        with self.lock:
            if h in self.seen:
                return False
            line = json.dumps({"v": h, "c": choice, "t": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                              ensure_ascii=False)
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
            self.seen.add(h)
            return True


def read_votes(path: Path) -> list[dict]:
    rows = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return rows
    for ln in text.splitlines():
        try:
            obj = json.loads(ln)
        except ValueError:
            continue  # 쓰다 끊긴 마지막 줄 등은 건너뛴다
        if isinstance(obj, dict) and "v" in obj and "c" in obj:
            rows.append(obj)
    return rows


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def tally(poll: dict, votes: list[dict]) -> dict:
    ids = [c["id"] for c in poll["candidates"]]
    counts = {i: 0 for i in ids}
    seen = set()
    for v in votes:  # 파일에 같은 쿠키가 두 줄 있어도(수동 편집 등) 한 번만
        if v["v"] in seen or v["c"] not in counts:
            continue
        seen.add(v["v"])
        counts[v["c"]] += 1
    n = sum(counts.values())
    rows = []
    for c in poll["candidates"]:
        k = counts[c["id"]]
        lo, hi = wilson(k, n)
        rows.append({"id": c["id"], "label": c["label"], "votes": k, "pct": round(100.0 * k / n, 1) if n else 0.0,
                     "ci_low": round(100 * lo, 1), "ci_high": round(100 * hi, 1)})
    ranked = sorted(rows, key=lambda r: (-r["votes"], r["id"]))
    warnings = []
    if n == 0:
        warnings.append("아직 투표가 없습니다")
    elif n < MIN_SAMPLE:
        warnings.append("표본 %d명(10 미만): 결과가 우연일 수 있어 신뢰하기 어렵습니다" % n)
    if n and len(ranked) >= 2 and ranked[0]["ci_low"] <= ranked[1]["ci_high"] and n >= 1:
        warnings.append("1위와 2위의 95%% 신뢰 구간이 겹칩니다: 순위를 단정할 수 없습니다 (%s vs %s)" %
                        (ranked[0]["label"], ranked[1]["label"]))
    return {"poll_id": poll["id"], "question": poll["question"], "n": n, "rows": rows,
            "winner": ranked[0]["label"] if n and ranked[0]["votes"] > ranked[1]["votes"] else None,
            "warnings": warnings}


def format_result(res: dict) -> str:
    from ._jjkit import textwidth
    lines = ["투표 %s: %s" % (res["poll_id"], res["question"]), "표본: %d명" % res["n"]]
    rows = [(r["label"], r["votes"], "%.1f%%" % r["pct"], "%.0f~%.0f%%" % (r["ci_low"], r["ci_high"]))
            for r in res["rows"]]
    lines += textwidth.table(rows, header=("후보", "표", "비율", "95% 신뢰구간"), right_cols=(1, 2, 3))
    for w in res["warnings"]:
        lines.append("경고: " + w)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 속도 제한
# --------------------------------------------------------------------------
class RateLimiter:
    """키(IP)별 슬라이딩 윈도 제한. ``clock``은 테스트에서 바꿔 끼운다."""

    def __init__(self, limit: int, window: float = 60.0, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.hits: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self.lock:
            if len(self.hits) > 5000:  # 메모리 보호: 오래된 키 정리
                for k in [k for k, v in self.hits.items() if not v or now - v[-1] > self.window]:
                    del self.hits[k]
            q = [t for t in self.hits.get(key, []) if now - t < self.window]
            if len(q) >= self.limit:
                self.hits[key] = q
                return False
            q.append(now)
            self.hits[key] = q
            return True


# --------------------------------------------------------------------------
# 페이지
# --------------------------------------------------------------------------
CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1a1a1a;--line:#d0d0d0;--accent:#2563eb}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#eee;--line:#444;--accent:#6ea0ff}}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font-family:system-ui,"Noto Sans KR","Malgun Gothic",sans-serif}
main{max-width:960px;margin:0 auto}h1{font-size:1.25rem;margin:.5rem 0 1rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:12px}
button.cand{padding:0;border:2px solid var(--line);border-radius:10px;background:none;cursor:pointer;overflow:hidden}
button.cand:hover,button.cand:focus-visible{border-color:var(--accent);outline:none}
button.cand img{display:block;width:100%;height:auto}
p.note{opacity:.75;font-size:.9rem}
"""


def _page(title: str, body: str) -> str:
    return ("<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta name=\"robots\" content=\"noindex,nofollow\">"
            "<title>%s</title><style>%s</style></head><body><main>%s</main></body></html>"
            % (html.escape(title), CSS, body))


def image_uri(folder: Path, cand: dict) -> str:
    raw = (Path(folder) / cand["file"]).read_bytes()
    return "data:%s;base64,%s" % (cand["mime"], base64.b64encode(raw).decode("ascii"))


def render_vote_page(poll: dict, folder: Path, rng: random.Random | None = None) -> str:
    """후보 순서는 매번 섞는다(위치 편향 줄이기). 후보 이름은 화면에 보이지 않는다."""
    cands = list(poll["candidates"])
    (rng or random.SystemRandom()).shuffle(cands)
    btns = "".join(
        "<button class=\"cand\" type=\"submit\" name=\"choice\" value=\"%s\" aria-label=\"후보 %d번 선택\">"
        "<img alt=\"썸네일 후보 %d\" src=\"%s\"></button>" % (html.escape(c["id"]), i + 1, i + 1, image_uri(folder, c))
        for i, c in enumerate(cands))
    body = ("<h1>%s</h1><form method=\"post\" action=\"vote\"><div class=\"grid\">%s</div></form>"
            "<p class=\"note\">마음에 드는 그림을 눌러 주세요. 한 사람이 한 번만 투표할 수 있습니다. "
            "IP 주소와 이름은 저장하지 않습니다.</p>" % (html.escape(poll["question"]), btns))
    return _page(poll["question"], body)


def render_done_page(counted: bool) -> str:
    msg = "투표했습니다. 고맙습니다!" if counted else "이미 투표하셨습니다. 한 번만 집계됩니다."
    return _page("투표", "<h1>%s</h1>" % msg)


CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; form-action 'self'; "
       "base-uri 'none'; frame-ancestors 'none'")


# --------------------------------------------------------------------------
# 서버
# --------------------------------------------------------------------------
class VoteApp:
    def __init__(self, data: Path, poll: dict, *, get_limit: int = 60, post_limit: int = 6,
                 bad_token_limit: int = 10, window: float = 60.0, clock=time.monotonic,
                 secure_cookie: bool = False, trust_xff: bool = False):
        self.poll = poll
        self.folder = polls_dir(data) / poll["id"]
        self.box = VoteBox(self.folder / "votes.jsonl")
        self.get_rl = RateLimiter(get_limit, window, clock)
        self.post_rl = RateLimiter(post_limit, window, clock)
        self.bad_rl = RateLimiter(bad_token_limit, window, clock)
        self.cookie_name = "sfb_" + poll["id"]
        self.secure_cookie = secure_cookie
        self.trust_xff = trust_xff
        self.ids = {c["id"] for c in poll["candidates"]}


class VoteHandler(TokenHandler):
    server_version = "shortsfb"
    max_body = 4096

    def client_ip(self) -> str:
        app: VoteApp = self.app
        if app.trust_xff:
            xff = self.headers.get("X-Forwarded-For", "")
            if xff.strip():
                return xff.split(",")[-1].strip()  # 신뢰하는 프록시가 덧붙인 가장 오른쪽 값
        return self.client_address[0]

    def _split(self) -> tuple[str, str]:
        tok, _, rest = self.route_rest.partition("/")
        return tok, rest

    def _gate(self, limiter: RateLimiter) -> None:
        app: VoteApp = self.app
        ip = self.client_ip()
        tok, _ = self._split()
        if not token_matches(tok, app.poll["token"]):
            if not app.bad_rl.allow(ip):
                raise HttpError(429, "요청이 너무 많습니다")
            raise HttpError(403, "링크가 올바르지 않습니다")
        if not limiter.allow(ip):
            self.close_connection = True
            raise HttpError(429, "요청이 너무 많습니다. 잠시 뒤에 다시 시도하세요")

    def _cookie(self) -> str:
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie", ""))
        except Exception:  # noqa: BLE001 - 깨진 쿠키 헤더는 없는 것으로
            return ""
        m = c.get(self.app.cookie_name)
        return m.value if m and COOKIE_RE.match(m.value) else ""

    def _set_cookie(self, value: str) -> dict[str, str]:
        attrs = "%s=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax" % (self.app.cookie_name, value)
        if self.app.secure_cookie:
            attrs += "; Secure"
        return {"Set-Cookie": attrs}

    @route("GET", "/v/", auth=False, prefix=True)
    def page(self, q):
        app: VoteApp = self.app
        self._gate(app.get_rl)
        if "/" not in self.route_rest:  # 상대 경로 폼 주소("vote")가 맞으려면 끝에 /가 필요하다
            self.send_response(302)
            self.send_header("Location", "/v/%s/" % self.route_rest)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            return
        if self._split()[1] != "":
            raise HttpError(404)
        self.send_html(200, render_vote_page(app.poll, app.folder), {"Content-Security-Policy": CSP})

    @route("POST", "/v/", auth=False, prefix=True)
    def vote(self, q):
        app: VoteApp = self.app
        self._gate(app.post_rl)
        if self._split()[1] != "vote":
            raise HttpError(404)
        raw = self.read_body()
        if "json" in (self.headers.get("Content-Type") or "").lower():
            try:
                obj = json.loads(raw.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                raise HttpError(400, "JSON 형식이 아닙니다")
            choice = str(obj.get("choice", "")) if isinstance(obj, dict) else ""
            want_json = True
        else:
            choice = (parse_qs(raw.decode("utf-8", "replace")).get("choice") or [""])[0]
            want_json = False
        if choice not in app.ids:
            raise HttpError(400, "없는 후보입니다")
        voter = self._cookie()
        fresh = not voter
        if fresh:
            voter = secrets.token_urlsafe(18)
        counted = app.box.record(voter, choice)
        headers = self._set_cookie(voter) if fresh else {}
        if want_json:
            self.send_json(200, {"ok": True, "counted": counted}, headers)
        else:
            headers["Content-Security-Policy"] = CSP
            self.send_html(200, render_done_page(counted), headers)


def build_server(data: Path, poll: dict, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT, **app_kw):
    app = VoteApp(data, poll, **app_kw)
    # 서버 token은 링크 토큰과 같게 둔다(공개 바인드 시 토큰 없는 서버를 httpkit이 거부하므로)
    return make_server(VoteHandler, host=host, port=port, token=poll["token"], app=app)


def vote_url(host: str, port: int, poll: dict) -> str:
    return "http://%s:%d/v/%s/" % (host, port, poll["token"])
