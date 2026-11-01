"""Korean voice-podcast stub (roadmap).

Interface for future backends:
    synthesize(text, out_path, backend="none", **options) -> str | None
        Render `text` (Korean) to an audio file at `out_path` and return the path,
        or None when no audio was produced. Must never raise for "none".

Planned backends: a local Korean TTS (e.g. MeloTTS / Coqui XTTS / Piper with a Korean voice)
running on the GPU PC, called over HTTP like the LLM. For now the "none" backend writes the
podcast script next to `out_path` so it can be read aloud or fed to any TTS manually.
"""
import os


def build_script(briefing):
    """Turn the briefing JSON (see render.to_json) into a spoken-style Korean script."""
    lines = ["안녕하세요, %s 커뮤니티 브리핑입니다." % briefing.get("date", "")]
    lines += [l for l in briefing.get("overview", []) if l]
    for cluster in briefing.get("clusters", []):
        lines.append("다음은 %s 소식입니다." % cluster["topic"])
        for item in cluster["items"]:
            lines.append("%s. %s" % (item["title"], item.get("summary") or ""))
    lines.append("오늘 브리핑은 여기까지입니다.")
    return "\n".join(lines)


def synthesize(text, out_path, backend="none", **options):
    script_path = os.path.splitext(out_path)[0] + ".script.txt"
    os.makedirs(os.path.dirname(os.path.abspath(script_path)), exist_ok=True)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(text)
    if backend in (None, "", "none"):
        return None
    # TODO: implement real backends here; unknown backends are a no-op for now.
    return None
