"""테스트 도우미: 임시 홈, 가짜 컨텍스트, 다른 프로젝트의 설정 로더 불러오기(읽기 전용)."""
import importlib
import socket
import sys
import tempfile
import unittest
from pathlib import Path

from homelab import configgen, paths, registry, secretstore

REPO = Path(__file__).resolve().parents[2]
FAKE_SERVER = Path(__file__).resolve().parent / "fake_server.py"


def make_ctx(tmp: Path, **overrides) -> registry.Ctx:
    """tmp 를 홈으로 쓰는 컨텍스트 (토큰은 실행 중에 만든 가짜 값)."""
    layout = paths.Layout(tmp / ".homelab", tmp)
    s = dict(paths.DEFAULT_SETTINGS)
    s.update(overrides)
    s["repo_root"] = str(REPO)
    s = paths.validate_settings(s, list(registry.SERVICES))
    secrets = secretstore.empty()
    ctx = registry.Ctx(layout, s, secrets)
    toks, extras = configgen.required_tokens(ctx)
    secretstore.ensure(secrets, toks, extras)
    return ctx


def write_all(ctx: registry.Ctx):
    files = configgen.render_all(ctx)
    configgen.ensure_data_dirs(ctx)
    return files, configgen.apply(files, ctx.secrets)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def project_loader(folder: str, module: str):
    """다른 프로젝트의 설정 모듈을 읽기 전용으로 import 한다. 폴더가 없으면 테스트를 건너뛴다."""
    proj = REPO / folder
    if not (proj / module).is_dir():
        raise unittest.SkipTest("%s 폴더가 없어요" % folder)
    for name in [n for n in sys.modules if n == module or n.startswith(module + ".")]:
        del sys.modules[name]
    sys.path.insert(0, str(proj))
    try:
        return importlib.import_module(module + ".config")
    finally:
        sys.path.remove(str(proj))


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
