from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
# O runner do GitHub roda em UTC; os prazos da UFMT seguem o horário de Cuiabá (UTC-4).
TZ = ZoneInfo("America/Cuiaba")


def today() -> date:
    return datetime.now(TZ).date()


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
        if s.item_pattern:
            try:
                re.compile(s.item_pattern)
            except re.error as exc:
                raise ValueError(f"{s.id}: item_pattern inválido ({exc})") from exc
    return sources
