from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

from .config import ROOT

log = logging.getLogger(__name__)

SCRIPT = ROOT / "browser" / "render.mjs"


def available() -> bool:
    return (
        os.getenv("BROWSER", "1") != "0"
        and shutil.which("node") is not None
        and (ROOT / "browser" / "node_modules" / "playwright").is_dir()
    )


def render(url: str, discover: bool = False, timeout_s: int = 180) -> dict:
    cmd = ["node", str(SCRIPT), url, "--timeout", "45000"] + (["--discover"] if discover else [])
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout_s, cwd=SCRIPT.parent)
    if proc.returncode != 0:
        raise RuntimeError(f"navegador falhou: {proc.stderr.decode(errors='replace')[:300]}")
    return json.loads(proc.stdout)
