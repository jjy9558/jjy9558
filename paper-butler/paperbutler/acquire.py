"""입력 텍스트 획득 (플러그인 방식). 모두 선택 사항이며 실패 시 명확한 한국어 안내를 낸다.

- 텍스트/붙여넣기: 항상 가능 (UTF-8/UTF-16/CP949 자동 판별)
- PDF: `pdftotext` CLI (있을 때)  -- 인자는 리스트로 전달(셸 미사용)
- 이미지: OpenAI 호환 비전 엔드포인트(로컬 VLM) 또는 `tesseract` CLI(kor+eng)
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .config import Config

log = logging.getLogger("paperbutler.acquire")

TEXT_EXT = {".txt", ".text", ".md", ".sms", ".log", ".csv", ""}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
UNSUPPORTED_IMAGE_EXT = {".heic", ".heif"}
PDF_EXT = {".pdf"}
SUPPORTED_EXT = TEXT_EXT | IMAGE_EXT | PDF_EXT


class AcquireError(Exception):
    """사용자에게 그대로 보여줄 수 있는 한국어 메시지를 담는다."""


@dataclass
class Acquired:
    text: str
    method: str  # text | pdftotext | vision | tesseract


def decode_bytes(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    for enc in ("utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def read_text_file(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return decode_bytes(f.read())
    except OSError as e:
        raise AcquireError("파일을 읽을 수 없습니다: %s (%s)" % (os.path.basename(path), e.strerror)) from e


def pdf_text(path: str, cfg: Config, which: Callable[[str], Optional[str]] = shutil.which,
             runner: Callable[..., Any] = subprocess.run) -> str:
    exe = which(cfg.pdftotext_cmd)
    if not exe:
        raise AcquireError(
            "PDF 글자를 읽으려면 `pdftotext` 가 필요합니다 (poppler-utils: `sudo apt install poppler-utils` / "
            "`brew install poppler`). 설치하지 않고 쓰려면 PDF 내용을 복사해 `add -` 로 붙여넣으세요.")
    args = [exe, "-layout", "-enc", "UTF-8", os.path.abspath(path), "-"]
    try:
        r = runner(args, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as e:
        raise AcquireError("pdftotext 가 60초 안에 끝나지 않았습니다.") from e
    except OSError as e:
        raise AcquireError("pdftotext 실행 실패: %s" % e.strerror) from e
    if r.returncode != 0:
        raise AcquireError("pdftotext 오류: PDF 가 손상되었거나 암호가 걸려 있을 수 있습니다.")
    out = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else str(r.stdout)
    if len(out.strip()) < 5:
        raise AcquireError(
            "PDF 에서 글자를 찾지 못했습니다(스캔본 PDF 일 수 있음). 페이지를 사진(JPG/PNG)으로 저장해 "
            "이미지로 추가하거나, 내용을 붙여넣어 주세요.")
    return out


def tesseract_text(path: str, cfg: Config, which: Callable[[str], Optional[str]] = shutil.which,
                   runner: Callable[..., Any] = subprocess.run) -> str:
    exe = which(cfg.tesseract_cmd)
    if not exe:
        raise AcquireError(
            "tesseract 가 설치되어 있지 않습니다 (`sudo apt install tesseract-ocr tesseract-ocr-kor`).")
    args = [exe, os.path.abspath(path), "stdout", "-l", cfg.ocr_lang]
    try:
        r = runner(args, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired as e:
        raise AcquireError("tesseract 가 120초 안에 끝나지 않았습니다.") from e
    except OSError as e:
        raise AcquireError("tesseract 실행 실패: %s" % e.strerror) from e
    err = r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else str(r.stderr or "")
    if r.returncode != 0:
        if "Failed loading language" in err or "load language" in err.lower():
            raise AcquireError("tesseract 한국어 데이터가 없습니다 (`sudo apt install tesseract-ocr-kor`).")
        raise AcquireError("tesseract 오류: 이미지를 읽지 못했습니다.")
    out = r.stdout.decode("utf-8", "replace") if isinstance(r.stdout, bytes) else str(r.stdout)
    if len(out.strip()) < 5:
        raise AcquireError("이미지에서 글자를 찾지 못했습니다. 밝은 곳에서 서류를 평평하게 펴고 다시 찍어 주세요.")
    return out


NO_IMAGE_BACKEND_MSG = (
    "이미지에서 글자를 읽으려면 둘 중 하나가 필요합니다: "
    "(1) 로컬 VLM 서버 설정(PAPERBUTLER_LLM_URL / PAPERBUTLER_LLM_MODEL, OpenAI 호환) "
    "(2) tesseract 설치(`sudo apt install tesseract-ocr tesseract-ocr-kor`). "
    "당장은 사진 속 글자를 텍스트로 붙여넣어 `add -` 로 추가할 수 있습니다.")


def image_text(path: str, cfg: Config, llm: Any = None,
               which: Callable[[str], Optional[str]] = shutil.which,
               runner: Callable[..., Any] = subprocess.run) -> Acquired:
    backend = (cfg.image_backend or "auto").lower()
    errors = []
    if backend in ("auto", "vision") and llm is not None:
        try:
            text = llm.transcribe_image(os.path.abspath(path))
            if text and len(text.strip()) >= 5:
                return Acquired(text, "vision")
            errors.append("비전 모델이 글자를 돌려주지 않았습니다.")
        except Exception as e:  # LLMError 등
            errors.append("비전 모델 사용 실패: %s" % getattr(e, "args", [type(e).__name__])[0])
            log.warning("vision 전사 실패: %s", type(e).__name__)
    if backend in ("auto", "tesseract"):
        if which(cfg.tesseract_cmd):
            return Acquired(tesseract_text(path, cfg, which, runner), "tesseract")
        errors.append("tesseract 없음")
    if backend == "vision" and llm is None:
        raise AcquireError("image_backend=vision 인데 LLM 설정이 없습니다. " + NO_IMAGE_BACKEND_MSG)
    if errors and llm is not None:
        raise AcquireError("이미지에서 글자를 읽지 못했습니다 (%s). %s" % ("; ".join(errors), NO_IMAGE_BACKEND_MSG))
    raise AcquireError(NO_IMAGE_BACKEND_MSG)


def acquire_text(path: str, cfg: Config, llm: Any = None,
                 which: Callable[[str], Optional[str]] = shutil.which,
                 runner: Callable[..., Any] = subprocess.run) -> Acquired:
    ext = os.path.splitext(path)[1].lower()
    if not os.path.isfile(path):
        raise AcquireError("파일이 없습니다: %s" % os.path.basename(path))
    if ext in PDF_EXT:
        return Acquired(pdf_text(path, cfg, which, runner), "pdftotext")
    if ext in IMAGE_EXT:
        return image_text(path, cfg, llm, which, runner)
    if ext in UNSUPPORTED_IMAGE_EXT:
        raise AcquireError(
            "HEIC/HEIF 사진은 지원하지 않습니다. 카메라 앱 설정에서 JPEG 로 저장하거나 JPG 로 변환해 주세요.")
    if ext in TEXT_EXT:
        text = read_text_file(path)
        if not text.strip():
            raise AcquireError("파일이 비어 있습니다: %s" % os.path.basename(path))
        return Acquired(text, "text")
    raise AcquireError("지원하지 않는 파일 형식입니다: %s (지원: txt, pdf, jpg/png/webp)" % ext)
