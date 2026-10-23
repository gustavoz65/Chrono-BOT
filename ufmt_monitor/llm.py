from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import replace
from datetime import date

from .classify import TITLE_CLOSED_RE, Assessment
from .dates import Window, fold, overall_status
from .verify import Check, compare_windows

log = logging.getLogger(__name__)

CHECK = "heurística×Groq"
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_CHARS = int(os.getenv("GROQ_MAX_CHARS", "14000"))
KEYWORDS_RE = re.compile(r"inscric|vaga|computac|cronograma|prorroga|retifica|periodo|prazo", re.I)

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
    "Se uma informação não estiver no texto, deixe a data vazia, os booleanos como false e as vagas "
    "como -1. Responda apenas com JSON seguindo exatamente estas chaves: "
    + ", ".join(SCHEMA["required"]) + "."
)


def enabled() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def _parse_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def excerpt(body: str, limit: int = MAX_CHARS) -> tuple[str, bool]:
    # Editais longos estouram o limite de tokens por minuto do Groq.
    if len(body) <= limit:
        return body, False
    folded = fold(body)
    spans = [(0, 1500)] + [(max(0, m.start() - 400), m.end() + 400) for m in KEYWORDS_RE.finditer(folded)]
    merged: list[list[int]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    parts, size = [], 0
    for a, b in merged:
        chunk = body[a:b]
        if size + len(chunk) > limit:
            chunk = chunk[:limit - size]
        parts.append(chunk)
        size += len(chunk)
        if size >= limit:
            break
    return "\n[...]\n".join(parts), True


def _valid(data: object) -> bool:
    if not isinstance(data, dict) or set(SCHEMA["required"]) - data.keys():
        return False
    return (data["modalidade"] in SCHEMA["properties"]["modalidade"]["enum"]
            and isinstance(data["inscricao_inicio"], str) and isinstance(data["inscricao_fim"], str)
            and isinstance(data["vagas_ciencia_computacao_cuiaba"], int))


def _ask(client, content: str):
    kwargs = {"model": MODEL, "max_completion_tokens": 2000,
              "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]}
    if MODEL.startswith("openai/gpt-oss"):
        kwargs["reasoning_effort"] = "low"
    import groq
    try:
        return client.chat.completions.create(
            response_format={"type": "json_schema",
                             "json_schema": {"name": "analise_edital", "schema": SCHEMA, "strict": True}},
            **kwargs)
    except groq.BadRequestError as exc:
        log.info("Groq: %s sem json_schema (%s); usando json_object", MODEL, exc)
        return client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)


def _skip(heuristic: Assessment, reason: str) -> Assessment:
    log.warning("Groq: %s; usando heurística", reason)
    return replace(heuristic, checks=[*heuristic.checks, Check(CHECK, None, reason)])


def refine(title: str, body: str, heuristic: Assessment, today: date) -> Assessment:
    if not enabled():
        return replace(heuristic, checks=[*heuristic.checks, Check(CHECK, None, "Groq não configurado")])
    import groq

    text, partial = excerpt(body)
    content = (
        f"Data de hoje: {today.isoformat()}\nTítulo da publicação: {title}\n"
        + ("(Abaixo, só os trechos relevantes do documento.)\n" if partial else "")
        + f"\n<documento>\n{text}\n</documento>"
    )
    try:
        resp = _ask(groq.Groq(), content)
    except groq.RateLimitError:
        return _skip(heuristic, "limite de requisições do Groq")
    except groq.APIStatusError as exc:
        return _skip(heuristic, f"erro HTTP {exc.status_code}")
    except groq.APIConnectionError as exc:
        return _skip(heuristic, f"falha de conexão ({exc})")

    choice = resp.choices[0]
    if choice.finish_reason != "stop":
        return _skip(heuristic, f"resposta incompleta ({choice.finish_reason})")
    try:
        data = json.loads(choice.message.content or "")
    except json.JSONDecodeError:
        return _skip(heuristic, "JSON inválido")
    if not _valid(data):
        return _skip(heuristic, "JSON fora do formato esperado")

    start, end = _parse_date(data["inscricao_inicio"]), _parse_date(data["inscricao_fim"])
    ai_windows = [Window(start, end, "extraído pelo Groq")] if (start or end) else []
    check = compare_windows(CHECK, heuristic.windows, ai_windows)
    windows = heuristic.windows + [w for w in ai_windows if (w.start, w.end) not in
                                   {(h.start, h.end) for h in heuristic.windows}]
    status, window = overall_status(windows, today)
    if TITLE_CLOSED_RE.search(fold(title)):
        status = "encerrada"
    elif status == "indeterminada":
        status = heuristic.status
    vagas = data["vagas_ciencia_computacao_cuiaba"]
    return Assessment(
        modalidade=data["modalidade"] if data["modalidade"] != "outro" else heuristic.modalidade,
        status=status,
        window=window or heuristic.window,
        windows=windows,
        computacao=heuristic.computacao or bool(data["oferece_ciencia_computacao"]),
        computacao_cuiaba=heuristic.computacao_cuiaba or bool(data["ciencia_computacao_cuiaba"]),
        vagas_cc=heuristic.vagas_cc if heuristic.vagas_cc is not None else (vagas if vagas >= 0 else None),
        resumo=str(data["resumo"]),
        fonte_analise=f"heurística + Groq ({resp.model})",
        checks=[*heuristic.checks, check],
    )
