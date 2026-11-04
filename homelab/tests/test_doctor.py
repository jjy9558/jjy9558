import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path

from homelab import cli, doctor
from homelab.doctor import Deps
from helpers import TmpCase, make_ctx, write_all


class FakeResp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    def __init__(self, status=200, body=b'{"data": [{"id": "m1"}, {"id": "m2"}]}', error=None):
        self.status, self.body, self.error = status, body, error
        self.urls = []

    def open(self, req, timeout=None):
        self.urls.append(req.full_url)
        if self.error:
            raise self.error
        return FakeResp(self.status, self.body)


def run_ok(argv, cwd, **kw):
    return 0, "3.13.1" if "-c" in argv and "sys.version_info" in argv[2] else ""


def deps(**kw):
    base = dict(which=lambda n: "/usr/bin/" + n, run=run_ok, opener=FakeOpener(),
                port_in_use=lambda h, p: False, alive=lambda pid: False, py_version=(3, 13, 1))
    base.update(kw)
    return Deps(**base)


def by_text(checks, needle):
    return [c for c in checks if needle in c.text]


class DoctorTests(TmpCase):
    def setUp(self):
        super().setUp()
        self.ctx = make_ctx(self.tmp, appids=[570])
        write_all(self.ctx)
        self.ctx.layout.root.mkdir(parents=True, exist_ok=True)
        self.ctx.layout.settings_path.write_text("{}", encoding="utf-8")
        from homelab import secretstore
        secretstore.save(self.ctx.layout.secrets_path, self.ctx.secrets)

    def levels(self, checks):
        return {c.level for c in checks}

    def test_all_good(self):
        checks = doctor.run_doctor(self.ctx, deps())
        self.assertNotIn("fail", self.levels(checks), [c.text for c in checks if c.level == "fail"])
        self.assertEqual(doctor.exit_code(checks), 0)
        text = "\n".join(doctor.render(checks))
        self.assertIn("✅", text)

    def test_python_too_old(self):
        checks = doctor.run_doctor(self.ctx, deps(py_version=(3, 10, 4)))
        bad = by_text(checks, "3.12 이상이 필요")
        self.assertTrue(bad and bad[0].level == "fail")
        self.assertEqual(doctor.exit_code(checks), 1)

    def test_configured_python_missing(self):
        def run(argv, cwd, **kw):
            if cwd is None:
                return 127, "no such file"
            return 0, ""
        checks = doctor.run_doctor(self.ctx, deps(run=run))
        self.assertTrue(by_text(checks, "python 을 실행할 수 없어요"))
        self.assertEqual(doctor.exit_code(checks), 1)

    def test_project_not_importable(self):
        def run(argv, cwd, **kw):
            if len(argv) >= 3 and argv[2] == "import sidekick":
                return 1, "Traceback...\nModuleNotFoundError: x"
            return run_ok(argv, cwd)
        checks = doctor.run_doctor(self.ctx, deps(run=run))
        bad = by_text(checks, "불러오지 못했어요")
        self.assertEqual(len(bad), 1)
        self.assertIn("s21-sidekick", bad[0].text)
        self.assertEqual(bad[0].level, "fail")

    def test_config_loader_rejects(self):
        def run(argv, cwd, **kw):
            if len(argv) >= 4 and "load_config" in argv[2] and "sidekick" in argv[2]:
                return 1, "ConfigError: server.token을 설정하세요"
            return run_ok(argv, cwd)
        checks = doctor.run_doctor(self.ctx, deps(run=run))
        bad = by_text(checks, "프로젝트 로더가 거부")
        self.assertTrue(bad and "sidekick" in bad[0].text)

    def test_missing_config(self):
        self.ctx.cfg("sidekick").unlink()
        checks = doctor.run_doctor(self.ctx, deps())
        self.assertTrue([c for c in by_text(checks, "sidekick 설정이 없어요") if c.level == "fail"])

    def test_token_mismatch_phone_home_vs_backend(self):
        p = self.ctx.cfg("sidekick")
        d = json.loads(p.read_text(encoding="utf-8"))
        d["server"]["token"] = "다른-토큰-" + "z" * 20
        p.write_text(json.dumps(d), encoding="utf-8")
        checks = doctor.token_consistency(self.ctx)
        bad = [c for c in checks if c.level == "fail"]
        self.assertTrue(any("phone-home → sidekick" in c.text for c in bad), [c.text for c in checks])

    def test_token_mismatch_phone_home_token_file(self):
        self.ctx.layout.token_file("object-memory").write_text("바뀐-토큰-" + "q" * 20 + "\n", encoding="utf-8")
        bad = [c for c in doctor.token_consistency(self.ctx) if c.level == "fail"]
        self.assertTrue(any("object-memory" in c.text for c in bad))

    def test_token_mismatch_jarvis_s21(self):
        p = self.ctx.cfg("jarvis-hub")
        d = json.loads(p.read_text(encoding="utf-8"))
        d["devices"]["s21"]["token"] = "x" * 24
        p.write_text(json.dumps(d), encoding="utf-8")
        bad = [c for c in doctor.token_consistency(self.ctx) if c.level == "fail"]
        self.assertTrue(any("jarvis" in c.text for c in bad))

    def test_tokens_consistent_ok(self):
        checks = doctor.token_consistency(self.ctx)
        self.assertEqual([c for c in checks if c.level != "ok"], [])
        self.assertEqual(len([c for c in checks if "토큰 일치" in c.text]), 5)

    def test_secrets_missing_tokens(self):
        self.ctx.secrets["tokens"]["sidekick"] = "짧다"
        checks = doctor.token_consistency(self.ctx)
        self.assertEqual(checks[0].level, "fail")
        self.assertIn("homelab init", checks[0].hint)

    def test_port_used_by_stranger(self):
        checks = doctor.run_doctor(self.ctx, deps(port_in_use=lambda h, p: p == 8765))
        bad = by_text(checks, "sidekick 포트 8765")
        self.assertEqual(bad[0].level, "fail")
        self.assertIn("ports", bad[0].hint)
        self.assertTrue(by_text(checks, "object-memory 포트 8788: 비어"))

    def test_port_used_by_our_own_service(self):
        from homelab import procs
        procs.write_pid(self.ctx.layout.pid_file("sidekick"), {"pid": 4242})
        checks = doctor.run_doctor(self.ctx, deps(port_in_use=lambda h, p: p == 8765, alive=lambda pid: pid == 4242))
        ok = by_text(checks, "sidekick 포트 8765")
        self.assertEqual(ok[0].level, "ok")
        self.assertIn("내가 켠", ok[0].text)

    def test_duplicate_ports(self):
        self.ctx.settings["ports"] = {"voice-journal": 8765}
        checks = doctor.run_doctor(self.ctx, deps())
        self.assertTrue([c for c in checks if c.level == "fail" and "같은 포트" in c.text])

    def test_optional_tools_warn_with_hints(self):
        checks = doctor.run_doctor(self.ctx, deps(which=lambda n: None))
        for tool in ("ffmpeg", "nvidia-smi", "pdftotext", "tesseract"):
            c = by_text(checks, tool + " 없음")
            self.assertTrue(c and c[0].level == "warn" and c[0].hint, tool)
        node = by_text(checks, "node")
        self.assertEqual(node[0].level, "ok")  # node 는 필요 없다
        self.assertEqual(doctor.exit_code(checks), 0)  # 선택 도구는 실패가 아니다

    def test_llm_reachable_and_configurable_url(self):
        op = FakeOpener()
        checks = doctor.run_doctor(self.ctx, deps(opener=op), llm_url="http://127.0.0.1:9999/v1/")
        self.assertEqual(op.urls, ["http://127.0.0.1:9999/v1/models"])
        c = by_text(checks, "응답")
        self.assertTrue(any(x.level == "ok" and "모델 2개" in x.text for x in c))

    def test_llm_unreachable_is_warning(self):
        op = FakeOpener(error=urllib.error.URLError("refused"))
        checks = doctor.run_doctor(self.ctx, deps(opener=op))
        c = by_text(checks, "LLM 서버에 닿지 않아요")
        self.assertEqual(c[0].level, "warn")
        self.assertIn("llama-server", c[0].hint)
        self.assertEqual(doctor.exit_code(checks), 0)

    def test_report_is_korean_with_hints(self):
        checks = doctor.run_doctor(self.ctx, deps(which=lambda n: None, py_version=(3, 11, 0)))
        lines = doctor.render(checks)
        text = "\n".join(lines)
        self.assertIn("[파이썬]", text)
        self.assertIn("→ ", text)
        self.assertIn("❌", text)
        self.assertIn("요약:", text)

    def test_cli_doctor_exit_code(self):
        base = ["--dir", str(self.ctx.layout.root), "--home", str(self.tmp)]
        out = io.StringIO()
        rc = cli.main(base + ["doctor"], out=out, deps=deps(py_version=(3, 9, 0)))
        self.assertEqual(rc, 1)
        out = io.StringIO()
        rc = cli.main(base + ["doctor"], out=out, deps=deps())
        self.assertEqual(rc, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
