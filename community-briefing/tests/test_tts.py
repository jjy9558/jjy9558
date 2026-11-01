import json
import os
import subprocess
import tempfile
import unittest
import wave
import xml.etree.ElementTree as ET
from unittest import mock

import fixtures as fx  # noqa: F401  (sets sys.path)
from briefing import tts

BRIEFING = {
    "date": "2026-11-01", "overview": ["로컬 LLM 소식이 많았습니다", "GPU 가격이 내렸습니다", ""],
    "clusters": [
        {"topic": "로컬 LLM", "items": [
            {"title": "llama.cpp 새 포맷", "summary": "VRAM이 줄었습니다.", "sources": ["HN"]},
            {"title": "Ollama 업데이트", "summary": "Ollama 업데이트", "sources": ["GN"]}]},
        {"topic": "GPU", "items": [{"title": "RTX 가격 하락", "summary": "가격이 내렸습니다.", "sources": ["GN"]}]},
    ],
}


def make_wav(path, frames=100, rate=16000, channels=1):
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * frames * channels)


class FakeProc:
    returncode = 0
    stderr = b""


class ScriptTests(unittest.TestCase):
    def test_template_monologue(self):
        s = tts.build_script(BRIEFING)
        self.assertTrue(s.startswith("안녕하세요, 2026-11-01 커뮤니티 브리핑입니다."))
        self.assertIn("다음은 GPU 소식입니다.", s)
        self.assertIn("llama.cpp 새 포맷. VRAM이 줄었습니다.", s)
        self.assertEqual(s.count("Ollama 업데이트"), 1)  # summary == title is not repeated
        self.assertTrue(s.rstrip().endswith("좋은 하루 보내세요."))

    def test_template_dialogue(self):
        s = tts.build_script(BRIEFING, style="dialogue")
        lines = s.splitlines()
        self.assertTrue(all(l[:2] in ("A:", "B:") for l in lines))
        self.assertEqual({sp for sp, _ in tts.parse_script(s)}, {"A", "B"})
        self.assertNotIn("A:", " ".join(t for _, t in tts.parse_script(s)))

    def test_empty_briefing(self):
        s = tts.build_script({"date": "d", "overview": [], "clusters": []})
        self.assertIn("새 소식이 없습니다", s)

    def test_llm_used_and_fallbacks(self):
        class LLM:
            def __init__(self, out):
                self.out = out

            def chat(self, messages, max_tokens=300):
                return self.out

        good = "안녕하세요 여러분, 오늘의 브리핑을 시작합니다. 먼저 로컬 LLM 소식입니다. 마무리 인사드립니다."
        self.assertEqual(tts.build_script(BRIEFING, LLM("<think>x</think>" + good)), good)
        self.assertEqual(tts.build_script(BRIEFING, LLM("짧음")), tts.template_script(BRIEFING))
        dlg = "\n".join(["A: 안녕하세요", "B: 반갑습니다", "A: 소식입니다", "B: 네", "잡담"])
        out = tts.build_script(BRIEFING, LLM(dlg), "dialogue")
        self.assertEqual(len(out.splitlines()), 4)
        self.assertEqual(tts.build_script(BRIEFING, LLM("A: 하나"), "dialogue"),
                         tts.template_script(BRIEFING, "dialogue"))

        class Down:
            def chat(self, *a, **k):
                raise RuntimeError("down")

        self.assertEqual(tts.build_script(BRIEFING, Down()), tts.template_script(BRIEFING))

    def test_speech_clean_strips_urls_and_markdown(self):
        self.assertEqual(tts.parse_script("**굵게** 보기 https://x.example/a?b=1 끝"), [(None, "굵게 보기 끝")])


class ChunkTests(unittest.TestCase):
    def test_chunks_respect_limit_and_keep_text(self):
        text = " ".join("이것은 %d번째 문장입니다." % n for n in range(40))
        chunks = tts.chunk_text(text, 100)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c) <= 100 for c in chunks))
        self.assertEqual(" ".join(chunks).split(), text.split())

    def test_long_sentence_hard_split(self):
        chunks = tts.chunk_text("가" * 250, 100)
        self.assertEqual([len(c) for c in chunks], [100, 100, 50])

    def test_short_text_single_chunk_and_empty(self):
        self.assertEqual(tts.chunk_text("안녕. 반가워.", 300), ["안녕. 반가워."])
        self.assertEqual(tts.chunk_text("", 300), [])

    def test_plan_keeps_speaker(self):
        plan = tts.plan_chunks("A: 첫째.\nB: 둘째.", 300)
        self.assertEqual(plan, [("A", "첫째."), ("B", "둘째.")])


class WavTests(unittest.TestCase):
    def test_concat(self):
        with tempfile.TemporaryDirectory() as d:
            a, b, out = (os.path.join(d, n) for n in ("a.wav", "b.wav", "o.wav"))
            make_wav(a, 100)
            make_wav(b, 50)
            tts.concat_wavs([a, b], out)
            with wave.open(out) as w:
                self.assertEqual((w.getnframes(), w.getframerate(), w.getnchannels()), (150, 16000, 1))
            tts.concat_wavs([a, b], out, pause_ms=10)
            with wave.open(out) as w:
                self.assertEqual(w.getnframes(), 150 + 160)

    def test_mismatch_and_empty(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.wav"), os.path.join(d, "b.wav")
            make_wav(a)
            make_wav(b, rate=22050)
            with self.assertRaises(tts.TTSError):
                tts.concat_wavs([a, b], os.path.join(d, "o.wav"))
            with self.assertRaises(tts.TTSError):
                tts.concat_wavs([], os.path.join(d, "o.wav"))


class BackendTests(unittest.TestCase):
    SECRET = "비밀스러운 본문 텍스트 $(calc) ; \"quote\""

    def _fake_run(self, calls, txt_seen=None):
        def run(cmd, **kw):
            calls.append((cmd, kw))
            wav = cmd[cmd.index("--output_file") + 1] if "--output_file" in cmd else cmd[cmd.index("-OutFile") + 1]
            if txt_seen is not None and "-TextFile" in cmd:
                with open(cmd[cmd.index("-TextFile") + 1], encoding="utf-8") as f:
                    txt_seen.append(f.read())
            make_wav(wav, 80)
            return FakeProc()
        return run

    def test_sapi_command(self):
        cmd = tts.build_sapi_command("s.ps1", "t.txt", "o.wav", "Microsoft Heami", 2)
        self.assertIsInstance(cmd, list)
        self.assertEqual(cmd[-2:], ["-Voice", "Microsoft Heami"])
        self.assertIn("-File", cmd)
        self.assertNotIn("-Command", cmd)
        self.assertNotIn("-Voice", tts.build_sapi_command("s.ps1", "t.txt", "o.wav"))

    def test_sapi_synthesize_text_via_file_only(self):
        calls, seen = [], []
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.wav")
            res = tts.synthesize(self.SECRET, out, "sapi", runner=self._fake_run(calls, seen), voice="V")
            self.assertEqual(res, out)
            self.assertTrue(os.path.exists(out))
            self.assertTrue(os.path.exists(os.path.join(d, "x.script.txt")))
        self.assertEqual(len(calls), 1)
        cmd, kw = calls[0]
        self.assertFalse(kw.get("shell"))
        self.assertFalse(any("비밀스러운" in a or "calc" in a for a in cmd))
        self.assertEqual(seen, [tts.speech_clean(self.SECRET)])
        self.assertIsInstance(cmd, list)

    def test_piper_command_and_stdin(self):
        cmd = tts.build_piper_command("m.onnx", "o.wav", "piper", ["--length_scale", "1.1"])
        self.assertEqual(cmd[:5], ["piper", "--model", "m.onnx", "--output_file", "o.wav"])
        with self.assertRaises(tts.TTSError):
            tts.build_piper_command("", "o.wav")
        calls = []
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "p.wav")
            tts.synthesize("A: 첫째 문장.\nB: 둘째 문장.", out, "piper", runner=self._fake_run(calls),
                           voices={"A": "a.onnx", "B": "b.onnx"})
            self.assertTrue(os.path.exists(out))
        self.assertEqual([c[0][2] for c in calls], ["a.onnx", "b.onnx"])
        for cmd, kw in calls:
            self.assertFalse(kw.get("shell"))
            self.assertFalse(any("문장" in a for a in cmd))
            self.assertIn("문장", kw["input"].decode("utf-8"))

    def test_real_subprocess_never_uses_shell(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(tts.subprocess, "run") as run:
            def side(cmd, **kw):
                make_wav(cmd[cmd.index("--output_file") + 1])
                return FakeProc()
            run.side_effect = side
            tts.synthesize(self.SECRET, os.path.join(d, "o.wav"), "piper", model="m.onnx")
        self.assertFalse(run.call_args[1].get("shell"))
        self.assertNotIn(self.SECRET, " ".join(run.call_args[0][0]))

    def test_failures_raise(self):
        with tempfile.TemporaryDirectory() as d:
            bad = mock.Mock(return_value=mock.Mock(returncode=1, stderr="boom".encode()))
            with self.assertRaises(tts.TTSError):
                tts.synthesize("안녕하세요.", os.path.join(d, "o.wav"), "piper", model="m", runner=bad)
            missing = mock.Mock(side_effect=FileNotFoundError())
            with self.assertRaises(tts.TTSError):
                tts.synthesize("안녕하세요.", os.path.join(d, "o.wav"), "sapi", runner=missing)

    def test_openai_compat(self):
        reqs = []

        class Resp:
            def __init__(self, data):
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self.data

        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src.wav")
            make_wav(src, 60)
            with open(src, "rb") as fh:
                blob = fh.read()

            def opener(req, timeout):
                reqs.append(req)
                return Resp(blob)

            out = os.path.join(d, "o.wav")
            tts.synthesize("안녕하세요. 반갑습니다.", out, "openai_compat", opener=opener,
                           base_url="http://127.0.0.1:8880/v1", voice="ko", api_key="k")
            self.assertTrue(os.path.exists(out))

            def junk(req, timeout):
                return Resp(b"not a wav")

            with self.assertRaises(tts.TTSError):
                tts.synthesize("안녕하세요.", out, "openai_compat", opener=junk)
        req = reqs[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:8880/v1/audio/speech")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual((body["voice"], body["response_format"]), ("ko", "wav"))
        self.assertEqual(req.get_header("Authorization"), "Bearer k")

    def test_none_and_unknown_backend(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(tts.synthesize("안녕", os.path.join(d, "a.wav")))
            self.assertIsNone(tts.synthesize("안녕", os.path.join(d, "b.wav"), "future"))
            self.assertTrue(os.path.exists(os.path.join(d, "a.script.txt")))


class FeedTests(unittest.TestCase):
    def test_feed_is_valid_rss(self):
        with tempfile.TemporaryDirectory() as d:
            make_wav(os.path.join(d, "2026-11-01.wav"), 16000)
            make_wav(os.path.join(d, "2026-11-02.wav"), 8000)
            make_wav(os.path.join(d, "2026-11-02-2.wav"), 8000)
            open(os.path.join(d, "notes.wav"), "w").close()  # ignored
            with open(os.path.join(d, "2026-11-02.json"), "w", encoding="utf-8") as f:
                json.dump({"overview": ["요약 & <특수>", "둘째", ""]}, f, ensure_ascii=False)
            path = tts.write_feed(d, "http://pc.tailnet.ts.net:8000/", "제목 & 테스트")
            root = ET.parse(path).getroot()
        self.assertEqual(root.tag, "rss")
        ch = root.find("channel")
        self.assertEqual(ch.findtext("title"), "제목 & 테스트")
        items = ch.findall("item")
        self.assertEqual([i.findtext("guid") for i in items], ["2026-11-02-2.wav", "2026-11-02.wav", "2026-11-01.wav"])
        enc = items[1].find("enclosure")
        self.assertEqual(enc.get("url"), "http://pc.tailnet.ts.net:8000/2026-11-02.wav")
        self.assertGreater(int(enc.get("length")), 0)
        self.assertIn("요약 & <특수>", items[1].findtext("description"))
        self.assertEqual(items[2].findtext("{http://www.itunes.com/dtds/podcast-1.0.dtd}duration"), "1")
        self.assertTrue(items[0].findtext("pubDate").endswith("+0000") or "GMT" in items[0].findtext("pubDate"))


class PipelineWiringTests(unittest.TestCase):
    def test_pipeline_makes_wav_feed_and_timeline_html(self):
        from datetime import datetime, timezone
        from briefing import config, pipeline
        with tempfile.TemporaryDirectory() as d:
            cfg = config.from_dict({"sources": fx.SOURCES, "interests": fx.INTERESTS,
                                    "timeline": {"enabled": True},
                                    "tts": {"enabled": True, "backend": "piper", "model": "m.onnx",
                                            "feed": {"enabled": True, "base_url": "http://h:8000/"}}}, d)

            def fake(cmd, **kw):
                make_wav(cmd[cmd.index("--output_file") + 1])
                return FakeProc()

            with mock.patch.object(tts.subprocess, "run", side_effect=fake):
                res = pipeline.run(cfg, since="24h", fetcher=fx.FakeFetcher(), llm_client=fx.FakeLLM(),
                                   now=datetime(2026, 11, 1, tzinfo=timezone.utc))
            p = res["paths"]
            self.assertTrue(p["audio"].endswith("2026-11-01.wav") and os.path.exists(p["audio"]))
            self.assertTrue(os.path.exists(p["timeline_html"]))
            self.assertEqual(ET.parse(p["podcast_feed"]).getroot().tag, "rss")

    def test_tts_failure_is_not_fatal(self):
        from datetime import datetime, timezone
        from briefing import config, pipeline
        with tempfile.TemporaryDirectory() as d:
            cfg = config.from_dict({"sources": fx.SOURCES, "interests": fx.INTERESTS,
                                    "tts": {"enabled": True, "backend": "piper"}}, d)  # no model -> TTSError
            res = pipeline.run(cfg, since="24h", fetcher=fx.FakeFetcher(), llm_client=None,
                               now=datetime(2026, 11, 1, tzinfo=timezone.utc))
            self.assertTrue(os.path.exists(res["paths"]["markdown"]))
            self.assertNotIn("audio", res["paths"])


if __name__ == "__main__":
    unittest.main()
