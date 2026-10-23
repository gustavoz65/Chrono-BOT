from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
_MONTH_ALT = "|".join(MONTHS)

_NUMERIC = r"(?P<nd>\d{1,2})\s*[/.\-]\s*(?P<nm>\d{1,2})(?:\s*[/.\-]\s*(?P<ny>\d{4}|\d{2}))?(?![\d/.\-]*\d)"
_WRITTEN = (
    r"(?P<wd>\d{1,2})\s*(?:º|o)?\s+de\s+(?P<wm>" + _MONTH_ALT + r")"
    r"(?:\s+de\s+(?P<wy>\d{4}))?"
)
_SHARED = (
    r"(?P<sd1>\d{1,2})\s*(?:º|o)?\s*(?:a|ate|e)\s*(?P<sd2>\d{1,2})\s*(?:º|o)?\s+de\s+"
    r"(?P<sm>" + _MONTH_ALT + r")(?:\s+de\s+(?P<sy>\d{4}))?"
)
DATE_RE = re.compile(rf"(?<!\d)(?:{_SHARED}|{_WRITTEN}|{_NUMERIC})", re.I)

INSCRICAO_RE = re.compile(r"inscric(?:ao|oes)", re.I)
NOT_WINDOW_RE = re.compile(
    r"homologa|isencao|resultado|recurso|deferid|indeferid|confirmacao|pagamento|boleto|gru\b",
    re.I,
)


def fold(text: str) -> str:
    out = []
    for ch in text:
        base = unicodedata.normalize("NFKD", ch)
        base = "".join(c for c in base if not unicodedata.combining(c)) or ch
        out.append(base[0].lower() if len(base) >= 1 else ch)
    return "".join(out)


@dataclass(frozen=True)
class Mention:
    start: int
    end: int
    day: int
    month: int
    year: int | None


@dataclass(frozen=True)
class Window:
    start: date | None
    end: date | None
    context: str

    def status(self, today: date) -> str:
        if self.end and self.end < today:
            return "encerrada"
        if self.start and self.start > today:
            return "prevista"
        if (self.start or self.end) and (not self.start or self.start <= today) and (not self.end or today <= self.end):
            return "aberta"
        return "indeterminada"

    def label(self) -> str:
        fmt = lambda d: d.strftime("%d/%m/%Y")  # noqa: E731
        if self.start and self.end:
            return f"{fmt(self.start)} a {fmt(self.end)}"
        if self.end:
            return f"até {fmt(self.end)}"
        if self.start:
            return f"a partir de {fmt(self.start)}"
        return "datas não identificadas"


def _year(raw: str | None) -> int | None:
    if not raw:
        return None
    y = int(raw)
    return y + 2000 if y < 100 else y


def find_mentions(folded: str) -> list[Mention]:
    mentions: list[Mention] = []
    for m in DATE_RE.finditer(folded):
        g = m.groupdict()
        if g["sd1"]:
            month, year = MONTHS[g["sm"]], _year(g["sy"])
            mentions.append(Mention(m.start("sd1"), m.end("sd1"), int(g["sd1"]), month, year))
            mentions.append(Mention(m.start("sd2"), m.end(), int(g["sd2"]), month, year))
        elif g["wd"]:
            mentions.append(Mention(m.start(), m.end(), int(g["wd"]), MONTHS[g["wm"]], _year(g["wy"])))
        else:
            mentions.append(Mention(m.start(), m.end(), int(g["nd"]), int(g["nm"]), _year(g["ny"])))
    return mentions


def _to_dates(mentions: list[Mention], default_year: int | None) -> list[date]:
    out: list[date] = []
    for i, m in enumerate(mentions):
        year = m.year
        if year is None:
            year = next((n.year for n in mentions[i + 1:] if n.year), None) or default_year
        if year is None:
            continue
        try:
            out.append(date(year, m.month, m.day))
        except ValueError:
            continue
    return out


def parse_dates(text: str, default_year: int | None = None) -> list[date]:
    return _to_dates(find_mentions(fold(text)), default_year)


def inscription_windows(text: str, today: date | None = None) -> list[Window]:
    folded = fold(text)
    mentions = find_mentions(folded)
    default_year = today.year if today else None
    windows: list[Window] = []
    seen: set[tuple] = set()
    for kw in INSCRICAO_RE.finditer(folded):
        lead = folded[max(0, kw.start() - 60):kw.start()]
        # Só o trecho imediatamente anterior decide se é outra etapa ("homologação das inscrições").
        if NOT_WINDOW_RE.search(lead[-35:]):
            continue
        stop = min(len(folded), kw.end() + 220)
        nxt = INSCRICAO_RE.search(folded, kw.end(), stop)
        if nxt:
            stop = nxt.start()
        near = [m for m in mentions if kw.end() <= m.start < stop]
        if not near:
            continue
        near_text = folded[kw.end():near[0].start]
        if NOT_WINDOW_RE.search(near_text):
            continue
        dates = _to_dates(near[:2], default_year)
        if not dates:
            continue
        before = folded[kw.end():near[0].start]
        if len(dates) >= 2 and dates[0] <= dates[1]:
            win = Window(dates[0], dates[1], text[kw.start():near[1].end].strip())
        elif re.search(r"\bate\b", before):
            win = Window(None, dates[0], text[kw.start():near[0].end].strip())
        else:
            win = Window(dates[0], None, text[kw.start():near[0].end].strip())
        key = (win.start, win.end)
        if key not in seen:
            seen.add(key)
            windows.append(win)
    return windows


def overall_status(windows: list[Window], today: date) -> tuple[str, Window | None]:
    if not windows:
        return "indeterminada", None
    for wanted in ("aberta", "prevista"):
        hits = [w for w in windows if w.status(today) == wanted]
        if hits:
            return wanted, min(hits, key=lambda w: w.start or w.end or today)
    closed = [w for w in windows if w.status(today) == "encerrada"]
    if closed:
        return "encerrada", max(closed, key=lambda w: w.end or w.start or today)
    return "indeterminada", windows[0]
