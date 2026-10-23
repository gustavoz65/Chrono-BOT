from __future__ import annotations

import os
import shutil

from . import workers
from .config import ROOT
from .gen.ufmt.v1 import worker_pb2 as pb

CLI = ROOT / "browser" / "src" / "cli.js"


def available() -> bool:
    return (
        os.getenv("BROWSER", "1") != "0"
        and shutil.which("node") is not None
        and (ROOT / "browser" / "node_modules" / "playwright").is_dir()
    )


def render(url: str, discover: bool = False, timeout_s: int = 180) -> dict:
    request = pb.Request(render=pb.RenderRequest(urls=[url], discover_filters=discover, timeout_ms=45000))
    response = workers.call("browser", ["node", str(CLI), "--worker"], request, timeout=timeout_s, cwd=CLI.parent)
    page = response.render.pages[0]
    if page.error:
        raise workers.WorkerError("browser", page.error)
    return {
        "url": page.final_url or url,
        "html": page.html,
        "options": [{"select": o.select, "value": o.value, "label": o.label, "url": o.url} for o in page.options],
    }
