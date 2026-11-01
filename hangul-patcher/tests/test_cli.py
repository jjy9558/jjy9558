import csv
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from hpatch import cli
from hpatch.llm import LLMError, LLMUnreachable, OpenAIChatClient
from hpatch.tm import TranslationMemory
from helpers import FakeLLM, make_renpy, make_rpgmaker, snapshot


def run(argv, client=None):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv, client=client)
    return code, out.getvalue(), err.getvalue()


class CliWorkflowTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name) / "game"
        make_rpgmaker(self.root, mz=True)

    def test_scan_extract_translate_apply_restore_flow(self):
        before = snapshot(self.root)
        code, out, _ = run(["scan", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("RPG Maker MZ", out)
        self.assertIn("번역 대상", out)
        self.assertEqual(snapshot(self.root), before)  # scan 은 아무것도 쓰지 않음

        code, out, _ = run(["extract", str(self.root)])
        self.assertEqual(code, 0)
        units = json.loads((self.root / ".hpatch/units.json").read_text("utf-8"))["units"]
        self.assertTrue(units and "hash" in units[0])

        code, out, _ = run(["translate", str(self.root), "--dry-run", "--show-prompt"])
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out)
        self.assertFalse((self.root / ".hpatch/tm.sqlite").exists() and TranslationMemory(self.root / ".hpatch/tm.sqlite").count())

        llm = FakeLLM()
        code, out, _ = run(["translate", str(self.root), "--limit", "5", "--batch-size", "3"], client=llm)
        self.assertEqual(code, 0)
        tm = TranslationMemory(self.root / ".hpatch/tm.sqlite")
        self.assertEqual(tm.count(), 5)
        tm.close()
        code, out, _ = run(["translate", str(self.root)], client=llm)
        self.assertEqual(code, 0)

        code, out, _ = run(["apply", str(self.root)])
        self.assertEqual(code, 0)
        self.assertTrue((self.root / ".hpatch_backup/manifest.json").exists())
        code, out, _ = run(["restore", str(self.root)])
        self.assertEqual(code, 0)
        after = snapshot(self.root)
        self.assertEqual({k: v for k, v in after.items() if not k.startswith(".hpatch/")}, before)

    def test_translate_when_llm_down_exits_3_and_keeps_progress(self):
        llm = FakeLLM(fail_after=1)
        code, out, err = run(["translate", str(self.root), "--batch-size", "2"], client=llm)
        self.assertEqual(code, 3)
        self.assertIn("중단", out)
        tm = TranslationMemory(self.root / ".hpatch/tm.sqlite")
        self.assertEqual(tm.count(), 2)
        tm.close()
        # 서버가 돌아오면 이어서
        code, _, _ = run(["translate", str(self.root)], client=FakeLLM())
        self.assertEqual(code, 0)

    def test_translate_with_real_client_and_dead_server(self):
        code, out, _ = run(["translate", str(self.root), "--endpoint", "http://127.0.0.1:9/v1", "--timeout", "2"])
        self.assertEqual(code, 3)

    def test_apply_without_tm_fails_cleanly(self):
        code, out, _ = run(["apply", str(self.root)])
        self.assertEqual(code, 1)
        self.assertFalse((self.root / ".hpatch_backup").exists())

    def test_unknown_game_dir(self):
        with tempfile.TemporaryDirectory() as td:
            code, _, err = run(["scan", td])
        self.assertEqual(code, 1)
        self.assertIn("지원하는 엔진", err)

    def test_glossary_file_used(self):
        gl = self.root / ".hpatch"
        gl.mkdir()
        (gl / "glossary.json").write_text(json.dumps({"terms": {"Potion": "물약"}, "keep": ["Slime"]}), encoding="utf-8")
        code, _, _ = run(["translate", str(self.root)], client=FakeLLM())
        tm = TranslationMemory(self.root / ".hpatch/tm.sqlite")
        self.assertEqual(tm.get("Potion"), "물약")
        self.assertEqual(tm.get("Slime"), "Slime")
        tm.close()

    def test_csv_export_edit_import_wins_over_llm(self):
        run(["translate", str(self.root)], client=FakeLLM())
        csv_path = Path(self._td.name) / "review.csv"
        code, out, _ = run(["export-csv", str(self.root), "--out", str(csv_path)])
        self.assertEqual(code, 0)
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertTrue(rows[0]["hash"] and "source" in rows[0])
        potion = next(r for r in rows if r["source"] == "Potion")
        self.assertEqual(potion["translation"], "KO:Potion")
        potion["translation"] = "수동 번역"
        bad = next(r for r in rows if r["source"] == "%1 is poisoned!")
        bad["translation"] = "제어코드 없음"  # %1 이 사라짐 -> 거부되어야 함
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        code, out, _ = run(["import-csv", str(self.root), str(csv_path)])
        self.assertEqual(code, 0)
        self.assertIn("경고", out)
        tm = TranslationMemory(self.root / ".hpatch/tm.sqlite")
        self.assertEqual(tm.get("Potion"), "수동 번역")
        self.assertEqual(tm.get("%1 is poisoned!"), "KO:%1 is poisoned!")
        tm.close()
        # 이후 LLM 재번역은 수동 항목을 덮어쓰지 않는다
        run(["translate", str(self.root)], client=FakeLLM())
        tm = TranslationMemory(self.root / ".hpatch/tm.sqlite")
        self.assertEqual(tm.get("Potion"), "수동 번역")
        tm.close()
        # --only-untranslated 는 번역된 행을 뺀다
        code, out, _ = run(["export-csv", str(self.root), "--out", str(csv_path), "--only-untranslated"])
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            left = list(csv.DictReader(f))
        self.assertTrue(all(r["translation"] == "" for r in left))

    def test_renpy_cli_flow_and_compiled_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "rp"
            make_renpy(root)
            before = snapshot(root)
            code, out, _ = run(["scan", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("Ren'Py", out)
            run(["translate", str(root)], client=FakeLLM())
            code, out, _ = run(["apply", str(root), "--font", str(Path(td) / "nofont.ttf")])
            self.assertEqual(code, 0)
            self.assertTrue((root / "game/tl/korean/script.rpy").exists())
            run(["restore", str(root)])
            after = {k: v for k, v in snapshot(root).items() if not k.startswith(".hpatch/")}
            self.assertEqual(after, before)
            root2 = Path(td) / "rp2"
            make_renpy(root2, with_rpy=False)
            code, out, _ = run(["scan", str(root2)])
            self.assertEqual(code, 1)
            self.assertIn("unrpa", out)


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).last = {"path": self.path, "body": body, "auth": self.headers.get("Authorization")}
        if self.mode == "503":
            self.send_response(503)
            self.end_headers()
            return
        if self.mode == "bad":
            payload = b"not json"
        else:
            payload = json.dumps({"choices": [{"message": {"content": "응답"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


class ClientTests(unittest.TestCase):
    def serve(self, mode):
        handler = type("H", (_Handler,), {"mode": mode})
        srv = HTTPServer(("127.0.0.1", 0), handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return srv, handler

    def test_ok_response_and_request_shape(self):
        srv, handler = self.serve("ok")
        c = OpenAIChatClient(f"http://127.0.0.1:{srv.server_port}/v1", model="m", api_key="k", timeout=5)
        self.assertEqual(c.complete([{"role": "user", "content": "x"}]), "응답")
        self.assertEqual(handler.last["path"], "/v1/chat/completions")
        self.assertEqual(handler.last["body"]["model"], "m")
        self.assertEqual(handler.last["auth"], "Bearer k")

    def test_errors_map_to_exceptions(self):
        srv, _ = self.serve("503")
        c = OpenAIChatClient(f"http://127.0.0.1:{srv.server_port}/v1", timeout=5)
        with self.assertRaises(LLMUnreachable):
            c.complete([{"role": "user", "content": "x"}])
        srv2, _ = self.serve("bad")
        c2 = OpenAIChatClient(f"http://127.0.0.1:{srv2.server_port}/v1", timeout=5)
        with self.assertRaises(LLMError) as cm:
            c2.complete([{"role": "user", "content": "x"}])
        self.assertNotIsInstance(cm.exception, LLMUnreachable)
        with self.assertRaises(LLMUnreachable):
            OpenAIChatClient("http://127.0.0.1:9/v1", timeout=2).complete([{"role": "user", "content": "x"}])


if __name__ == "__main__":
    unittest.main()
