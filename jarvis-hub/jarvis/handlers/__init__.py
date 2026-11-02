"""작업 타입 -> 핸들러. 워커 설정에서 켜진 것만 등록된다 (등록 안 된 타입은 빌려오지도 않음)."""
from .base import Handler, HandlerError, PermanentError, default_runner  # noqa: F401
from .claude import ClaudeTaskHandler
from .llm import LlmChatHandler
from .notify import NotifySayHandler
from .shell import ShellAllowedHandler
from .wake import WakeHandler


def build_handlers(worker_cfg, runner=default_runner, opener=None, sock_factory=None):
    h = worker_cfg.get("handlers") or {}
    out = {}
    llm = h.get("llm.chat") or {}
    if llm.get("base_url") and llm.get("enabled", True):
        out["llm.chat"] = LlmChatHandler(llm, runner=runner, opener=opener)
    sh = h.get("shell.allowed") or {}
    if sh.get("commands"):
        out["shell.allowed"] = ShellAllowedHandler(sh, runner=runner)
    cl = h.get("claude.task") or {}
    if cl.get("enabled") is True:
        out["claude.task"] = ClaudeTaskHandler(cl, runner=runner)
    ns = h.get("notify.say") or {}
    if ns.get("enabled"):
        out["notify.say"] = NotifySayHandler(ns, opener=opener)
    wk = h.get("wake") or {}
    if wk.get("enabled"):
        out["wake"] = WakeHandler(wk, sock_factory=sock_factory)
    return out
