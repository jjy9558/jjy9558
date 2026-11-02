"""설정: 환경변수 + <home>/config.json. 모든 데이터는 로컬 home 아래에 저장."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional


def default_home(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    h = env.get("PAPERBUTLER_HOME")
    return Path(h).expanduser() if h else Path.home() / ".paperbutler"


@dataclass
class Config:
    home: Path
    llm_url: str = ""               # 예: http://localhost:8080/v1  (OpenAI 호환)
    llm_model: str = ""
    llm_api_key: str = ""
    vision_model: str = ""          # 비어 있으면 llm_model 사용
    allow_remote_llm: bool = False  # 기본: 로컬/사설망 주소만 허용
    image_backend: str = "auto"     # auto | vision | tesseract
    ocr_lang: str = "kor+eng"
    dedupe_minutes: int = 10
    default_refund_days: int = 7
    llm_threshold: float = 0.7
    category_rules: Dict[str, List[str]] = field(default_factory=dict)
    pdftotext_cmd: str = "pdftotext"
    tesseract_cmd: str = "tesseract"
    llm_timeout: int = 120

    @property
    def db_path(self) -> Path:
        return self.home / "paperbutler.db"

    @property
    def docs_dir(self) -> Path:
        return self.home / "docs"

    @classmethod
    def load(cls, home: Optional[os.PathLike] = None,
             env: Optional[Mapping[str, str]] = None) -> "Config":
        env = os.environ if env is None else env
        h = Path(home).expanduser() if home else default_home(env)
        cfg = cls(home=h)
        f = h / "config.json"
        if f.is_file():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            for k, v in data.items():
                if k == "home" or not hasattr(cfg, k):
                    continue
                setattr(cfg, k, v)
        mapping = {
            "PAPERBUTLER_LLM_URL": ("llm_url", str),
            "PAPERBUTLER_LLM_MODEL": ("llm_model", str),
            "PAPERBUTLER_LLM_API_KEY": ("llm_api_key", str),
            "PAPERBUTLER_VISION_MODEL": ("vision_model", str),
            "PAPERBUTLER_IMAGE_BACKEND": ("image_backend", str),
            "PAPERBUTLER_OCR_LANG": ("ocr_lang", str),
            "PAPERBUTLER_DEDUPE_MINUTES": ("dedupe_minutes", int),
            "PAPERBUTLER_ALLOW_REMOTE_LLM": ("allow_remote_llm", lambda x: x.lower() in ("1", "true", "yes")),
        }
        for key, (attr, conv) in mapping.items():
            if env.get(key):
                try:
                    setattr(cfg, attr, conv(env[key]))
                except ValueError:
                    pass
        return cfg
