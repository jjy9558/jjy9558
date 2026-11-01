"""faster-whisper transcription (imported lazily)."""
from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

from .segment import SegConfig, build_cues
from .srt import Cue

INSTALL_HINT = (
    "faster-whisper가 설치되어 있지 않습니다.\n"
    "  pip install faster-whisper\n"
    "NVIDIA GPU(RTX 50xx 등)는 CUDA 12 런타임(cuBLAS, cuDNN 9)이 필요합니다. "
    "자세한 내용은 README.md의 '설치' 항목을 참고하세요."
)


class TranscribeError(RuntimeError):
    pass


def _default_factory(model: str, device: str, compute_type: str) -> Any:
    try:
        from faster_whisper import WhisperModel  # lazy
    except ImportError as e:
        raise TranscribeError(INSTALL_HINT) from e
    return WhisperModel(model, device=device, compute_type=compute_type)


def transcribe_media(
    path: str,
    model: str = "large-v3",
    lang: str = "auto",
    device: str = "cuda",
    compute_type: Optional[str] = None,
    cfg: Optional[SegConfig] = None,
    model_factory: Optional[Callable[[str, str, str], Any]] = None,
) -> Tuple[List[Cue], str]:
    """Return (cues, detected_language). model_factory is injectable for tests."""
    compute_type = compute_type or ("float16" if device == "cuda" else "int8")
    factory = model_factory or _default_factory
    wm = factory(model, device, compute_type)
    segments, info = wm.transcribe(
        path,
        language=None if lang == "auto" else lang,
        word_timestamps=True,
        vad_filter=True,
    )
    segs = list(segments)  # generator -> list
    detected = getattr(info, "language", None) or (lang if lang != "auto" else "und")
    return build_cues(segs, cfg), detected
