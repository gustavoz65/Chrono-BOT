from __future__ import annotations

import json
import logging
import os
from datetime import date

from .classify import Assessment
from .dates import Window

log = logging.getLogger(__name__)

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
MAX_CHARS = 200_000

SCHEMA = {
    "type": "object",
    "properties": {
        "modalidade": {"type": "string", "enum": [
            "admissao_graduados", "transferencia_facultativa", "vagas_remanescentes", "segundo_ciclo", "ead", "outro"]},
        "inscricao_inicio": {"type": "string", "description": "AAAA-MM-DD ou vazio se não houver"},
        "inscricao_fim": {"type": "string", "description": "AAAA-MM-DD ou vazio se não houver"},
        "oferece_ciencia_computacao": {"type": "boolean"},
        "ciencia_computacao_cuiaba": {"type": "boolean"},
        "vagas_ciencia_computacao_cuiaba": {"type": "integer", "description": "-1 se não informado"},
        "resumo": {"type": "string", "description": "Até 2 frases, em português"},
    },
    "required": ["modalidade", "inscricao_inicio", "inscricao_fim", "oferece_ciencia_computacao",
                 "ciencia_computacao_cuiaba", "vagas_ciencia_computacao_cuiaba", "resumo"],
    "additionalProperties": False,
}

SYSTEM = (
    "Você analisa publicações oficiais da UFMT (editais, retificações, cronogramas) para alguém que "
    "quer ingressar em Ciência da Computação no campus Cuiabá pela Admissão de Graduados (portadores "
    "de diploma). Extraia o período de INSCRIÇÃO vigente — se houver retificação ou prorrogação, use "
    "as datas mais recentes. Ignore datas de homologação, resultado, recurso e matrícula. "
    "\"Segundo ciclo\" e cursos EaD são modalidades diferentes de Admissão de Graduados. "
    "Transferência Facultativa e Vagas Remanescentes também são processos diferentes, mas as vagas "
    "que sobram deles vão para a Admissão de Graduados: nesses documentos, informe quantas vagas de "
    "Ciência da Computação em Cuiabá são ofertadas ou sobraram (resultados, listas de vagas). "
    "Se uma informação não estiver no texto, deixe a data vazia e os booleanos como false."
)


def enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def refine(title: str, body: str, heuristic: Assessment, today: date) -> Assessment:
    if not enabled():
        return heuristic
    import anthropic

    truncated = len(body) > MAX_CHARS
    content = (
        f"Data de hoje: {today.isoformat()}\nTítulo da publicação: {title}\n"
        + ("(Texto truncado por tamanho.)\n" if truncated else "")
        + f"\n<documento>\n{body[:MAX_CHARS]}\n</documento>"
    )
    try:
        client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.RateLimitError as exc:
        log.warning("Claude: limite de requisições (%s); usando heurística", exc)
        return heuristic
    except anthropic.APIStatusError as exc:
        log.warning("Claude: erro HTTP %s (%s); usando heurística", exc.status_code, exc)
        return heuristic
    except anthropic.APIConnectionError as exc:
        log.warning("Claude: falha de conexão (%s); usando heurística", exc)
        return heuristic

    if resp.stop_reason in ("refusal", "max_tokens"):
        log.warning("Claude: resposta incompleta (%s); usando heurística", resp.stop_reason)
        return heuristic
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("Claude: JSON inválido; usando heurística")
        return heuristic

    start, end = _parse_date(data["inscricao_inicio"]), _parse_date(data["inscricao_fim"])
    window = heuristic.window
    windows = list(heuristic.windows)
    status = heuristic.status
    if (start or end) and heuristic.status in ("indeterminada", "prevista", "aberta"):
        window = Window(start, end, "extraído pelo Claude")
        windows.append(window)
        status = window.status(today)
    return Assessment(
        modalidade=data["modalidade"],
        status=status,
        window=window,
        windows=windows,
        computacao=heuristic.computacao or data["oferece_ciencia_computacao"],
        computacao_cuiaba=heuristic.computacao_cuiaba or data["ciencia_computacao_cuiaba"],
        vagas_cc=heuristic.vagas_cc if heuristic.vagas_cc is not None else (
            data["vagas_ciencia_computacao_cuiaba"] if data["vagas_ciencia_computacao_cuiaba"] >= 0 else None),
        resumo=data["resumo"],
        fonte_analise=f"heurística + Claude ({resp.model})",
    )
