from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .dates import Window, fold

MIN_TEXT_AGREEMENT = 0.6
MIN_LISTING_AGREEMENT = 1.0


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool | None
    detail: str = ""

    @property
    def icon(self) -> str:
        return {True: "✔", False: "✖", None: "–"}[self.ok]

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def merge(checks: list[Check]) -> list[Check]:
    by_name: dict[str, list[Check]] = {}
    for c in checks:
        by_name.setdefault(c.name, []).append(c)
    out = []
    for name, group in by_name.items():
        if any(c.ok is False for c in group):
            ok = False
        elif all(c.ok for c in group):
            ok = True
        else:
            ok = None
        details = [c.detail for c in group if c.ok is ok and c.detail]
        out.append(Check(name, ok, "; ".join(dict.fromkeys(details))))
    return out


def confidence(checks: list[Check]) -> str:
    if any(c.ok is False for c in checks):
        return "baixa"
    if checks and all(c.ok for c in checks):
        return "alta"
    return "média"


def summary(checks: list[Check]) -> str:
    return " · ".join(f"{c.name} {c.icon}" for c in merge(checks)) or "nenhuma verificação executada"


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", fold(text))}


def text_agreement(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _window_key(w: Window | None) -> tuple[date | None, date | None] | None:
    return (w.start, w.end) if w else None


def compare_windows(name: str, a: list[Window], b: list[Window]) -> Check:
    ka = {_window_key(w) for w in a}
    kb = {_window_key(w) for w in b}
    if not ka and not kb:
        return Check(name, True, "nenhuma das leituras achou período de inscrição")
    if ka & kb:
        return Check(name, True, "mesmo período de inscrição")
    labels = lambda ws: ", ".join(w.label() for w in ws) or "nada"  # noqa: E731
    return Check(name, False, f"{labels(a)} ≠ {labels(b)}")


def compare_text(name: str, a: str, b: str) -> Check:
    ratio = text_agreement(a, b)
    return Check(name, ratio >= MIN_TEXT_AGREEMENT, f"{ratio:.0%} das palavras em comum")


def compare_listing(http_urls: list[str], browser_urls: list[str], http_cycles: set[str],
                    browser_cycles: set[str]) -> Check:
    name = "HTML×navegador"
    a, b = set(http_urls), set(browser_urls)
    if not a and not b:
        return Check(name, False, "nenhuma das leituras encontrou publicações")
    overlap = len(a & b) / max(len(a), len(b))
    missing_cycles = browser_cycles - http_cycles
    if overlap >= MIN_LISTING_AGREEMENT and not missing_cycles:
        return Check(name, True, f"{len(a & b)} publicações confirmadas")
    detail = f"{len(a)} via HTML, {len(b)} via navegador, {len(a & b)} em comum"
    if missing_cycles:
        detail += f"; ciclos só no navegador: {', '.join(sorted(missing_cycles))}"
    return Check(name, False, detail)
