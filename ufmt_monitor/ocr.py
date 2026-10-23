from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .config import ROOT

log = logging.getLogger(__name__)

OCR_BIN = Path(os.getenv("OCR_BIN", ROOT / "ocr" / "bin" / "ocr"))
MAX_OCR_PAGES = int(os.getenv("OCR_MAX_PAGES", "15"))
MIN_CHARS_PER_PAGE = 80


def available() -> bool:
    return OCR_BIN.is_file() and os.access(OCR_BIN, os.X_OK) and shutil.which("pdftoppm") is not None


def needs_ocr(text: str, pages: int) -> bool:
    return len(text.strip()) < MIN_CHARS_PER_PAGE * max(1, min(pages, MAX_OCR_PAGES))


def ocr_pdf(data: bytes, lang: str = "por", timeout: int = 300) -> str:
    if not available():
        log.info("OCR indisponível (compile ocr/ com `make` e instale poppler-utils)")
        return ""
    with tempfile.TemporaryDirectory(prefix="ufmt-ocr-") as tmp:
        pdf = Path(tmp) / "doc.pdf"
        pdf.write_bytes(data)
        subprocess.run(
            ["pdftoppm", "-r", "200", "-gray", "-png", "-l", str(MAX_OCR_PAGES), str(pdf), str(Path(tmp) / "p")],
            check=True, timeout=timeout, capture_output=True,
        )
        images = sorted(str(p) for p in Path(tmp).glob("p*.png"))
        if not images:
            return ""
        proc = subprocess.run([str(OCR_BIN), "-l", lang, *images], timeout=timeout, capture_output=True)
        if proc.returncode not in (0, 3):  # 3 = alguma página falhou, mas há texto das demais
            log.warning("OCR falhou (código %s): %s", proc.returncode, proc.stderr.decode(errors="replace")[:300])
            return ""
        return proc.stdout.decode("utf-8", errors="replace")
