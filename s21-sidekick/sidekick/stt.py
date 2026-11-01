"""선택 사항: PC에서 faster-whisper로 음성 인식. 기본 경로는 폰 브라우저의 Web Speech API."""
import os
import tempfile
import threading

_model = None
_lock = threading.Lock()


class STTUnavailable(RuntimeError):
    pass


def _load(cfg):
    global _model
    with _lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel  # 지연 import
            except ImportError as e:
                raise STTUnavailable(
                    "PC 음성 인식을 쓰려면 `pip install faster-whisper`가 필요합니다. "
                    "설치 없이 쓰려면 폰 페이지에서 '폰 음성 인식(기본)'을 선택하세요.") from e
            _model = WhisperModel(cfg.get("model", "small"), device=cfg.get("device", "auto"),
                                  compute_type=cfg.get("compute_type", "default"))
        return _model


def transcribe(audio_bytes, cfg, suffix=".webm"):
    model = _load(cfg)
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio_bytes)
        segments, _ = model.transcribe(path, language=cfg.get("language", "ko"), vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()
    finally:
        os.unlink(path)  # 녹음은 남기지 않는다
