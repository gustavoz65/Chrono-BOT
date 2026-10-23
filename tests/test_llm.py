import json
import os
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ufmt_monitor import llm, workers
from ufmt_monitor.classify import assess
from ufmt_monitor.gen.ufmt.v1 import worker_pb2 as pb

HOJE = date(2026, 10, 23)


def fake_ai(monkeypatch, analysis=None, error=None):
    calls = []

    def analyze(title, body, today, url=""):
        calls.append((title, url))
        if error:
            raise error
        return pb.AnalyzeResponse(analysis=analysis, model="groq/openai/gpt-oss-120b",
                                  tools_used=["buscar_no_documento", "extrair_regras"])

    monkeypatch.setenv("GROQ_API_KEY", "teste")
    monkeypatch.setattr(llm.os, "access", lambda *_: True)
    monkeypatch.setattr(llm, "analyze", analyze)
    return calls


ABERTO = pb.Analysis(modalidade="admissao_graduados", inscricao_inicio="2026-10-20", inscricao_fim="2026-10-30",
                     status="aberta", oferece_ciencia_computacao=True, ciencia_computacao_cuiaba=True,
                     vagas_ciencia_computacao_cuiaba=3, resumo="Edital aberto.", evidencia="de 20 a 30/10/2026")


def test_sem_chave_usa_so_heuristica_e_registra_que_nao_verificou(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    h = assess("t", "Inscrições até 01/01/2026", HOJE)
    r = llm.refine("t", "x", h, HOJE)
    assert (r.status, r.window) == (h.status, h.window)
    assert r.checks[-1].ok is None and r.confidence == "média"


def test_groq_complementa_datas_que_a_heuristica_nao_achou(monkeypatch):
    calls = fake_ai(monkeypatch, ABERTO)
    h = assess("Edital", "texto confuso sem datas claras", HOJE)
    r = llm.refine("Edital", "texto confuso", h, HOJE, "https://www.ufmt.br/x")
    assert r.status == "aberta" and r.computacao_cuiaba and r.vagas_cc == 3
    assert "tools: buscar_no_documento, extrair_regras" in r.fonte_analise
    assert calls == [("Edital", "https://www.ufmt.br/x")]


def test_falha_do_agente_volta_para_heuristica(monkeypatch):
    fake_ai(monkeypatch, error=workers.WorkerError("ai", "limite de requisições", pb.Error.CODE_INTERNAL))
    h = assess("t", "nada", HOJE)
    r = llm.refine("t", "nada", h, HOJE)
    assert r.status == h.status and "limite" in r.checks[-1].detail and r.checks[-1].ok is None


def test_heuristica_e_groq_concordam_confianca_alta(monkeypatch):
    fake_ai(monkeypatch, ABERTO)
    h = assess("Edital", "Inscrições de 20/10/2026 a 30/10/2026", HOJE)
    r = llm.refine("Edital", "x", h, HOJE)
    assert r.checks[-1].ok is True and r.confidence == "alta"


def test_divergencia_prioriza_inscricao_aberta_e_baixa_confianca(monkeypatch):
    fake_ai(monkeypatch, ABERTO)
    h = assess("Edital", "Inscrições de 01/09/2026 a 05/09/2026", HOJE)
    assert h.status == "encerrada"
    r = llm.refine("Edital", "x", h, HOJE)
    assert r.status == "aberta" and r.confidence == "baixa"
    assert "≠" in r.checks[-1].detail


class FakeGroq(BaseHTTPRequestHandler):
    bodies: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeGroq.bodies.append(body)
        if len(FakeGroq.bodies) == 1:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function", "function": {
                    "name": "extrair_regras",
                    "arguments": json.dumps({"texto": "Período de inscrições: 10/11/2026 a 16/11/2026"})}}]}
            finish = "tool_calls"
        else:
            tool_msg = next(m for m in body["messages"] if m["role"] == "tool")
            assert "prevista" in tool_msg["content"], tool_msg
            message = {"role": "assistant", "content": json.dumps({
                "modalidade": "admissao_graduados", "inscricao_inicio": "2026-11-10",
                "inscricao_fim": "2026-11-16", "status": "prevista", "oferece_ciencia_computacao": True,
                "ciencia_computacao_cuiaba": True, "vagas_ciencia_computacao_cuiaba": 4,
                "resumo": "Inscrições em novembro.", "evidencia": "10/11/2026 a 16/11/2026"})}
            finish = "stop"
        payload = json.dumps({"id": "x", "object": "chat.completion", "created": 1, "model": body["model"],
                              "choices": [{"index": 0, "finish_reason": finish, "message": message}],
                              "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.mark.skipif(not os.access(workers.AI_BIN, os.X_OK), reason="agente Go não compilado (task build)")
def test_ponta_a_ponta_python_go_groq_e_tool_python(monkeypatch):
    FakeGroq.bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeGroq)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("GROQ_API_KEY", "teste")
    monkeypatch.setenv("GROQ_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    try:
        body = "Edital Admissão de Graduado 2026/2. Período de inscrições: 10/11/2026 a 16/11/2026."
        h = assess("Edital 09/2026", body, HOJE)
        r = llm.refine("Edital 09/2026", body, h, HOJE)
    finally:
        server.shutdown()
    assert len(FakeGroq.bodies) == 2
    assert {t["function"]["name"] for t in FakeGroq.bodies[0]["tools"]} >= {"extrair_regras", "ocr_pdf", "renderizar_pagina"}
    assert r.status == "prevista" and r.vagas_cc == 4
    assert r.checks[-1].ok is True, r.checks[-1]
    assert "tools: extrair_regras" in r.fonte_analise
