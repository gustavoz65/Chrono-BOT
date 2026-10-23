from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    kind: str = "page"
    critical: bool = False
    verify_tls: bool = True
    item_pattern: str = ""
    min_items: int = 1
    modalidade: str = ""
def load_sources(path: Path | str | None = None) -> list[Source]:
    path = Path(path) if path else ROOT / "sources.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    sources = [Source(**raw) for raw in data.get("source", [])]
    ids = [s.id for s in sources]
    if len(ids) != len(set(ids)):
        raise ValueError("ids duplicados em sources.toml")
    for s in sources:
        if s.kind not in ("listing", "page"):
            raise ValueError(f"{s.id}: kind inválido {s.kind!r}")
    return sources
