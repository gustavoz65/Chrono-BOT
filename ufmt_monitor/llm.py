from __future__ import annotations

import logging
import os
import sys
from dataclasses import replace
from datetime import date

from . import workers
from .classify import TITLE_CLOSED_RE, Assessment
from .config import ROOT
from .dates import Window, fold, overall_status
from .gen.ufmt.v1 import worker_pb2 as pb
from .verify import Check, compare_windows

log = logging.getLogger(__name__)

CHECK = "heurística×Groq"
TIMEOUT = int(os.getenv("AI_TIMEOUT_SECONDS", "240"))


def enabled() -> bool:
    return bool(os.getenv("GROQ_API_KEY")) and os.access(workers.AI_BIN, os.X_OK)


def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def analyze(title: str, body: str, today: date, url: str = "") -> pb.AnalyzeResponse:
    request = pb.Request(analyze=pb.AnalyzeRequest(title=title, url=url, body=body, today=today.isoformat()))
    # O agente Go chama de volta o worker Python: precisa do mesmo interpretador e da raiz do projeto.
    env = {**os.environ, "PYTHON": sys.executable, "UFMT_ROOT": str(ROOT), "AI_TIMEOUT": f"{TIMEOUT - 10}s"}
    response = workers.call("ai", [str(workers.AI_BIN), "--worker"], request, timeout=TIMEOUT, cwd=ROOT, env=env)
    return response.analyze


def _skip(heuristic: Assessment, reason: str) -> Assessment:
    log.warning("IA: %s; usando heurística", reason)
    return replace(heuristic, checks=[*heuristic.checks, Check(CHECK, None, reason)])


def refine(title: str, body: str, heuristic: Assessment, today: date, url: str = "") -> Assessment:
    if not os.getenv("GROQ_API_KEY"):
        return replace(heuristic, checks=[*heuristic.checks, Check(CHECK, None, "Groq não configurado")])
    if not os.access(workers.AI_BIN, os.X_OK):
        return _skip(heuristic, "agente de IA não compilado (rode `task build` ou `make build`)")
    try:
        result = analyze(title, body, today, url)
    except workers.WorkerError as exc:
        return _skip(heuristic, str(exc)[:200])

    a = result.analysis
    start, end = _parse_date(a.inscricao_inicio), _parse_date(a.inscricao_fim)
    ai_windows = [Window(start, end, a.evidencia or "extraído pelo Groq")] if (start or end) else []
    check = compare_windows(CHECK, heuristic.windows, ai_windows)
    windows = heuristic.windows + [w for w in ai_windows if (w.start, w.end) not in
                                   {(h.start, h.end) for h in heuristic.windows}]
    status, window = overall_status(windows, today)
    if TITLE_CLOSED_RE.search(fold(title)):
        status = "encerrada"
    elif status == "indeterminada":
        status = heuristic.status
    tools = f"; tools: {', '.join(result.tools_used)}" if result.tools_used else ""
    vagas = a.vagas_ciencia_computacao_cuiaba
    return Assessment(
        modalidade=a.modalidade if a.modalidade and a.modalidade != "outro" else heuristic.modalidade,
        status=status,
        window=window or heuristic.window,
        windows=windows,
        computacao=heuristic.computacao or a.oferece_ciencia_computacao,
        computacao_cuiaba=heuristic.computacao_cuiaba or a.ciencia_computacao_cuiaba,
        vagas_cc=heuristic.vagas_cc if heuristic.vagas_cc is not None else (vagas if vagas >= 0 else None),
        resumo=a.resumo,
        fonte_analise=f"heurística + Groq ({result.model}{tools})",
        checks=[*heuristic.checks, check],
    )
