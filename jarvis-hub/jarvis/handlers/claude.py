"""claude.task: 설정한 `claude -p` 명령 템플릿을 설정한 저장소 폴더에서 실행한다. 기본 꺼짐.

payload: {"prompt": "README 오타 고쳐줘", "repo": "default"}
설정:   "claude.task": {"enabled": true, "command": ["claude", "-p", "{prompt}"],
                       "repos": {"default": "C:/work/myrepo"}}

- 프롬프트는 argv의 '{prompt}' 항목 자리에 인자 하나로 들어간다 (셸 없음, 쪼개지지 않음).
- repo는 설정의 이름 중 하나만. 경로를 payload로 받지 않는다.
- Windows에서 실행 파일이 .cmd/.bat이면 cmd.exe가 인자를 다시 해석하므로(BatBadBut)
  위험한 문자를 지운다. 가능하면 node + cli.js 를 직접 지정할 것.
"""
import os

from .base import Handler, PermanentError, clip

BAT_UNSAFE = '"%!^&|<>()\r\n`'


def needs_bat_sanitize(exe, is_windows=None):
    """실행 파일이 .cmd/.bat 이거나, Windows에서 확장자 없이 쓴 경우(claude -> claude.cmd)."""
    is_windows = (os.name == "nt") if is_windows is None else is_windows
    base = os.path.basename(exe).lower()
    return base.endswith((".cmd", ".bat")) or (is_windows and "." not in base)


def render_command(template, prompt, is_windows=None):
    if "{prompt}" not in template:
        raise PermanentError("설정 오류: command에 '{prompt}' 항목이 없습니다")
    if needs_bat_sanitize(template[0], is_windows):
        prompt = "".join(ch for ch in prompt if ch not in BAT_UNSAFE)
    return [prompt if a == "{prompt}" else a for a in template]


class ClaudeTaskHandler(Handler):
    type = "claude.task"

    def run(self, job):
        if not self.cfg.get("enabled"):
            raise PermanentError("claude.task 가 이 워커에서 꺼져 있습니다 (handlers.claude.task.enabled)")
        p = job.get("payload") or {}
        prompt = p.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise PermanentError("prompt가 비었습니다")
        if len(prompt) > self.cfg.get("max_prompt_chars", 8000):
            raise PermanentError("prompt가 너무 깁니다")
        repos = self.cfg.get("repos") or {}
        repo = p.get("repo") or "default"
        if repo not in repos:
            raise PermanentError("설정에 없는 repo: %r (가능: %s)" % (repo, ", ".join(sorted(repos)) or "없음"))
        cwd = repos[repo]
        if not os.path.isdir(cwd):
            raise PermanentError("repo 폴더가 없습니다: %s" % cwd)
        argv = render_command(list(self.cfg["command"]), prompt.strip())
        code, out, err = self.runner(argv, cwd=cwd, timeout=self.cfg.get("timeout", 1800))
        n = self.cfg.get("max_output_chars", 20000)
        if code != 0:
            raise PermanentError("claude 종료 코드 %s: %s" % (code, clip(err or out, 500)))
        text = clip(out.strip(), n)
        return {"repo": repo, "returncode": code, "text": text, "summary": clip(text, 300)}
