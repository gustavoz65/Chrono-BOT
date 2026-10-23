from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .dates import Window, fold, inscription_windows, overall_status
from .verify import Check, confidence

GRADUADO_RE = re.compile(
    r"admissao\s+de\s+graduad|portador(?:es)?\s+de\s+diploma|ingresso\s+de\s+graduad"
    r"|segunda\s+graduacao|diplomad|graduados?\b.{0,40}vagas|vagas.{0,40}graduados?\b",
    re.I,
)
TRANSFERENCIA_RE = re.compile(r"transferencia\s+facultativa", re.I)
REMANESCENTES_RE = re.compile(r"vagas\s+remanescentes", re.I)
SEGUNDO_CICLO_RE = re.compile(r"segundo\s+ciclo|2o\s+ciclo", re.I)
EAD_RE = re.compile(r"\bead\b|a\s+distancia|uab\b", re.I)
COMPUTACAO_RE = re.compile(r"ciencia\s+da\s+computacao|ciencias\s+da\s+computacao", re.I)
CUIABA_RE = re.compile(r"cuiaba", re.I)
TITLE_CLOSED_RE = re.compile(r"encerrad", re.I)
TITLE_OPEN_RE = re.compile(r"inscricoes\s+abertas|abertura\s+(?:das\s+)?inscric", re.I)
IMPORTANT_RE = re.compile(r"edital|inscric|abertura|processo\s+seletivo|vagas|retifica|prorroga|cronograma", re.I)
ROUTINE_RE = re.compile(
    r"resultado|matricula|convoca|recurso|deferid|chamada|homologa|lista|relacao|encerrad|classifica", re.I)
GENERIC_RE = re.compile(r"edital|processo\s+seletivo|inscric|vagas|cronograma|retificac|prorroga", re.I)

PRECURSORES = ("transferencia_facultativa", "vagas_remanescentes")
MODALIDADE_LABEL = {
    "admissao_graduados": "Admissão de Graduados",
    "transferencia_facultativa": "Transferência Facultativa (sobras vão para Admissão de Graduados)",
    "vagas_remanescentes": "Vagas Remanescentes (sobras vão para Admissão de Graduados)",
    "segundo_ciclo": "Segundo ciclo (outra modalidade)",
    "ead": "Ingresso EaD (outra modalidade)",
    "outro": "Outro processo",
}
STATUS_LABEL = {
    "aberta": "🟢 INSCRIÇÕES ABERTAS",
    "prevista": "🟡 Inscrições previstas",
    "encerrada": "🔴 Inscrições encerradas",
    "indeterminada": "⚪ Status não identificado (conferir edital)",
}


def is_graduado(text: str) -> bool:
    return bool(GRADUADO_RE.search(fold(text)))


def is_generic_admissions(text: str) -> bool:
    return bool(GENERIC_RE.search(fold(text)))


def is_important(title: str, status: str) -> bool:
    if status in ("aberta", "prevista"):
        return True
    t = fold(title)
    return bool(IMPORTANT_RE.search(t)) and not ROUTINE_RE.search(t)


def process_kind(text: str) -> str | None:
    t = fold(text)
    if SEGUNDO_CICLO_RE.search(t):
        return None
    if GRADUADO_RE.search(t) or "graduad" in t:
        return "admissao_graduados"
    if TRANSFERENCIA_RE.search(t):
        return "transferencia_facultativa"
    if REMANESCENTES_RE.search(t):
        return "vagas_remanescentes"
    return None


def modalidade(title: str, body: str = "") -> str:
    for text in (title, body[:3000]):
        t = fold(text)
        if SEGUNDO_CICLO_RE.search(t):
            return "segundo_ciclo"
        kind = process_kind(text)
        if kind:
            return kind
        if EAD_RE.search(t):
            return "ead"
    return "outro"


VAGAS_RE = re.compile(r"(\d{1,3})\s*\(?[a-z ]*\)?\s*vagas?\b|vagas?\s*[:\-]?\s*(\d{1,3})\b", re.I)


def vagas_computacao(text: str) -> int | None:
    t = fold(text)
    for m in COMPUTACAO_RE.finditer(t):
        window = t[m.end():m.end() + 160]
        nxt = re.search(r"[.;]\s|\b(?:engenharia|licenciatura|bacharelado em|medicina|direito)\b", window)
        window = window[:nxt.start()] if nxt else window
        if not CUIABA_RE.search(t[max(0, m.start() - 200):m.end() + 160]):
            continue
        v = VAGAS_RE.search(window)
        if v:
            return int(v.group(1) or v.group(2))
    return None


@dataclass
class Assessment:
    modalidade: str
    status: str
    window: Window | None
    windows: list[Window] = field(default_factory=list)
    computacao: bool = False
    computacao_cuiaba: bool = False
    vagas_cc: int | None = None
    resumo: str = ""
    fonte_analise: str = "heurística"
    checks: list[Check] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        return confidence(self.checks)

    @property
    def status_label(self) -> str:
        return STATUS_LABEL[self.status]

    @property
    def modalidade_label(self) -> str:
        return MODALIDADE_LABEL[self.modalidade]


def assess(title: str, body: str, today: date) -> Assessment:
    windows = inscription_windows(f"{title}. {body}", today)
    status, main = overall_status(windows, today)
    ft = fold(title)
    if TITLE_CLOSED_RE.search(ft):
        status = "encerrada"
    elif status == "indeterminada" and TITLE_OPEN_RE.search(ft):
        status = "aberta"
    t = fold(body)
    comp = bool(COMPUTACAO_RE.search(t))
    comp_cba = any(CUIABA_RE.search(t[max(0, m.start() - 300):m.end() + 300]) for m in COMPUTACAO_RE.finditer(t))
    return Assessment(modalidade(title, body), status, main, windows, comp, comp_cba, vagas_computacao(body))
