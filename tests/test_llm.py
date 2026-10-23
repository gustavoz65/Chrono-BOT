import json
from datetime import date
from types import SimpleNamespace

import anthropic

from ufmt_monitor import llm
from ufmt_monitor.classify import assess

HOJE = date(2026, 10, 23)


class FakeMessages:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.kwargs = payload, stop_reason, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            stop_reason=self.stop_reason, model=kwargs["model"],
            content=[SimpleNamespace(type="text", text=json.dumps(self.payload))],
        )


def install(monkeypatch, messages):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "teste")
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    monkeypatch.setattr(anthropic, "Anthropic", lambda: client)


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


def test_claude_complementa_datas_que_a_heuristica_nao_achou(monkeypatch):
    msgs = FakeMessages(PAYLOAD)
    install(monkeypatch, msgs)
    h = assess("Edital", "texto confuso sem datas claras", HOJE)
    assert h.status == "indeterminada"
    r = llm.refine("Edital", "texto confuso", h, HOJE)
    assert r.status == "aberta" and r.computacao_cuiaba and r.vagas_cc == 3 and "Claude" in r.fonte_analise
    assert msgs.kwargs["output_config"]["format"]["type"] == "json_schema"


def test_recusa_volta_para_heuristica(monkeypatch):
    install(monkeypatch, FakeMessages(PAYLOAD, stop_reason="refusal"))
    h = assess("t", "nada", HOJE)
    r = llm.refine("t", "nada", h, HOJE)
    assert r.status == h.status and "refusal" in r.checks[-1].detail


def test_heuristica_e_claude_concordam_confianca_alta(monkeypatch):
    install(monkeypatch, FakeMessages(PAYLOAD))
    h = assess("Edital", "Inscrições de 20/10/2026 a 30/10/2026", HOJE)
    r = llm.refine("Edital", "x", h, HOJE)
    assert r.checks[-1].ok is True and r.confidence == "alta"


def test_divergencia_prioriza_inscricao_aberta_e_baixa_confianca(monkeypatch):
    install(monkeypatch, FakeMessages(PAYLOAD))
    h = assess("Edital", "Inscrições de 01/09/2026 a 05/09/2026", HOJE)
    assert h.status == "encerrada"
    r = llm.refine("Edital", "x", h, HOJE)
    assert r.status == "aberta" and r.confidence == "baixa"
    assert "≠" in r.checks[-1].detail
