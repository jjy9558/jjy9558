"""혼잣말 디버거: 트리거 -> 최근 에러 수집(로그 tail 또는 미리 정한 명령) -> 짧은 요약."""
import os
import re
import shutil
import subprocess

from .triggers import Cooldown, TriggerMatcher

# Windows .cmd/.bat 은 인자를 cmd.exe 가 다시 해석한다(BatBadBut). 위험 문자를 제거.
_BATCH_UNSAFE = re.compile(r'[\"%!^&|<>()\r\n`]')
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_UTTERANCE = 300


def clean_utterance(text, for_batch=False):
    t = _CONTROL.sub(" ", text or "").strip()[:MAX_UTTERANCE]
    if for_batch:
        t = _BATCH_UNSAFE.sub(" ", t)
    return t


def build_argv(template, utterance, prompt_template):
    """설정된 argv 각 원소의 {utterance}/{prompt}만 치환한다.

    str.format 대신 replace를 쓰므로 발화 속 중괄호가 해석되지 않고,
    발화는 항상 '하나의 인자 안의 데이터'로만 들어간다 (셸을 거치지 않음).
    """
    if not template or not isinstance(template, list):
        raise ValueError("debugger.command는 비어있지 않은 리스트여야 합니다")
    exe = shutil.which(template[0]) or template[0]
    is_batch = exe.lower().endswith((".cmd", ".bat"))
    u = clean_utterance(utterance, for_batch=is_batch)
    prompt = prompt_template.replace("{utterance}", u)
    if is_batch:
        prompt = clean_utterance(prompt, for_batch=True)
    argv = [exe] + [a.replace("{prompt}", prompt).replace("{utterance}", u) for a in template[1:]]
    return argv


def default_runner(argv, cwd=None, timeout=120):
    """shell=False 고정. (returncode, 출력) 반환."""
    try:
        p = subprocess.run(argv, cwd=cwd or None, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           shell=False, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    except FileNotFoundError:
        return 127, "명령을 찾을 수 없습니다: %s" % argv[0]
    except subprocess.TimeoutExpired:
        return 124, "명령이 시간 초과되었습니다."


def tail_errors(path, lines=200, pattern=r"(?i)error", keep=8):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as f:
        tail = f.readlines()[-lines:]
    rx = re.compile(pattern)
    hits = [ln.rstrip() for ln in tail if rx.search(ln)]
    return hits[-keep:]


def shorten(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Debugger:
    def __init__(self, cfg, llm=None, runner=default_runner, clock=None):
        self.cfg = cfg
        self.llm = llm
        self.runner = runner
        self.matcher = TriggerMatcher(cfg["triggers"])
        self.cooldown = Cooldown(cfg["cooldown_seconds"], **({"clock": clock} if clock else {}))

    def check(self, utterance):
        """('trigger' | 'cooldown' | None, 매칭 문구)."""
        if not self.cfg.get("enabled", True):
            return None, None
        phrase = self.matcher.match(utterance)
        if not phrase:
            return None, None
        if not self.cooldown.try_acquire("debugger"):
            return "cooldown", phrase
        return "trigger", phrase

    def investigate(self, utterance):
        c = self.cfg
        limit = c["max_speech_chars"]
        if c["mode"] == "command":
            argv = build_argv(c["command"], utterance, c["prompt_template"])
            code, out = self.runner(argv, cwd=c.get("cwd") or None, timeout=c["timeout_seconds"])
            if not out.strip():
                return "명령은 끝났는데 출력이 없어요. (종료 코드 %s)" % code
            return shorten(out, limit)
        errors = tail_errors(c["log_file"], c["tail_lines"], c["error_pattern"])
        if not errors:
            if not c["log_file"]:
                return "로그 파일이 설정되지 않았어요. config의 debugger.log_file을 채워주세요."
            return "최근 로그에는 에러가 안 보여요."
        if self.llm and c.get("summarize_with_llm", True):
            try:
                ans = self.llm.ask(
                    "다음은 최근 로그의 에러 줄이다. 무엇이 문제인지 폰 스피커로 읽을 한국어 2문장 이내로 요약해라.\n\n"
                    + "\n".join(errors),
                    max_tokens=200)
                if ans.strip():
                    return shorten(ans, limit)
            except Exception:
                pass
        return shorten("최근 에러 %d줄. 마지막: %s" % (len(errors), errors[-1]), limit)
