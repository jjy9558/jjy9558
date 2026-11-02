import os
import subprocess
import sys
import unittest

import helpers as H
from helpers import git, git_env, make_repo, rmtree, rnd, write

from devguard import githooks


def commit(repo, msg="m", env=None):
    return git(repo, "commit", "-q", "-m", msg, env=env, check=False)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.hooks = os.path.join(self.repo, ".git", "hooks")

    def tearDown(self):
        rmtree(self.repo)

    def read(self, name):
        with open(os.path.join(self.hooks, name), encoding="utf-8") as f:
            return f.read()

    def test_install_creates_hooks(self):
        msgs = githooks.install(self.repo)
        self.assertEqual(len(msgs), 2)
        for h in ("pre-commit", "pre-push"):
            text = self.read(h)
            self.assertTrue(text.startswith("#!/bin/sh\n"))
            self.assertIn(githooks.MARKER, text)
            self.assertIn("devguard_run.py", text)
            self.assertNotIn("\r", text)
            if os.name == "posix":
                self.assertTrue(os.access(os.path.join(self.hooks, h), os.X_OK))
        self.assertIn("precommit", self.read("pre-commit"))
        self.assertIn("prepush", self.read("pre-push"))

    def test_idempotent(self):
        githooks.install(self.repo)
        first = {h: self.read(h) for h in ("pre-commit", "pre-push")}
        msgs = githooks.install(self.repo)
        self.assertTrue(all("이미 설치됨" in m for m in msgs), msgs)
        for h in first:
            self.assertEqual(first[h], self.read(h))
            self.assertFalse(os.path.exists(os.path.join(self.hooks, h + githooks.ORIG_SUFFIX)))

    def test_existing_hook_preserved_and_chained(self):
        marker = os.path.join(self.repo, "orig-ran.txt")
        orig_body = "#!/bin/sh\necho ran >> '%s'\nexit 0\n" % marker.replace("\\", "/")
        write(os.path.join(self.hooks, "pre-commit"), orig_body)
        os.chmod(os.path.join(self.hooks, "pre-commit"), 0o755)
        githooks.install(self.repo)
        orig = os.path.join(self.hooks, "pre-commit" + githooks.ORIG_SUFFIX)
        self.assertEqual(H.read(orig), orig_body)
        # 재설치해도 원본은 그대로
        githooks.install(self.repo)
        self.assertEqual(H.read(orig), orig_body)
        write(os.path.join(self.repo, "a.txt"), "hello\n")
        git(self.repo, "add", "a.txt")
        self.assertEqual(commit(self.repo).returncode, 0)
        self.assertTrue(os.path.exists(marker), "기존 훅이 실행되지 않음")

    def test_failing_original_hook_still_blocks(self):
        write(os.path.join(self.hooks, "pre-commit"), "#!/bin/sh\necho nope >&2\nexit 1\n")
        os.chmod(os.path.join(self.hooks, "pre-commit"), 0o755)
        githooks.install(self.repo)
        write(os.path.join(self.repo, "a.txt"), "hello\n")
        git(self.repo, "add", "a.txt")
        p = commit(self.repo)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"nope", p.stderr)

    def test_uninstall_restores_original(self):
        orig_body = "#!/bin/sh\nexit 0\n"
        write(os.path.join(self.hooks, "pre-commit"), orig_body)
        githooks.install(self.repo)
        githooks.uninstall(self.repo)
        self.assertEqual(self.read("pre-commit"), orig_body)
        self.assertFalse(os.path.exists(os.path.join(self.hooks, "pre-push")))

    def test_not_a_repo(self):
        import tempfile
        d = tempfile.mkdtemp()
        try:
            with self.assertRaises(Exception):
                githooks.install(d)
        finally:
            rmtree(d)

    def test_cli_install(self):
        p = subprocess.run([sys.executable, "-m", "devguard", "install-git-hooks", self.repo],
                           cwd=H.ROOT, env=git_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.hooks, "pre-commit")))

    def test_path_with_spaces_and_quotes(self):
        text = githooks.render_hook("pre-commit", "/opt/my py/python", "/home/o'brien/dev guard/devguard_run.py")
        self.assertIn("'/opt/my py/python'", text)
        self.assertIn("'/home/o'\\''brien/dev guard/devguard_run.py'", text)


class PreCommitEndToEnd(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        githooks.install(self.repo, hooks=("pre-commit",))

    def tearDown(self):
        rmtree(self.repo)

    def stage(self, name, text):
        write(os.path.join(self.repo, name), text)
        git(self.repo, "add", "-f", name)

    def test_blocks_env_file(self):
        secret = "ghp_" + rnd(36, seed=1)
        self.stage(".env", "TOKEN=%s\n" % secret)
        p = commit(self.repo)
        self.assertNotEqual(p.returncode, 0)
        err = p.stderr.decode("utf-8", "replace")
        self.assertIn("도둑 경보", err)
        self.assertIn(".env", err)
        self.assertNotIn(secret, err)

    def test_blocks_secret_in_code_and_masks(self):
        secret = "sk-ant-" + rnd(70, H.B64URL, 2)
        self.stage("src/app.py", "x = 1\nclient = Anthropic(api_key='%s')\n" % secret)
        p = commit(self.repo)
        self.assertNotEqual(p.returncode, 0)
        err = p.stderr.decode("utf-8", "replace")
        self.assertIn("src/app.py:2", err)
        self.assertNotIn(secret, err)
        self.assertNotIn(secret[12:40], err)

    def test_clean_commit_passes(self):
        self.stage("README.md", "# hi\napi_key = os.environ['API_KEY']\n")
        self.stage(".env.example", "API_KEY=your_api_key_here\n")
        self.assertEqual(commit(self.repo).returncode, 0)

    def test_only_added_lines_scanned(self):
        # 이미 커밋된(히스토리) 비밀이 있는 파일의 다른 줄을 고쳐도 막지 않는다
        secret = "ghp_" + rnd(36, seed=5)
        self.stage("old.py", "t = '%s'  # devguard:allow\n" % secret)
        self.assertEqual(commit(self.repo, "first").returncode, 0)
        write(os.path.join(self.repo, "old.py"), "t = '%s'  # devguard:allow\nprint(1)\n" % secret)
        git(self.repo, "add", "old.py")
        self.assertEqual(commit(self.repo, "second").returncode, 0)

    def test_deleting_a_secret_is_fine(self):
        secret = "ghp_" + rnd(36, seed=6)
        self.stage("old.py", "t = '%s'  # devguard:allow\n" % secret)
        commit(self.repo, "first")
        write(os.path.join(self.repo, "old.py"), "t = None\n")
        git(self.repo, "add", "old.py")
        self.assertEqual(commit(self.repo, "second").returncode, 0)

    def test_inline_allow_and_ignore_file(self):
        secret = "ghp_" + rnd(36, seed=7)
        self.stage("a.py", "t = '%s'  # devguard:allow\n" % secret)
        self.stage("fixtures/b.py", "t = '%s'\n" % secret)
        write(os.path.join(self.repo, ".devguardignore"), "fixtures/\n")
        git(self.repo, "add", ".devguardignore")
        self.assertEqual(commit(self.repo).returncode, 0)

    def test_skip_env(self):
        self.stage(".env", "A=1\n")
        env = git_env({"DEVGUARD_SKIP": "1"})
        self.assertEqual(commit(self.repo, env=env).returncode, 0)

    def test_file_with_spaces_and_korean_name(self):
        secret = "ghp_" + rnd(36, seed=8)
        self.stage("설정 파일.py", "t = '%s'\n" % secret)
        p = commit(self.repo)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("설정 파일.py:1", p.stderr.decode("utf-8", "replace"))

    def test_initial_commit_without_head(self):
        # 첫 커밋(HEAD 없음)에서도 동작
        self.stage("x.txt", "ok\n")
        self.assertEqual(commit(self.repo).returncode, 0)


class PrePushEndToEnd(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.repo = make_repo()
        self.remote = tempfile.mkdtemp(prefix="dg-remote-")
        git(self.remote, "init", "-q", "--bare", "-b", "main")
        git(self.repo, "remote", "add", "origin", self.remote)
        write(os.path.join(self.repo, "a.txt"), "1\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-q", "-m", "init")
        githooks.install(self.repo, hooks=("pre-push",))

    def tearDown(self):
        rmtree(self.repo)
        rmtree(self.remote)

    def push(self, ref, now, extra_env=None):
        env = git_env(dict({"DEVGUARD_NOW": now}, **(extra_env or {})))
        return git(self.repo, "push", "-q", "origin", ref, env=env, check=False)

    def test_friday_afternoon_blocks_main(self):
        p = self.push("main", "2025-01-03T16:00:00")
        self.assertNotEqual(p.returncode, 0)
        err = p.stderr.decode("utf-8", "replace")
        self.assertIn("금요일 배포 차단기", err)
        self.assertIn("main", err)

    def test_friday_morning_ok(self):
        self.assertEqual(self.push("main", "2025-01-03T10:00:00").returncode, 0)

    def test_thursday_afternoon_ok(self):
        self.assertEqual(self.push("main", "2025-01-02T16:00:00").returncode, 0)

    def test_env_override(self):
        p = self.push("main", "2025-01-03T16:00:00", {"DEVGUARD_FRIDAY_OK": "1"})
        self.assertEqual(p.returncode, 0)

    def test_feature_branch_ok_on_friday(self):
        git(self.repo, "branch", "feature/x")
        self.assertEqual(self.push("feature/x", "2025-01-03T16:00:00").returncode, 0)

    def test_release_branch_blocked(self):
        git(self.repo, "branch", "release/1.0")
        self.assertNotEqual(self.push("release/1.0", "2025-01-03T16:00:00").returncode, 0)

    def test_chained_original_receives_stdin(self):
        # 설치 후 기존 훅을 심은 상황 재현: pre-push 를 원본으로 교체하고 재설치
        out = os.path.join(self.repo, "stdin.txt").replace("\\", "/")
        hook = os.path.join(self.repo, ".git", "hooks", "pre-push")
        os.remove(hook)
        write(hook, "#!/bin/sh\ncat > '%s'\nexit 0\n" % out)
        os.chmod(hook, 0o755)
        githooks.install(self.repo, hooks=("pre-push",))
        self.assertEqual(self.push("main", "2025-01-02T10:00:00").returncode, 0)
        self.assertIn("refs/heads/main", H.read(out))

    def test_push_wrapper_i_know(self):
        env = git_env({"DEVGUARD_NOW": "2025-01-03T16:00:00"})
        p = subprocess.run([sys.executable, os.path.join(H.ROOT, "devguard_run.py"), "push", "--i-know", "-q", "origin", "main"],
                           cwd=self.repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stderr)
        p = subprocess.run([sys.executable, os.path.join(H.ROOT, "devguard_run.py"), "push", "-q", "origin", "main:release/2"],
                           cwd=self.repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertNotEqual(p.returncode, 0)


if __name__ == "__main__":
    unittest.main()
