import json
from datetime import date
from types import SimpleNamespace

import groq

from ufmt_monitor import llm
from ufmt_monitor.classify import assess

HOJE = date(2026, 10, 23)


class FakeCompletions:
    def __init__(self, payload, finish_reason="stop", reject_schema=False):
        self.payload, self.finish_reason, self.reject_schema = payload, finish_reason, reject_schema
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject_schema and kwargs["response_format"]["type"] == "json_schema":
            raise groq.BadRequestError("json_schema não suportado", response=SimpleNamespace(
                status_code=400, headers={}, request=None), body=None)
        message = SimpleNamespace(content=json.dumps(self.payload))
        return SimpleNamespace(model=kwargs["model"],
                               choices=[SimpleNamespace(finish_reason=self.finish_reason, message=message)])


def install(monkeypatch, completions):
    monkeypatch.setenv("GROQ_API_KEY", "teste")
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(groq, "Groq", lambda: client)


PAYLOAD = {
    "modalidade": "admissao_graduados", "inscricao_inicio": "2026-10-20", "inscricao_fim": "2026-10-30",
    "oferece_ciencia_computacao": True, "ciencia_computacao_cuiaba": True, "vagas_ciencia_computacao_cuiaba": 3,
    "resumo": "Edital aberto.",
}


def test_sem_chave_usa_so_heuristica_e_registra_que_nao_verificou():
    h = assess("t", "Inscrições até 01/01/2026", HOJE)
    r = llm.refine("t", "x", h, HOJE)
    assert (r.status, r.window) == (h.status, h.window)
    assert r.checks[-1].ok is None and r.confidence == "média"


def test_groq_complementa_datas_que_a_heuristica_nao_achou(monkeypatch):
    fake = FakeCompletions(PAYLOAD)
    install(monkeypatch, fake)
    h = assess("Edital", "texto confuso sem datas claras", HOJE)
    assert h.status == "indeterminada"
    r = llm.refine("Edital", "texto confuso", h, HOJE)
    assert r.status == "aberta" and r.computacao_cuiaba and r.vagas_cc == 3 and "Groq" in r.fonte_analise
    fmt = fake.calls[0]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True


def test_modelo_sem_json_schema_cai_para_json_object(monkeypatch):
    fake = FakeCompletions(PAYLOAD, reject_schema=True)
    install(monkeypatch, fake)
    r = llm.refine("Edital", "x", assess("Edital", "x", HOJE), HOJE)
    assert [c["response_format"]["type"] for c in fake.calls] == ["json_schema", "json_object"]
    assert r.status == "aberta"


def test_resposta_cortada_volta_para_heuristica(monkeypatch):
    install(monkeypatch, FakeCompletions(PAYLOAD, finish_reason="length"))
    h = assess("t", "nada", HOJE)
    r = llm.refine("t", "nada", h, HOJE)
    assert r.status == h.status and "length" in r.checks[-1].detail


def test_json_fora_do_formato_e_rejeitado(monkeypatch):
    install(monkeypatch, FakeCompletions({"modalidade": "qualquer"}))
    h = assess("t", "nada", HOJE)
    assert "formato" in llm.refine("t", "nada", h, HOJE).checks[-1].detail


def test_heuristica_e_groq_concordam_confianca_alta(monkeypatch):
    install(monkeypatch, FakeCompletions(PAYLOAD))
    h = assess("Edital", "Inscrições de 20/10/2026 a 30/10/2026", HOJE)
    r = llm.refine("Edital", "x", h, HOJE)
    assert r.checks[-1].ok is True and r.confidence == "alta"


def test_divergencia_prioriza_inscricao_aberta_e_baixa_confianca(monkeypatch):
    install(monkeypatch, FakeCompletions(PAYLOAD))
    h = assess("Edital", "Inscrições de 01/09/2026 a 05/09/2026", HOJE)
    assert h.status == "encerrada"
    r = llm.refine("Edital", "x", h, HOJE)
    assert r.status == "aberta" and r.confidence == "baixa"
    assert "≠" in r.checks[-1].detail


def test_edital_longo_vai_em_trechos_relevantes():
    body = "Introdução. " + "texto irrelevante " * 3000 + "Período de inscrições: 10/11/2026 a 16/11/2026. " \
        + "outro texto " * 3000
    text, partial = llm.excerpt(body, limit=4000)
    assert partial and len(text) <= 4000 + 200
    assert "10/11/2026 a 16/11/2026" in text and text.startswith("Introdução")
