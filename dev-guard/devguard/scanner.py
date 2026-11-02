"""비밀 값 스캐너 (.env 커밋 도둑 경보의 핵심).

- scan_line()       : 한 줄에서 비밀 후보 찾기 (순수 함수, 테스트 용이)
- scan_filename()   : .env / 개인키 파일 이름 검사
- scan_staged()     : `git diff --cached` 로 스테이징된 '추가된 줄'만 검사
- mask_secret()     : 비밀 중간을 가려서 출력
"""
from __future__ import annotations

import fnmatch
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from devguard.common import run_git

ALLOW_MARKER = "devguard:allow"
MAX_LINE = 20000

# ----------------------------------------------------------------------------
# 기본 유틸
# ----------------------------------------------------------------------------


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in Counter(s).values())


def mask_secret(s: str) -> str:
    """비밀의 가운데를 가린다. 절대 전체를 그대로 돌려주지 않는다."""
    n = len(s)
    if n <= 3:
        return "*" * max(n, 1)
    if n <= 8:
        return s[:1] + "****"
    head = min(6, n // 4)
    tail = min(4, n // 8)
    return s[:head] + "****" + (s[-tail:] if tail else "")


_PLACEHOLDER_WORDS = (
    "example", "sample", "placeholder", "changeme", "change_me", "change-me",
    "your_", "your-", "yourkey", "yourtoken", "yoursecret", "<your", "dummy", "fake",
    "redacted", "insert", "replace_", "replace-", "replaceme", "todo", "fixme",
    "notasecret", "not_a_secret", "not-a-secret", "foobar", "lorem", "mysecret",
    "1234567890", "0123456789", "abcdefghij", "abcdefgh", "qwertyuiop",
    "_here", "-here", "secret_key_here", "xxxxx", "*****", "....", "\u2026",
)
_PLACEHOLDER_RE = re.compile(r"(?i)x{5,}|(.)\1{6,}|[<>]|\$\{|\{\{|%[sd]|\{\d*\}")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_HASH_LENGTHS = {32, 40, 56, 64, 96, 128}
_ENV_NAME_VALUE_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")

# jwt.io 등에 실린 유명한 예제 토큰
_KNOWN_EXAMPLES = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ",
)


def looks_placeholder(value: str) -> bool:
    low = value.lower()
    if any(w in low for w in _PLACEHOLDER_WORDS):
        return True
    if _PLACEHOLDER_RE.search(value):
        return True
    if any(value.startswith(k) for k in _KNOWN_EXAMPLES):
        return True
    return False


# ----------------------------------------------------------------------------
# 규칙
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    name: str
    label: str
    regex: "re.Pattern[str]"
    group: int = 0
    min_entropy: Optional[float] = 3.0
    validator: Optional[Callable[[str], bool]] = None
    reveal: bool = False  # True 면 값 자체가 비밀이 아니라 헤더일 뿐 (개인키 헤더)


def _openai_ok(v: str) -> bool:
    body = v[3:]
    return any(c.isdigit() for c in body) and any(c.isalpha() for c in body)


def _slack_token_ok(v: str) -> bool:
    return any(c.isdigit() for c in v)


_WEAK_PASSWORDS = {
    "pass", "password", "passwd", "pwd", "secret", "changeme", "admin", "root", "user",
    "test", "postgres", "mysql", "123456", "12345678", "guest", "default", "null", "none",
}


def _url_cred_ok(v: str) -> bool:
    if len(v) < 6 or v.lower() in _WEAK_PASSWORDS:
        return False
    if any(ch in v for ch in "${}%<>"):
        return False
    return True


RULES: Tuple[Rule, ...] = (
    Rule("private-key", "개인키(PRIVATE KEY)",
         re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
         min_entropy=None, reveal=True),
    Rule("anthropic-key", "Anthropic API 키",
         re.compile(r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{20,}")),
    Rule("openai-key", "OpenAI 계열 API 키 (sk-)",
         re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}"),
         min_entropy=3.5, validator=_openai_ok),
    Rule("stripe-live-key", "Stripe 라이브 키",
         re.compile(r"(?<![A-Za-z0-9_])[sr]k_live_[A-Za-z0-9]{20,}")),
    Rule("github-token", "GitHub 토큰 (ghp_/gho_/ghu_/ghs_/ghr_)",
         re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{36,255}")),
    Rule("github-fine-grained", "GitHub 세분화 토큰 (github_pat_)",
         re.compile(r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{22,}")),
    Rule("slack-token", "Slack 토큰 (xox*/xapp-)",
         re.compile(r"(?<![A-Za-z0-9])(?:xox[abposr]|xapp)-[A-Za-z0-9-]{10,}"),
         min_entropy=2.5, validator=_slack_token_ok),
    Rule("slack-webhook", "Slack 웹훅 URL",
         re.compile(r"https://hooks\.slack\.com/services/T[A-Za-z0-9]{6,}/B[A-Za-z0-9]{6,}/[A-Za-z0-9]{16,}"),
         min_entropy=None),
    Rule("discord-webhook", "Discord 웹훅 URL",
         re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d{6,}/[A-Za-z0-9_-]{30,}"),
         min_entropy=None),
    Rule("aws-access-key-id", "AWS 액세스 키 ID",
         re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[A-Z0-9]{16}(?![A-Z0-9])"),
         min_entropy=2.5),
    Rule("google-api-key", "Google API 키 (AIza)",
         re.compile(r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])")),
    Rule("jwt", "JWT 토큰",
         re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{10,}"),
         min_entropy=None),
    Rule("url-credentials", "URL 안의 비밀번호 (scheme://user:pass@host)",
         re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s:/@\"'<>]*:(?P<pw>[^\s@/\"'<>]{4,})@"),
         group=1, min_entropy=2.0, validator=_url_cred_ok),
)
_RULE_PRIORITY = {r.name: i for i, r in enumerate(RULES)}

# 변수 이름 판정 ---------------------------------------------------------------

_STRONG_WORDS = {"secret", "token", "password", "passwd", "passphrase", "apikey", "credential", "credentials"}
_NEUTRAL_LAST = {
    "id", "name", "url", "uri", "path", "file", "type", "length", "ttl", "count", "expiry",
    "expires", "header", "prefix", "field", "label", "env", "var", "ref", "hint", "mode",
    "size", "version", "endpoint", "dir", "host", "port", "user", "username", "regex",
}
_HASHY_WORDS = {"cache", "commit", "git", "sha", "hash", "digest", "checksum", "etag", "build",
                "content", "file", "lock", "integrity"}
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def name_words(name: str) -> List[str]:
    words: List[str] = []
    for part in re.split(r"[^A-Za-z0-9]+", name):
        if part:
            words.extend(w.lower() for w in _CAMEL_SPLIT.split(part) if w)
    return words


def classify_name(name: str) -> Optional[str]:
    """비밀 변수 이름이면 'password' 또는 'secret', 아니면 None."""
    words = name_words(name)
    if not words or words[-1] in _NEUTRAL_LAST:
        return None
    if any(w in ("password", "passwd", "passphrase") for w in words):
        return "password"
    if any(w in _STRONG_WORDS for w in words) or words[-1] == "key":
        return "secret"
    return None


_ASSIGN = re.compile(
    r"""(?<![A-Za-z0-9_.\-])(?P<name>[A-Za-z_][A-Za-z0-9_.\-]*)["']?"""
    r"""(?:\s*:\s*[A-Za-z_][\w.\[\]]*)?\s*(?::=|=>|=|:)(?![=~])\s*"""
    r"""(?P<q>["']?)(?P<val>[^\s"'`]+)"""
)

_BAD_VALUE_CHARS = set("(){}[]<>$")


def _generic_value_ok(kind: str, words: Sequence[str], val: str, quoted: bool, min_entropy: float) -> bool:
    if not quoted:
        val = val.rstrip(",;)]}")
    if not val or any(ch in _BAD_VALUE_CHARS for ch in val):
        return False
    if val[0] in "./~" or val.lower().startswith(("http://", "https://", "file:", "ftp:")):
        return False
    if looks_placeholder(val) or _UUID_RE.match(val):
        return False
    if _ENV_NAME_VALUE_RE.match(val):
        return False
    if _HEX_RE.match(val) and len(val) in _HASH_LENGTHS and any(w in _HASHY_WORDS for w in words):
        return False
    if kind == "password":
        if len(val) < 8:
            return False
        if not any(c.isdigit() or (not c.isalnum() and c not in "_.-") for c in val):  # 식별자 같은 값 제외
            return False
        return shannon_entropy(val) >= 2.5
    minlen = 16 if quoted else 20
    if len(val) < minlen:
        return False
    if not (any(c.isdigit() for c in val) and any(c.isalpha() for c in val)):
        return False
    return shannon_entropy(val) >= min_entropy


# ----------------------------------------------------------------------------
# 한 줄 스캔
# ----------------------------------------------------------------------------


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    label: str
    masked: str


def scan_line(text: str, path: str = "", lineno: int = 0, generic: bool = True,
              disabled: FrozenSet[str] = frozenset(), min_entropy: float = 3.5) -> List[Finding]:
    if ALLOW_MARKER in text:
        return []
    text = text[:MAX_LINE]
    cands: List[Tuple[int, int, int, str, str, str]] = []  # (prio, start, end, rule, label, masked)

    for rule in RULES:
        if rule.name in disabled:
            continue
        for m in rule.regex.finditer(text):
            val = m.group(rule.group)
            if val is None:
                continue
            if looks_placeholder(val) and rule.name != "private-key":
                continue
            if rule.validator and not rule.validator(val):
                continue
            if rule.min_entropy is not None and shannon_entropy(val) < rule.min_entropy:
                continue
            start, end = m.span(rule.group)
            masked = val if rule.reveal else mask_secret(val)
            cands.append((_RULE_PRIORITY[rule.name], start, end, rule.name, rule.label, masked))

    if generic and "generic-secret" not in disabled:
        for m in _ASSIGN.finditer(text):
            kind = classify_name(m.group("name"))
            if not kind:
                continue
            quoted = bool(m.group("q"))
            val = m.group("val") if quoted else m.group("val").rstrip(",;)]}")
            words = name_words(m.group("name"))
            if not _generic_value_ok(kind, words, val, quoted, min_entropy):
                continue
            start, end = m.start("val"), m.start("val") + len(val)
            cands.append((100, start, end, "generic-secret",
                          "비밀 같은 값 할당 (%s)" % m.group("name"), mask_secret(val)))

    cands.sort()
    taken: List[Tuple[int, int]] = []
    out: List[Finding] = []
    for _prio, start, end, rule, label, masked in cands:
        if any(start < e and s < end for s, e in taken):
            continue
        taken.append((start, end))
        out.append(Finding(path, lineno, rule, label, masked))
    return out


# ----------------------------------------------------------------------------
# 파일 이름 규칙 / 허용 목록
# ----------------------------------------------------------------------------

_ENV_EXEMPT_SUFFIXES = (".example", ".sample", ".template", ".dist", ".tpl", ".defaults", ".schema")
_KEY_FILE_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".git-credentials", ".netrc"}
_KEY_FILE_EXTS = (".pfx", ".p12", ".jks", ".keystore", ".ppk")
_LOCK_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json", "poetry.lock",
    "pipfile.lock", "cargo.lock", "go.sum", "uv.lock", "composer.lock", "gemfile.lock",
    "packages.lock.json", "bun.lockb", "flake.lock", "pdm.lock", "mix.lock",
}
_BINARY_EXTS = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".pdf", ".zip", ".gz", ".tgz", ".7z",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".mov", ".so", ".dll", ".exe", ".class",
    ".jar", ".pyc", ".bin", ".svg", ".map",
)


def is_lockfile(path: str) -> bool:
    base = os.path.basename(path).lower()
    return base in _LOCK_NAMES or base.endswith((".lock", ".min.js", ".min.css"))


def is_binary_path(path: str) -> bool:
    return path.lower().endswith(_BINARY_EXTS)


def scan_filename(path: str) -> Optional[Finding]:
    base = os.path.basename(path.replace("\\", "/")).lower()
    if base == ".env" or base.endswith(".env") or base.startswith(".env."):
        if not base.endswith(_ENV_EXEMPT_SUFFIXES):
            return Finding(path, 0, "env-file", ".env 환경변수 파일", "(파일 이름만으로 차단)")
    if base in _KEY_FILE_NAMES or base.endswith(_KEY_FILE_EXTS):
        return Finding(path, 0, "key-file", "개인키/인증서/자격증명 파일", "(파일 이름만으로 차단)")
    return None


class Allowlist:
    """저장소 루트 `.devguardignore` 형식 (한 줄에 하나, # 주석)

        tests/fixtures/        # 디렉터리 통째로 제외
        *.sample.json          # 글롭 (슬래시 없으면 파일명에 적용)
        docs/keys.md           # 경로 글롭
        rule:openai-key        # 규칙 끄기
        line:EXAMPLE_ONLY      # 이 문자열이 들어간 줄은 무시
    """

    def __init__(self, patterns: Iterable[str] = (), rules: Iterable[str] = (), lines: Iterable[str] = ()):
        self.patterns = list(patterns)
        self.rules = frozenset(rules)
        self.lines = list(lines)

    @classmethod
    def parse(cls, text: str) -> "Allowlist":
        pats, rules, lines = [], [], []
        for raw in text.splitlines():
            line = raw.split(" #", 1)[0].strip() if not raw.strip().startswith("#") else ""
            if not line:
                continue
            if line.startswith("rule:"):
                rules.append(line[5:].strip())
            elif line.startswith("line:"):
                lines.append(line[5:].strip())
            else:
                pats.append(line.replace("\\", "/"))
        return cls(pats, rules, lines)

    @classmethod
    def load(cls, repo: str) -> "Allowlist":
        path = os.path.join(repo, ".devguardignore")
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return cls.parse(f.read())
        except OSError:
            return cls()

    def path_allowed(self, path: str) -> bool:
        p = path.replace("\\", "/")
        for pat in self.patterns:
            if pat.endswith("/"):
                if p.startswith(pat) or ("/" + pat) in ("/" + p):
                    return True
            elif "/" not in pat:
                if fnmatch.fnmatchcase(os.path.basename(p), pat):
                    return True
            elif fnmatch.fnmatchcase(p, pat.lstrip("/")):
                return True
        return False

    def line_allowed(self, text: str) -> bool:
        return any(s and s in text for s in self.lines)


# ----------------------------------------------------------------------------
# git 스테이징 영역 스캔
# ----------------------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def staged_files(repo: str) -> List[str]:
    out = run_git(["diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"], cwd=repo)
    return [p.decode("utf-8", "replace") for p in out.split(b"\0") if p]


def parse_diff_added_lines(diff_text: str) -> Dict[str, List[Tuple[int, str]]]:
    """`git diff -U0` 출력에서 파일별 (줄번호, 추가된 줄) 목록을 뽑는다."""
    result: Dict[str, List[Tuple[int, str]]] = {}
    path: Optional[str] = None
    in_hunk = False
    lineno = 0
    for raw in diff_text.split("\n"):
        if raw.startswith("diff --git "):
            in_hunk = False
            path = None
            continue
        if not in_hunk:
            if raw.startswith("+++ "):
                name = raw[4:].rstrip("\t").strip()
                if name.startswith('"') and name.endswith('"'):
                    name = name[1:-1]
                if name == "/dev/null":
                    path = None
                else:
                    path = name[2:] if name[:2] in ("a/", "b/") else name
            elif raw.startswith("@@"):
                m = _HUNK_RE.match(raw)
                if m:
                    in_hunk = True
                    lineno = int(m.group(1))
            continue
        if raw.startswith("@@"):
            m = _HUNK_RE.match(raw)
            if m:
                lineno = int(m.group(1))
            continue
        if raw.startswith("+"):
            if path is not None:
                result.setdefault(path, []).append((lineno, raw[1:].rstrip("\r")))
            lineno += 1
        # '-' 줄과 '\ No newline' 은 무시
    return result


def staged_added_lines(repo: str) -> Dict[str, List[Tuple[int, str]]]:
    out = run_git(
        ["-c", "core.quotepath=false", "diff", "--cached", "-U0", "--no-color", "--no-ext-diff",
         "--no-textconv", "--diff-filter=ACMR"],
        cwd=repo,
    )
    return parse_diff_added_lines(out.decode("utf-8", "replace"))


def scan_added_lines(added: Dict[str, List[Tuple[int, str]]], names: Sequence[str],
                     allow: Optional[Allowlist] = None, cfg: Optional[dict] = None) -> List[Finding]:
    allow = allow or Allowlist()
    scfg = (cfg or {}).get("secrets", {})
    disabled = frozenset(scfg.get("disable_rules", [])) | allow.rules
    min_entropy = float(scfg.get("min_entropy", 3.5))
    findings: List[Finding] = []

    for name in names:
        if allow.path_allowed(name):
            continue
        f = scan_filename(name)
        if f and f.rule not in disabled:
            findings.append(f)

    for path, lines in added.items():
        if allow.path_allowed(path) or is_binary_path(path):
            continue
        generic = not is_lockfile(path)
        for lineno, text in lines:
            if allow.line_allowed(text):
                continue
            findings.extend(scan_line(text, path, lineno, generic=generic, disabled=disabled,
                                      min_entropy=min_entropy))
    return findings


def scan_staged(repo: str, cfg: Optional[dict] = None, allow: Optional[Allowlist] = None) -> List[Finding]:
    allow = allow if allow is not None else Allowlist.load(repo)
    return scan_added_lines(staged_added_lines(repo), staged_files(repo), allow, cfg)


# ----------------------------------------------------------------------------
# 메시지
# ----------------------------------------------------------------------------


def format_report(findings: Sequence[Finding], max_shown: int = 20) -> str:
    lines = [
        "",
        "=" * 64,
        " [devguard] 도둑 경보! 스테이징 영역에 비밀이 숨어 있습니다.",
        "=" * 64,
    ]
    for f in list(findings)[:max_shown]:
        where = f.path if not f.line else "%s:%d" % (f.path, f.line)
        lines.append("  - %s  [%s]" % (where, f.label))
        lines.append("      값: %s" % f.masked)
    if len(findings) > max_shown:
        lines.append("  ... 외 %d건 더" % (len(findings) - max_shown))
    lines += [
        "",
        " 커밋을 중단했습니다. 이렇게 하세요:",
        "   1) 스테이징에서 빼기      git restore --staged <파일>   (.env 는 .gitignore 에 추가)",
        "   2) 코드에서는 환경변수로    os.environ[\"KEY\"] / process.env.KEY",
        "   3) 이미 푸시했다면 그 키는 이미 유출된 것입니다 -> 즉시 폐기(재발급)하세요.",
        "",
        " 오탐이라면:",
        "   - 해당 줄 끝에 주석  # devguard:allow   (또는 // devguard:allow)",
        "   - 저장소 루트 .devguardignore 에 경로/규칙 추가 (README 참고)",
        "   - 정말 급할 때만: DEVGUARD_SKIP=1 git commit ...  (또는 git commit --no-verify)",
        "",
    ]
    return "\n".join(lines)
