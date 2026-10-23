import subprocess
import sys

from conftest import FakeFetcher
from ufmt_monitor.config import ROOT
from ufmt_monitor.gen.ufmt.v1 import worker_pb2 as pb
from ufmt_monitor.worker import handle


def call(request: pb.Request, fetcher=None) -> pb.Response:
    response = pb.Response()
    response.ParseFromString(handle(request.SerializeToString(), fetcher))
    return response


def test_extract_aplica_as_regras_deterministicas():
    res = call(pb.Request(extract=pb.ExtractRequest(
        title="Edital - Admissão de Graduado 2026/2",
        text="Inscrições de 10/11/2026 a 16/11/2026. Ciência da Computação - Campus Cuiabá - 4 vagas.",
        today="2026-10-23")))
    e = res.extract
    assert (e.modalidade, e.status, e.vagas_cc, e.computacao_cuiaba) == ("admissao_graduados", "prevista", 4, True)
    assert [(w.start, w.end, w.status) for w in e.windows] == [("2026-11-10", "2026-11-16", "prevista")]
    assert list(e.dates) == ["2026-11-10", "2026-11-16"]


def test_extract_sem_vagas_informa_menos_um():
    res = call(pb.Request(extract=pb.ExtractRequest(text="nada aqui", today="2026-10-23")))
    assert res.extract.vagas_cc == -1 and res.extract.status == "indeterminada"


def test_fetch_devolve_texto_e_verificacoes(fixture_text):
    url = "https://www.ufmt.br/unidade/ingresso/publicacao/9001"
    res = call(pb.Request(fetch=pb.FetchRequest(url=url)), FakeFetcher({url: fixture_text("edital_2026.html")}))
    assert "Ciência da Computação" in res.fetch.text
    assert res.fetch.sources[0] == url
    assert {c.name: c.result for c in res.fetch.checks}["página×navegador"] == "nao_executada"


def test_erros_tipados():
    assert call(pb.Request(extract=pb.ExtractRequest(today="ontem"))).error.code == pb.Error.CODE_INVALID_REQUEST
    assert call(pb.Request(fetch=pb.FetchRequest(url="file:///etc/passwd"))).error.code == pb.Error.CODE_INVALID_REQUEST
    assert call(pb.Request(ocr=pb.OcrRequest())).error.code == pb.Error.CODE_UNSUPPORTED
    res = pb.Response()
    res.ParseFromString(handle(b"\xff\xff\xff"))
    assert res.error.code == pb.Error.CODE_INVALID_REQUEST


def test_executavel_fala_protobuf_pelo_stdin():
    req = pb.Request(extract=pb.ExtractRequest(text="Inscrições até 05/11/2026", today="2026-10-23"))
    proc = subprocess.run([sys.executable, "-m", "ufmt_monitor.worker"], input=req.SerializeToString(),
                          capture_output=True, cwd=ROOT, check=True)
    res = pb.Response()
    res.ParseFromString(proc.stdout)
    assert res.extract.status == "aberta"
