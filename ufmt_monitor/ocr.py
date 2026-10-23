from __future__ import annotations

import logging
import os

from . import workers
from .gen.ufmt.v1 import worker_pb2 as pb

log = logging.getLogger(__name__)

MAX_OCR_PAGES = int(os.getenv("OCR_MAX_PAGES", "15"))
MIN_CHARS_PER_PAGE = 80
VERIFY_PAGES = int(os.getenv("OCR_VERIFY_PAGES", "3"))


def available() -> bool:
    return workers.OCR_BIN.is_file() and os.access(workers.OCR_BIN, os.X_OK)


def needs_ocr(text: str, pages: int) -> bool:
    return len(text.strip()) < MIN_CHARS_PER_PAGE * max(1, min(pages, MAX_OCR_PAGES))


def ocr_pages(data: bytes, max_pages: int = MAX_OCR_PAGES, lang: str = "por") -> list[pb.OcrPage]:
    request = pb.Request(ocr=pb.OcrRequest(pdf=data, lang=lang, first_page=1, last_page=max_pages))
    return list(workers.call("ocr", [str(workers.OCR_BIN), "--worker"], request, timeout=300).ocr.pages)


def ocr_pdf(data: bytes, max_pages: int = MAX_OCR_PAGES, lang: str = "por") -> str:
    if not available():
        log.info("OCR indisponível (rode `make -C ocr`)")
        return ""
    try:
        return "\n".join(p.text for p in ocr_pages(data, max_pages, lang))
    except workers.WorkerError as exc:
        log.warning("OCR falhou: %s", exc)
        return ""
