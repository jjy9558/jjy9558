# -*- coding: utf-8 -*-
"""민감정보 마스킹 (정규식 기반, best-effort).

``Redactor.redact(text)`` 는 텍스트에서 경로/이메일/IP/토큰/전화번호 등을
치환하고, 어떤 규칙이 몇 번 발동했는지를 ``Redactor.counts`` 에 누적한다.
카운터에는 규칙 이름과 횟수만 저장하며 원본 값은 절대 저장하지 않는다.
"""
import getpass
import ipaddress
import os
import re
from collections import Counter

# 규칙 id -> 사람이 읽는 설명 (--check 출력, 푸터에서 사용)
RULE_LABELS = {
    "private_key": "개인키(PEM) 블록",
    "jwt": "JWT",
    "api_key": "API 키/토큰 (sk-, ghp_, github_pat_, xox, AKIA 등)",
    "bearer": "Bearer 토큰",
    "long_hex": "긴 16진수 문자열 (32자 이상)",
    "long_base64": "긴 base64류 문자열 (32자 이상)",
    "secret_kv": "비밀값 할당 (password=..., API_KEY=... 등)",
    "webhook": "웹훅 URL",
    "url_userinfo": "URL 내 계정정보 (user:pass@)",
    "url_query": "URL 쿼리/프래그먼트 값",
    "tailscale": "Tailscale 호스트명 (*.ts.net)",
    "email": "이메일 주소",
    "ipv6": "IPv6 주소",
    "ipv4": "IPv4 주소",
    "rrn": "주민등록번호 형식",
    "card": "카드번호 형식",
    "phone": "전화번호 (한국)",
    "path": "파일 경로",
    "username": "OS 사용자명",
    "custom_word": "사용자 지정 단어",
}

PH_PATH = "<경로>"
PH_TOKEN = "<토큰>"
PH_SECRET = "<비밀>"
PH_VALUE = "<값>"
PH_EMAIL = "<이메일>"
PH_IP = "<IP>"
PH_HOST = "<호스트>"
PH_PHONE = "<전화번호>"
PH_CARD = "<카드번호>"
PH_RRN = "<주민번호>"
PH_USER = "<사용자>"
PH_WORD = "<가림>"

# 사용자명으로 학습하면 안 되는 일반적인 이름들
GENERIC_USERNAMES = {
    "user", "users", "root", "admin", "administrator", "public", "default",
    "defaultuser0", "all users", "shared", "guest", "runner", "ubuntu",
    "home", "me", "pc", "owner", "ec2-user", "vagrant", "node", "docker",
    "appdata", "temp", "tmp", "name", "username", "your-name", "yourname",
    "foo", "bar", "example", "test",
}

# --- 토큰류 ---------------------------------------------------------------
_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?"
    r"(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)",
    re.S,
)
_JWT = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"
)
_API_KEY = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"sk-[A-Za-z0-9_-]{16,}"
    r"|sk_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|xapp-[A-Za-z0-9-]{10,}"
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|npm_[A-Za-z0-9]{30,}"
    r"|hf_[A-Za-z0-9]{30,}"
    r")"
)
_BEARER = re.compile(r"\b(Bearer)([ \t]+)([A-Za-z0-9._~+/=-]{8,})", re.I)
_HEX = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{32,}(?![0-9A-Za-z])")
_B64 = re.compile(r"(?<![A-Za-z0-9+_=-])[A-Za-z0-9+_-]{32,}={0,2}(?![A-Za-z0-9+_=-])")
_KV = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key|"
    r"private[_-]?key|credential)([A-Za-z0-9_.-]{0,30})"
    r"""(["']?[ \t]*[=:][ \t]*["']?)"""
    r"""([^\s"',;&<>)}\]]{6,})""",
    re.I,
)
_STRICT_KV = ("password", "passwd", "pwd", "secret", "credential")

# --- URL / 호스트 ----------------------------------------------------------
_WEBHOOK = re.compile(
    r"https?://(?:hooks\.slack\.com/(?:services|workflows)/|"
    r"(?:discord(?:app)?\.com)/api/webhooks/|"
    r"outlook\.office\.com/webhook/)[^\s<>\"'`)\]]+",
    re.I,
)
_URL = re.compile(
    r"""(?:https?|wss?|ftp)://(?:<[가-힣A-Za-z]+>|[^\s<>"'`)\]])+""", re.I
)
_TSNET = re.compile(r"(?<![A-Za-z0-9-])(?:[A-Za-z0-9-]+\.)*[A-Za-z0-9-]+\.ts\.net(?![A-Za-z0-9-])", re.I)
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+)@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?![A-Za-z0-9-])"
)

# --- IP ----------------------------------------------------------------------
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4 = re.compile(
    r"(?<![0-9A-Za-z.])" + _OCTET + r"(?:\." + _OCTET + r"){3}(?![0-9A-Za-z]|\.\d)"
)
_IPV6 = re.compile(
    r"(?<![0-9A-Za-z:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Za-z:]|\.\d)"
)

# --- 숫자형 개인정보 -------------------------------------------------------
_CARD_GROUPED = re.compile(
    r"(?<![0-9A-Za-z-])(?:\d{4}([ -])\d{4}\1\d{4}\1\d{4}|\d{4}([ -])\d{6}\2\d{5})(?![0-9A-Za-z]|-\d)"
)
_CARD_PLAIN = re.compile(r"(?<![0-9A-Za-z-])\d{15,19}(?![0-9A-Za-z]|-\d)")
_RRN = re.compile(
    r"(?<![0-9A-Za-z-])\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-[1-4]\d{6}(?![0-9A-Za-z]|-\d)"
)
_PHONE_MOBILE = re.compile(
    r"(?<![0-9A-Za-z-])(?:\+82[-. ]?0?|0)1[016789][-. ]?\d{3,4}[-. ]?\d{4}(?![0-9A-Za-z]|-\d)"
)
_PHONE_LAND = re.compile(
    r"(?<![0-9A-Za-z-])0(?:2|[3-6][1-5]|70|80)-\d{3,4}-\d{4}(?![0-9A-Za-z]|-\d)"
)

# --- 경로 -------------------------------------------------------------------
_PATH_CHARS = r"""[^\s"'<>|*?`]"""
_WIN_PATH = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:|%[A-Za-z_]+%)[\\/]+" + _PATH_CHARS + r"*"
)
_UNC_PATH = re.compile(r"(?<![\w\\])\\\\+[\w.$-]+\\+" + _PATH_CHARS + r"+")
_POSIX_ROOTS = (
    "home|Users|root|tmp|var|etc|opt|mnt|srv|usr|Volumes|media|"
    "workspace|workspaces|private"
)
_POSIX_PATH = re.compile(
    r"(?<![A-Za-z0-9_/:.~$%>-])(?:/(?:" + _POSIX_ROOTS + r")|/[A-Za-z](?=/Users/))"
    r"(?![A-Za-z0-9_-])(?:/" + _PATH_CHARS + r"*)?"
)
_TILDE_PATH = re.compile(r"(?<![A-Za-z0-9_/~.>-])~[\\/]" + _PATH_CHARS + r"*")
_TRAIL_PUNCT = ".,;:!?)]}"

# 사용자명 학습용: C:\Users\NAME, /home/NAME, /Users/NAME
_LEARN_USER = re.compile(
    r"""(?<![A-Za-z0-9_.~-])[\\/](?:[Uu]sers|home)[\\/]+([^\\/\s"'<>|:*?`]+)"""
)


def luhn_ok(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = ord(ch) - 48
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _is_ascii(s):
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _word_pattern(words):
    """단어 목록 -> 정규식. ASCII 단어는 영숫자 경계를 요구한다(한글 조사 붙는 건 허용)."""
    parts = []
    for w in sorted(set(words), key=len, reverse=True):
        esc = re.escape(w)
        if _is_ascii(w):
            parts.append(r"(?<![A-Za-z0-9_])" + esc + r"(?![A-Za-z0-9_])")
        else:
            parts.append(esc)
    if not parts:
        return None
    return re.compile("|".join(parts), re.I)


def _split_trailing(text):
    core = text.rstrip(_TRAIL_PUNCT)
    return core, text[len(core):]


class Redactor(object):
    """텍스트 마스킹기.

    usernames:   명시적으로 가릴 OS 사용자명
    words:       --redact-word / 설정 파일의 사용자 지정 단어
    use_os_user: 실행 중인 OS 사용자명(getpass 등)도 자동으로 추가
    """

    def __init__(self, words=(), usernames=(), use_os_user=True):
        self.counts = Counter()
        self._words = []
        self._usernames = []
        self._word_re = None
        self._user_re = None
        for w in words:
            self.add_word(w)
        for u in usernames:
            self.add_username(u)
        if use_os_user:
            for u in self._os_usernames():
                self.add_username(u)

    # -- 사용자명/단어 관리 ---------------------------------------------------
    @staticmethod
    def _os_usernames():
        names = []
        try:
            names.append(getpass.getuser())
        except Exception:
            pass
        for key in ("USERNAME", "USER", "LOGNAME"):
            v = os.environ.get(key)
            if v:
                names.append(v)
        return names

    def add_word(self, word):
        word = (word or "").strip()
        if len(word) < 2 or word in self._words:
            return
        self._words.append(word)
        self._word_re = _word_pattern(self._words)

    def add_username(self, name):
        name = (name or "").strip()
        if not name or name.lower() in GENERIC_USERNAMES:
            return
        if len(name) < (3 if _is_ascii(name) else 2):
            return
        if name.startswith("<") or name.startswith("%") or name.startswith("$"):
            return
        if name.lower() in (n.lower() for n in self._usernames):
            return
        self._usernames.append(name)
        self._user_re = _word_pattern(self._usernames)

    def learn(self, text):
        """텍스트 안의 경로에서 사용자명을 학습한다(치환은 하지 않음)."""
        if not text:
            return
        for m in _LEARN_USER.finditer(text):
            self.add_username(m.group(1))

    @property
    def usernames(self):
        return list(self._usernames)

    # -- 메인 -----------------------------------------------------------------
    def redact(self, text):
        if not text or not isinstance(text, str):
            return text
        self.learn(text)
        t = text
        t = self._sub("private_key", _PEM, t, lambda m: PH_SECRET)
        t = self._sub("jwt", _JWT, t, lambda m: PH_TOKEN)
        t = self._sub("api_key", _API_KEY, t, self._h_api_key)
        t = self._sub("bearer", _BEARER, t, self._h_bearer)
        t = self._sub("long_hex", _HEX, t, lambda m: PH_TOKEN)
        t = self._sub("long_base64", _B64, t, self._h_b64)
        t = self._sub("webhook", _WEBHOOK, t, lambda m: "<웹훅URL>")
        t = self._sub_url(t)
        t = self._sub("secret_kv", _KV, t, self._h_kv)
        t = self._sub("tailscale", _TSNET, t, lambda m: PH_HOST)
        t = self._sub("email", _EMAIL, t, self._h_email)
        t = self._sub("ipv6", _IPV6, t, self._h_ipv6)
        t = self._sub("ipv4", _IPV4, t, lambda m: PH_IP)
        t = self._sub("rrn", _RRN, t, lambda m: PH_RRN)
        t = self._sub("card", _CARD_GROUPED, t, lambda m: PH_CARD)
        t = self._sub("card", _CARD_PLAIN, t, self._h_card_plain)
        t = self._sub("phone", _PHONE_MOBILE, t, lambda m: PH_PHONE)
        t = self._sub("phone", _PHONE_LAND, t, lambda m: PH_PHONE)
        t = self._sub("path", _WIN_PATH, t, self._h_path)
        t = self._sub("path", _UNC_PATH, t, self._h_path)
        t = self._sub("path", _POSIX_PATH, t, self._h_path)
        t = self._sub("path", _TILDE_PATH, t, self._h_path)
        if self._user_re is not None:
            t = self._sub("username", self._user_re, t, lambda m: PH_USER)
        if self._word_re is not None:
            t = self._sub("custom_word", self._word_re, t, lambda m: PH_WORD)
        return t

    def summary(self):
        """규칙별 발동 횟수를 (설명, 횟수) 목록으로 반환 (비밀값 없음)."""
        out = []
        for rule in RULE_LABELS:
            n = self.counts.get(rule, 0)
            if n:
                out.append((rule, RULE_LABELS[rule], n))
        return out

    # -- 내부 -----------------------------------------------------------------
    def _sub(self, rule, pattern, text, func):
        def _r(m):
            out = func(m)
            if out is None:
                return m.group(0)
            self.counts[rule] += 1
            return out

        return pattern.sub(_r, text)

    @staticmethod
    def _h_api_key(m):
        tok = m.group(0)
        if tok.startswith("sk-") and not (re.search(r"\d", tok) or len(tok) >= 32):
            return None  # 'sk-some-plain-words' 같은 일반 문장 방지
        return PH_TOKEN

    @staticmethod
    def _h_bearer(m):
        tok = m.group(3)
        if re.search(r"\d", tok) or re.search(r"[^.]\.[^.]", tok) or len(tok) >= 32:
            return m.group(1) + m.group(2) + PH_TOKEN
        return None

    @staticmethod
    def _h_b64(m):
        s = m.group(0)
        body = s.rstrip("=")
        if not (re.search(r"[A-Z]", body) and re.search(r"[a-z]", body) and re.search(r"\d", body)):
            return None
        segs = re.split(r"[-_]", body)
        if len(segs) >= 4 and all(re.fullmatch(r"[A-Za-z]+|\d+", x) for x in segs if x):
            return None  # Kebab-Case-Title-2026 같은 슬러그
        return PH_TOKEN

    @staticmethod
    def _h_kv(m):
        key, rest, sep, val = m.group(1), m.group(2), m.group(3), m.group(4)
        name = (key + rest).lower()
        if name.endswith("tokens") or name.endswith("_count"):
            return None
        if val[0] in "<$%{*(@" or val.lower().strip("\"'") in (
            "true", "false", "none", "null", "undefined", "string", "required", "optional",
        ):
            return None
        if "(" in val or "[" in val:
            return None  # 코드 호출/인덱싱
        strict = any(k in name for k in _STRICT_KV)
        if re.fullmatch(r"[a-z_]+(?:\.[a-z_]+)+", val):
            return None  # os.environ.get 같은 속성 접근
        if not strict and re.fullmatch(r"[a-z_]+", val):
            return None  # token = tokenizer 같은 식별자
        return key + rest + sep + PH_VALUE

    @staticmethod
    def _h_email(m):
        local, domain = m.group(1), m.group(2)
        if local.lower() == "git":
            return None  # git@github.com:owner/repo 형태
        if not re.search(r"[A-Za-z]", domain.split(".")[0]):
            return None  # pkg@1.2.3 류
        return PH_EMAIL

    @staticmethod
    def _h_ipv6(m):
        s = m.group(0)
        if "::" not in s and s.count(":") != 7:
            return None
        groups = [g for g in s.split(":") if g]
        if not groups or all(len(g) <= 2 for g in groups):
            return None  # a[1::2] 슬라이스, ::1, 시각 등
        if not re.search(r"\d", s) and len(groups) < 3:
            return None  # a[1::2] 슬라이스, ::1, 시각 등
        try:
            ipaddress.IPv6Address(s)
        except ValueError:
            return None
        return PH_IP

    @staticmethod
    def _h_card_plain(m):
        s = m.group(0)
        return PH_CARD if luhn_ok(s) else None

    def _h_path(self, m):
        raw = m.group(0)
        core, trail = _split_trailing(raw)
        if not core:
            return None
        parts = [p for p in re.split(r"[\\/]+", core) if p]
        # 사용자명 학습
        is_win = bool(re.match(r"^(?:[A-Za-z]:|%)", core))
        root_i = 1 if is_win else 0
        if len(parts) > root_i + 1 and parts[root_i].lower() in ("users", "home"):
            self.add_username(parts[root_i + 1])
        if is_win:
            body = parts[1:]
            home_depth = 2 if body and body[0].lower() == "users" else 0
        else:
            body = parts
            home_depth = 2 if body and body[0] in ("home", "Users") else 0
            if len(body) >= 3 and re.fullmatch(r"[A-Za-z]", body[0]) and body[1] == "Users":
                body = body[1:]
                home_depth = 2
        if not body or len(body) == 1 or (home_depth and len(body) <= home_depth):
            return PH_PATH + trail
        last = body[-1]
        if last.lower() in (u.lower() for u in self._usernames):
            return PH_PATH + trail
        return PH_PATH + "/" + last + trail

    def _sub_url(self, text):
        def _r(m):
            url = m.group(0)
            core, trail = _split_trailing(url)
            mm = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*://)([^/?#]*)(.*)$", core, re.S)
            if not mm:
                return url
            scheme, auth, rest = mm.group(1), mm.group(2), mm.group(3)
            changed = False
            if "@" in auth:
                auth = auth.rsplit("@", 1)[1]
                self.counts["url_userinfo"] += 1
                changed = True
            frag = None
            if "#" in rest:
                rest, frag = rest.split("#", 1)
            query = None
            if "?" in rest:
                rest, query = rest.split("?", 1)
            query_changed = False
            if query is not None:
                pairs = []
                for p in query.split("&"):
                    if not p:
                        continue
                    if "=" in p:
                        k, v = p.split("=", 1)
                        if v != PH_VALUE and v != "":
                            query_changed = True
                        pairs.append(k + "=" + (PH_VALUE if v else ""))
                    elif p != PH_VALUE:
                        query_changed = True
                        pairs.append(PH_VALUE)
                    else:
                        pairs.append(p)
                query = "&".join(pairs)
            frag_changed = False
            if frag is not None and ("=" in frag or len(frag) > 24):
                frag = None
                frag_changed = True
            if query_changed or frag_changed:
                self.counts["url_query"] += 1
                changed = True
            if not changed:
                return url
            out = scheme + auth + rest
            if query:
                out += "?" + query
            if frag:
                out += "#" + frag
            return out + trail

        return _URL.sub(_r, text)


def build_redactor(words=(), usernames=(), use_os_user=True):
    return Redactor(words=words, usernames=usernames, use_os_user=use_os_user)
