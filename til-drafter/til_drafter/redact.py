# -*- coding: utf-8 -*-
"""민감정보 마스킹. 구현은 jjkit(``_jjkit/redact.py``)을 그대로 쓴다."""
from ._jjkit.redact import RULE_LABELS, Redactor, luhn_ok


def build_redactor(words=(), usernames=(), use_os_user=True):
    return Redactor(words=words, usernames=usernames, use_os_user=use_os_user)


__all__ = ["RULE_LABELS", "Redactor", "luhn_ok", "build_redactor"]
