# -*- coding: utf-8 -*-
"""민감정보 마스킹 최소 부분집합 (til-drafter/redact.py 의 아이디어를 축약).

Claude 트랜스크립트 내용을 리포트/LLM 으로 보내기 전에 반드시 통과시킨다.
best-effort 이며 카운터에는 규칙 이름과 횟수만 남는다.
"""
import re
from collections import Counter

_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----.*?"
    r"(?:-----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----|\Z)", re.S)
_JWT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
_API_KEY = re.compile(
    r"(?<![A-Za-z0-9_])(?:sk-[A-Za-z0-9_-]{16,}|sk_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}|(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])|AIza[0-9A-Za-z_-]{35}"
    r"|npm_[A-Za-z0-9]{30,}|hf_[A-Za-z0-9]{30,})")
_BEARER = re.compile(r"\b(Bearer)([ \t]+)([A-Za-z0-9._~+/=-]{8,})", re.I)
_KV = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key|private[_-]?key|credential)"
    r"([A-Za-z0-9_.-]{0,30})([\"']?[ \t]*[=:][ \t]*[\"']?)([^\s\"',;&<>)}\]]{6,})", re.I)
_URL_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@", re.I)
_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?![A-Za-z0-9-])")
_OCT = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4 = re.compile(r"(?<![0-9A-Za-z.])" + _OCT + r"(?:\." + _OCT + r"){3}(?![0-9A-Za-z]|\.\d)")
_RRN = re.compile(r"(?<!\d)\d{6}-[1-4]\d{6}(?!\d)")
_PHONE = re.compile(r"(?<!\d)(?:\+82[- ]?)?0?1[016789][- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_HEX = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{32,}(?![0-9A-Za-z])")
_HOME = re.compile(r"(?:/home|/Users)/[^/\s\"']+|[A-Za-z]:\\Users\\[^\\\s\"']+")

_STRICT = ("password", "passwd", "pwd", "secret", "credential")


class Redactor(object):
    def __init__(self, words=()):
        self.counts = Counter()
        self.words = [w for w in words if w]

    def _sub(self, name, pattern, repl, text):
        def f(m):
            self.counts[name] += 1
            return repl(m) if callable(repl) else repl
        return pattern.sub(f, text)

    def redact(self, text):
        if not isinstance(text, str) or not text:
            return text or ""
        t = text
        t = self._sub("private_key", _PEM, "<비밀>", t)
        t = self._sub("jwt", _JWT, "<토큰>", t)
        t = self._sub("api_key", _API_KEY, "<토큰>", t)
        t = self._sub("bearer", _BEARER, lambda m: m.group(1) + m.group(2) + "<토큰>", t)
        t = self._sub("secret_kv", _KV,
                      lambda m: m.group(1) + m.group(2) + m.group(3) + "<비밀>", t)
        t = self._sub("url_userinfo", _URL_USERINFO, lambda m: m.group(1) + "<비밀>@", t)
        t = self._sub("email", _EMAIL, "<이메일>", t)
        t = self._sub("rrn", _RRN, "<주민번호>", t)
        t = self._sub("phone", _PHONE, "<전화번호>", t)
        t = self._sub("ipv4", _IPV4, "<IP>", t)
        t = self._sub("long_hex", _HEX, "<토큰>", t)
        t = self._sub("path", _HOME, "~", t)
        for w in self.words:
            if w in t:
                self.counts["custom_word"] += t.count(w)
                t = t.replace(w, "<가림>")
        return t
