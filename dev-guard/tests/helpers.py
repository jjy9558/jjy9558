"""테스트 공용 도우미.

가짜 토큰은 소스에 리터럴로 넣지 않고 실행 시점에 결정적으로 생성한다
(이 저장소에 devguard 훅을 설치해도 테스트 파일이 차단되지 않고, 외부 비밀 스캐너에도 안 걸린다).
"""
import base64
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

LOWER = "abcdefghijklmnopqrstuvwxyz"
UPPER = LOWER.upper()
DIGITS = "0123456789"
ALNUM = LOWER + UPPER + DIGITS
B64URL = ALNUM + "_-"


def rnd(n, alphabet=ALNUM, seed=1):
    r = random.Random(seed)
    return "".join(r.choice(alphabet) for _ in range(n))


def b64url(obj):
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def fake_jwt(seed=3):
    return "%s.%s.%s" % (b64url({"alg": "HS256", "typ": "JWT"}),
                         b64url({"sub": "user-%d" % seed, "iat": 1700000000, "role": "admin"}),
                         rnd(43, B64URL, seed))


def git_env(extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "DEVGUARD_HOME": tempfile.gettempdir() + "/devguard-test-nohome",
    })
    env.pop("DEVGUARD_SKIP", None)
    env.pop("DEVGUARD_FRIDAY_OK", None)
    env.pop("DEVGUARD_NOW", None)
    if extra:
        env.update(extra)
    return env


def git(repo, *args, env=None, check=True):
    p = subprocess.run(["git"] + list(args), cwd=repo, env=env or git_env(),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        raise AssertionError("git %s 실패: %s" % (args, p.stderr.decode("utf-8", "replace")))
    return p


def make_repo(prefix="dg-repo-"):
    d = tempfile.mkdtemp(prefix=prefix)
    git(d, "init", "-q", "-b", "main")
    return d


def rmtree(d):
    shutil.rmtree(d, ignore_errors=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()
