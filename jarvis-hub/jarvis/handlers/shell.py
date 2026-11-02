"""shell.allowed: 설정에 미리 이름 붙여 둔 명령만 실행한다.

payload: {"name": "backup", "args": {"target": "D:/backup"}}
설정:   "backup": {"argv": ["robocopy", "C:/work", "{target}", "/MIR"], "cwd": "", "timeout": 3600,
                  "params": {"target": {"choices": ["D:/backup", "E:/backup"]}}}

- 이름이 설정에 없으면 거부 (PermanentError, 재시도 없음)
- 자리표시자 {param}은 argv 항목 '전체'일 때만 치환된다 -> 값은 항상 인자 하나. 셸 해석 없음.
- 모든 param은 choices(목록) 또는 pattern(정규식 fullmatch) 중 하나로 검증해야 한다.
- 선언되지 않은 인자, 빠진 인자, 너무 긴 값은 거부.
"""
import re

from .base import Handler, PermanentError, clip

_PH = re.compile(r"^\{([a-z_][a-z0-9_]*)\}$")
MAX_ARG_LEN = 512


def build_argv(spec, args):
    params = spec.get("params") or {}
    args = args or {}
    if not isinstance(args, dict):
        raise PermanentError("args는 객체여야 합니다")
    unknown = set(args) - set(params)
    if unknown:
        raise PermanentError("선언되지 않은 인자: %s" % ", ".join(sorted(unknown)))
    argv = []
    for item in spec["argv"]:
        m = _PH.match(item)
        if not m:
            if "{" in item and "}" in item and re.search(r"\{[a-z_][a-z0-9_]*\}", item):
                raise PermanentError("자리표시자는 argv 항목 전체여야 합니다: %r" % item)
            argv.append(item)
            continue
        name = m.group(1)
        rule = params.get(name)
        if rule is None:
            raise PermanentError("설정 오류: %s 의 규칙(params)이 없습니다" % name)
        if name not in args:
            if "default" in rule:
                argv.append(str(rule["default"]))
                continue
            raise PermanentError("인자가 빠졌습니다: %s" % name)
        val = args[name]
        if not isinstance(val, (str, int)) or isinstance(val, bool):
            raise PermanentError("인자 %s 는 문자열/숫자여야 합니다" % name)
        val = str(val)
        if len(val) > MAX_ARG_LEN or "\x00" in val:
            raise PermanentError("인자 %s 가 너무 길거나 잘못됐습니다" % name)
        if "choices" in rule:
            if val not in [str(c) for c in rule["choices"]]:
                raise PermanentError("인자 %s 값이 허용 목록에 없습니다" % name)
        elif "pattern" in rule:
            if not re.fullmatch(rule["pattern"], val):
                raise PermanentError("인자 %s 값이 형식(%s)에 맞지 않습니다" % (name, rule["pattern"]))
        else:
            raise PermanentError("설정 오류: %s 에 choices 또는 pattern이 필요합니다" % name)
        argv.append(val)
    return argv


class ShellAllowedHandler(Handler):
    type = "shell.allowed"

    def run(self, job):
        p = job.get("payload") or {}
        name = p.get("name")
        commands = self.cfg.get("commands") or {}
        if not isinstance(name, str) or name not in commands:
            raise PermanentError("허용되지 않은 명령 이름: %r (가능: %s)" % (name, ", ".join(sorted(commands)) or "없음"))
        spec = commands[name]
        if not isinstance(spec.get("argv"), list) or not spec["argv"] or \
                not all(isinstance(a, str) for a in spec["argv"]):
            raise PermanentError("설정 오류: commands.%s.argv 는 문자열 목록이어야 합니다" % name)
        argv = build_argv(spec, p.get("args"))
        code, out, err = self.runner(argv, cwd=spec.get("cwd") or None, timeout=spec.get("timeout", 600))
        n = self.cfg.get("max_output_chars", 8000)
        result = {"name": name, "returncode": code, "stdout": clip(out, n), "stderr": clip(err, n // 4)}
        if code != 0 and spec.get("fail_on_nonzero", True):
            raise PermanentError("명령 %s 종료 코드 %s: %s" % (name, code, clip(err or out, 300)))
        result["summary"] = "%s 완료" % name
        return result
