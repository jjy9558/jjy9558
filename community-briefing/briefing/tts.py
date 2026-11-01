"""음성 팟캐스트: 브리핑 JSON -> 한국어 구어체 대본 -> TTS 백엔드 -> WAV (+ 팟캐스트 RSS).

백엔드 (config tts.backend):
    "sapi"          Windows 내장 음성 (PowerShell System.Speech). 한국어 음성이 설치돼 있어야 함
    "piper"         piper CLI (모델 경로 필요, 텍스트는 stdin)
    "openai_compat" POST {base_url}/audio/speech 를 지원하는 로컬 서버
    "none"          오디오 없이 대본(.script.txt)만 저장

외부 프로그램은 항상 인자 리스트(shell=False)로 호출하고, 읽을 텍스트는 명령줄이 아니라
임시 파일/stdin 으로만 전달합니다.
"""
import email.utils
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import wave
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("briefing")

SPEAKERS = {"A": "민준", "B": "서연"}
_LABEL_RE = re.compile(r"^\s*([AB])\s*[:：]\s*(.+?)\s*$")

SCRIPT_SYSTEM = ("당신은 한국어 아침 뉴스 팟캐스트 작가입니다. 귀로 듣기 좋은 자연스러운 구어체로 쓰고, "
                 "주어진 내용에 없는 사실은 지어내지 않습니다. URL, 마크다운, 이모지는 쓰지 않습니다.")


class TTSError(Exception):
    pass


# ---------------------------------------------------------------- script

def _line(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


def _sentence(text):
    text = _line(text)
    return text if not text or text[-1] in ".!?…。" else text + "."


def _items(briefing):
    for cluster in briefing.get("clusters", []):
        for item in cluster.get("items", []):
            yield cluster.get("topic", ""), item


def template_script(briefing, style="monologue"):
    """LLM 없이 만드는 결정적 대본. style: "monologue" | "dialogue" (A/B 두 진행자)."""
    date = briefing.get("date", "")
    overview = [_sentence(l) for l in briefing.get("overview", []) if _line(l)]
    clusters = briefing.get("clusters", [])
    if style == "dialogue":
        a, b = SPEAKERS["A"], SPEAKERS["B"]
        out = ["A: 안녕하세요, %s 커뮤니티 브리핑입니다. 진행을 맡은 %s입니다." % (date, a),
               "B: 안녕하세요, %s입니다. 오늘 어떤 소식이 있었나요?" % b]
        if overview:
            out.append("A: 먼저 오늘의 흐름부터 짚어 볼게요. " + " ".join(overview))
        if not clusters:
            out.append("B: 오늘은 관심사에 맞는 새 소식이 없었네요.")
        for topic, items in ((c.get("topic", ""), c.get("items", [])) for c in clusters):
            out.append("B: %s 쪽 소식은 어떤가요?" % topic)
            for n, item in enumerate(items):
                body = _sentence(item.get("summary")) or ""
                title = _line(item.get("title"))
                if body and body.rstrip(".") == title:
                    body = ""
                who = "A" if n % 2 == 0 else "B"
                out.append("%s: %s%s" % (who, _sentence(title), (" " + body) if body else ""))
        out += ["B: 오늘 소식은 여기까지입니다.", "A: 들어 주셔서 감사합니다. 좋은 하루 보내세요."]
        return "\n".join(out)
    lines = ["안녕하세요, %s 커뮤니티 브리핑입니다." % date]
    lines += overview
    if not clusters:
        lines.append("오늘은 관심사에 맞는 새 소식이 없습니다.")
    for c in clusters:
        lines.append("다음은 %s 소식입니다." % c.get("topic", ""))
        for item in c.get("items", []):
            body = _sentence(item.get("summary"))
            title = _line(item.get("title"))
            lines.append(_sentence(title) + ((" " + body) if body and body.rstrip(".") != title else ""))
    lines.append("오늘 브리핑은 여기까지입니다. 좋은 하루 보내세요.")
    return "\n".join(lines)


def build_script(briefing, llm=None, style="monologue", max_items=12):
    """대본 만들기. llm(chat 메서드를 가진 클라이언트)이 있으면 LLM으로 다듬고, 실패/부실하면 템플릿."""
    fallback = template_script(briefing, style)
    if llm is None or not list(_items(briefing)):
        return fallback
    try:
        from .summarize import clean_llm_text
        listing = "\n".join("- [%s] %s: %s (출처: %s)" % (t, _line(i.get("title")), _line(i.get("summary")),
                                                         ", ".join(i.get("sources", [])))
                            for t, i in list(_items(briefing))[:max_items])
        head = "날짜: %s\n오늘의 흐름:\n%s\n\n기사 목록:\n%s" % (
            briefing.get("date", ""), "\n".join(l for l in briefing.get("overview", []) if l), listing)
        if style == "dialogue":
            ask = ("아래 내용으로 두 진행자(A는 %s, B는 %s)가 나누는 3~5분 분량의 아침 팟캐스트 대화 대본을 쓰세요. "
                   "모든 줄은 'A: ' 또는 'B: '로 시작하고, 인사로 시작해 마무리 인사로 끝냅니다. "
                   "대본만 출력하세요.\n\n" % (SPEAKERS["A"], SPEAKERS["B"]))
        else:
            ask = ("아래 내용으로 진행자 한 명이 읽는 3~5분 분량의 아침 팟캐스트 대본을 쓰세요. "
                   "인사로 시작해 마무리 인사로 끝내고, 대본만 출력하세요.\n\n")
        text = clean_llm_text(llm.chat([{"role": "system", "content": SCRIPT_SYSTEM},
                                        {"role": "user", "content": ask + head}], 2000))
        if style == "dialogue":
            lines = [l for l in text.splitlines() if _LABEL_RE.match(l)]
            if len(lines) < 4:
                return fallback
            return "\n".join(lines)
        text = "\n".join(l.strip() for l in text.splitlines() if l.strip())
        return text if len(text) >= 40 else fallback
    except Exception as e:  # LLM down / garbage: deterministic script still works
        logger.warning("script LLM failed, using template: %s", e)
        return fallback


# ---------------------------------------------------------------- chunking

_URL_RE = re.compile(r"https?://\S+")


def speech_clean(text):
    text = _URL_RE.sub("", text)
    text = re.sub(r"[*_`#>\[\]]", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def parse_script(script):
    """-> [(speaker or None, text)]. 'A: ...' 라벨은 화자로 분리 (읽을 때는 라벨을 읽지 않음)."""
    segs = []
    for raw in (script or "").splitlines():
        if not raw.strip():
            continue
        m = _LABEL_RE.match(raw)
        speaker, text = (m.group(1), m.group(2)) if m else (None, raw)
        text = speech_clean(text)
        if text:
            segs.append((speaker, text))
    return segs


def chunk_text(text, max_chars=300):
    """문장 경계 우선으로 max_chars 이하 조각으로 분할. 한 문장이 길면 공백, 그래도 길면 강제 분할."""
    sentences = [s for s in re.split(r"(?<=[.!?…。])\s+|\n+", text or "") if s.strip()]
    pieces = []
    for s in sentences:
        s = s.strip()
        while len(s) > max_chars:
            cut = s.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            pieces.append(s[:cut].strip())
            s = s[cut:].strip()
        if s:
            pieces.append(s)
    chunks, cur = [], ""
    for p in pieces:
        if cur and len(cur) + 1 + len(p) > max_chars:
            chunks.append(cur)
            cur = p
        else:
            cur = (cur + " " + p).strip()
    if cur:
        chunks.append(cur)
    return chunks


def plan_chunks(script, max_chars=300):
    """-> [(speaker, chunk)] 전체 대본의 합성 순서."""
    return [(sp, c) for sp, text in parse_script(script) for c in chunk_text(text, max_chars)]


# ---------------------------------------------------------------- wav

def concat_wavs(paths, out_path, pause_ms=0):
    """stdlib wave 로 WAV 이어붙이기. 형식(채널/비트/샘플레이트)이 다르면 TTSError."""
    if not paths:
        raise TTSError("no wav chunks to concatenate")
    params = None
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with wave.open(out_path, "wb") as out:
        for n, p in enumerate(paths):
            with wave.open(p, "rb") as w:
                cur = (w.getnchannels(), w.getsampwidth(), w.getframerate())
                if params is None:
                    params = cur
                    out.setnchannels(cur[0])
                    out.setsampwidth(cur[1])
                    out.setframerate(cur[2])
                elif cur != params:
                    raise TTSError("wav format mismatch: %s vs %s" % (cur, params))
                if n and pause_ms:
                    out.writeframes(b"\x00" * (int(params[2] * pause_ms / 1000) * params[0] * params[1]))
                out.writeframes(w.readframes(w.getnframes()))
    return out_path


def wav_duration(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate() or 1)


# ---------------------------------------------------------------- backends

# 고정 스크립트: 사용자 텍스트/경로는 절대 이 문자열에 들어가지 않고 -File 인자로만 전달된다.
SAPI_PS1 = """param([string]$TextFile, [string]$OutFile, [string]$Voice = "", [int]$Rate = 0)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($Voice) { $s.SelectVoice($Voice) }
    $s.Rate = $Rate
    $s.SetOutputToWaveFile($OutFile)
    $s.Speak([System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8))
} finally { $s.Dispose() }
"""


def build_sapi_command(ps1_path, text_file, wav_path, voice="", rate=0, powershell="powershell"):
    cmd = [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps1_path,
           "-TextFile", text_file, "-OutFile", wav_path, "-Rate", str(int(rate))]
    if voice:
        cmd += ["-Voice", voice]
    return cmd


def build_piper_command(model, wav_path, piper="piper", extra_args=()):
    if not model:
        raise TTSError("tts.model (piper .onnx path) is required for backend 'piper'")
    return [piper, "--model", model, "--output_file", wav_path] + [str(a) for a in extra_args]


def _run(runner, cmd, data=None, timeout=300):
    try:
        proc = runner(cmd, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                      shell=False)
    except FileNotFoundError:
        raise TTSError("command not found: %s" % cmd[0])
    except subprocess.TimeoutExpired:
        raise TTSError("%s timed out" % cmd[0])
    if proc.returncode != 0:
        err = (proc.stderr or b"")
        err = err.decode("utf-8", "replace") if isinstance(err, bytes) else str(err)
        raise TTSError("%s failed (%s): %s" % (cmd[0], proc.returncode, err.strip()[:300]))


def _http_speech(opts, text, voice, wav_path):
    base = opts.get("base_url") or "http://127.0.0.1:8880/v1"
    body = {"model": opts.get("model", "tts-1"), "input": text, "voice": voice or "alloy",
            "response_format": "wav"}
    headers = {"Content-Type": "application/json"}
    if opts.get("api_key"):
        headers["Authorization"] = "Bearer " + opts["api_key"]
    req = urllib.request.Request(base.rstrip("/") + "/audio/speech", data=json.dumps(body).encode("utf-8"),
                                 headers=headers)
    opener = opts.get("opener") or urllib.request.build_opener(urllib.request.ProxyHandler({})).open
    try:
        with opener(req, timeout=opts.get("timeout", 120)) as resp:
            data = resp.read()
    except Exception as e:
        raise TTSError("speech request failed: %s" % e)
    with open(wav_path, "wb") as f:
        f.write(data)
    try:
        wave.open(wav_path, "rb").close()
    except (wave.Error, EOFError):
        raise TTSError("server did not return a WAV (set response format to wav)")


def _synth_chunk(backend, text, speaker, wav_path, work, opts):
    voices = opts.get("voices") or {}
    voice = voices.get(speaker) or opts.get("voice", "")
    runner = opts.get("runner") or subprocess.run
    if backend == "sapi":
        ps1 = os.path.join(work, "speak.ps1")
        if not os.path.exists(ps1):
            with open(ps1, "w", encoding="utf-8-sig") as f:  # BOM so Windows PowerShell 5 reads it as UTF-8
                f.write(SAPI_PS1)
        txt = os.path.join(work, "chunk.txt")
        with open(txt, "w", encoding="utf-8") as f:
            f.write(text)
        _run(runner, build_sapi_command(ps1, txt, wav_path, voice, opts.get("rate", 0),
                                        opts.get("powershell", "powershell")))
    elif backend == "piper":
        _run(runner, build_piper_command(voice or opts.get("model", ""), wav_path, opts.get("piper", "piper"),
                                         opts.get("piper_args", ())), text.encode("utf-8"))
    elif backend == "openai_compat":
        _http_speech(opts, text, voice, wav_path)
    else:
        raise TTSError("unknown tts backend %r" % backend)
    if not os.path.exists(wav_path):
        raise TTSError("backend %s produced no audio" % backend)


def synthesize(text, out_path, backend="none", **options):
    """대본 저장 + 오디오 합성. out_path 는 .wav. 오디오가 없으면 None. 실패하면 TTSError."""
    script_path = os.path.splitext(out_path)[0] + ".script.txt"
    os.makedirs(os.path.dirname(os.path.abspath(script_path)), exist_ok=True)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(text)
    if backend in (None, "", "none"):
        return None
    if backend not in ("sapi", "piper", "openai_compat"):
        logger.warning("unknown tts backend %r; script only", backend)
        return None
    chunks = plan_chunks(text, int(options.get("max_chars", 300)))
    if not chunks:
        return None
    work = tempfile.mkdtemp(prefix="briefing-tts-")
    try:
        parts = []
        for n, (speaker, chunk) in enumerate(chunks):
            wav = os.path.join(work, "part%04d.wav" % n)
            _synth_chunk(backend, chunk, speaker, wav, work, options)
            parts.append(wav)
        concat_wavs(parts, out_path, int(options.get("pause_ms", 250)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_path


# ---------------------------------------------------------------- podcast rss

_EP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:-(\d+))?\.wav$")


def write_feed(out_dir, base_url, title="커뮤니티 브리핑", description="매일 아침 내 관심사 브리핑",
               max_episodes=30, feed_name="podcast.xml"):
    """out_dir 의 YYYY-MM-DD[-N].wav 로 팟캐스트 RSS 2.0 (iTunes 태그 포함) 생성. -> 파일 경로."""
    base = base_url.rstrip("/") + "/"
    episodes = []
    for name in os.listdir(out_dir):
        m = _EP_RE.match(name)
        if m:
            day = datetime.strptime(m.group(1), "%Y-%m-%d").replace(hour=6, tzinfo=timezone.utc)
            episodes.append((day + timedelta(minutes=int(m.group(2) or 1) - 1), name))
    episodes.sort(reverse=True)
    ns = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    ET.register_namespace("itunes", ns)
    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")
    for tag, val in (("title", title), ("link", base), ("description", description), ("language", "ko")):
        ET.SubElement(ch, tag).text = val
    ET.SubElement(ch, "{%s}explicit" % ns).text = "false"
    for when, name in episodes[:max_episodes]:
        stem = name[:-4]
        path = os.path.join(out_dir, name)
        desc = ""
        try:
            with open(os.path.join(out_dir, stem + ".json"), encoding="utf-8") as f:
                desc = " ".join(l for l in json.load(f).get("overview", []) if l)
        except (OSError, ValueError):
            pass
        item = ET.SubElement(ch, "item")
        ET.SubElement(item, "title").text = "%s %s" % (title, stem)
        ET.SubElement(item, "description").text = desc or stem
        ET.SubElement(item, "pubDate").text = email.utils.format_datetime(when)
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = name
        url = base + urllib.parse.quote(name)
        try:
            dur = int(wav_duration(path))
        except (wave.Error, OSError, EOFError):
            dur = 0
        ET.SubElement(item, "enclosure", {"url": url, "length": str(os.path.getsize(path)),
                                          "type": "audio/x-wav"})
        ET.SubElement(item, "{%s}duration" % ns).text = str(dur)
    feed_path = os.path.join(out_dir, feed_name)
    ET.ElementTree(rss).write(feed_path, encoding="utf-8", xml_declaration=True)
    return feed_path
