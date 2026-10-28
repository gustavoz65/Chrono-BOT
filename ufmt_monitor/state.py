from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import ROOT

DEFAULT_PATH = ROOT / "state" / "state.json"


def empty() -> dict[str, Any]:
    return {
        "version": 2,
        "sources": {},
        "publications": {},
        "last_heartbeat": None,
    }


def load(path: Path | str = DEFAULT_PATH) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return empty()
    data = json.loads(path.read_text(encoding="utf-8"))
    base = empty()
    base.update(data)
    return base


def save(state: dict[str, Any], path: Path | str = DEFAULT_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Grava num temporário e troca: um processo morto no meio não deixa JSON truncado.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
