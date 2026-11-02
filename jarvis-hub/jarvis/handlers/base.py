"""핸들러 공통: 오류 종류와 프로세스 실행기.

명령 실행은 항상 argv 목록 + shell 미사용이다. 문자열 명령을 셸로 넘기는 코드는 이 저장소에 없다
(tests/test_safety.py가 소스 전체를 검사한다).
"""
import subprocess


class PermanentError(Exception):
    """다시 시도해도 소용없는 오류 (잘못된 요청, 허용되지 않은 명령 등). 재시도하지 않는다."""


class HandlerError(Exception):
    """일시적 오류. 시도 횟수가 남았으면 허브가 다시 큐에 넣는다."""


def default_runner(argv, cwd=None, timeout=60, env=None):
    """(returncode, stdout, stderr). 시간 초과면 HandlerError."""
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) for a in argv):
        raise PermanentError("argv는 문자열 목록이어야 합니다")
    try:
        p = subprocess.run(list(argv), cwd=cwd or None, timeout=timeout, env=env,
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.TimeoutExpired:
        raise HandlerError("시간 초과 (%ss): %s" % (timeout, argv[0]))
    except FileNotFoundError:
        raise PermanentError("실행 파일을 찾을 수 없습니다: %s" % argv[0])
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def clip(text, n):
    text = text or ""
    return text if len(text) <= n else text[:n] + "\n...(%d자 생략)" % (len(text) - n)


class Handler:
    type = ""

    def __init__(self, cfg, runner=default_runner, opener=None, **_):
        self.cfg = cfg or {}
        self.runner = runner
        self.opener = opener

    def run(self, job):  # pragma: no cover - 인터페이스
        raise NotImplementedError
