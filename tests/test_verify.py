from datetime import date

from conftest import FakeFetcher
from test_monitor import BASE, LIST_URL, LISTING, PUB, make_pages, started
from ufmt_monitor.extract import check_pdf_readings, parse_html
from ufmt_monitor.monitor import Monitor
from ufmt_monitor.notify import Telegram
from ufmt_monitor.verify import Check, compare_listing, confidence

EDITAL = "Edital Admissão de Graduados. Período de inscrições: 23 a 28 de junho de 2026. Ciência da Computação."


def test_texto_e_ocr_concordam():
    c = check_pdf_readings([EDITAL], EDITAL.replace("Período", "Periodo"))
    assert c.ok is True


def test_texto_e_ocr_com_datas_diferentes_divergem():
    c = check_pdf_readings([EDITAL], EDITAL.replace("28 de junho", "28 de julho"))
    assert c.ok is False and "≠" in c.detail


def test_pdf_escaneado_tem_leitura_unica():
    assert check_pdf_readings([], EDITAL).ok is None
    assert check_pdf_readings([EDITAL], "").ok is None


def test_listagem_compara_publicacoes_e_ciclos():
    assert compare_listing(["a", "b", "c"], ["a", "b", "c"], {"2026/1"}, {"2026/1"}).ok is True
    assert compare_listing(["a"], ["a", "b", "c"], set(), set()).ok is False
    c = compare_listing(["a"], ["a"], {"2026/1"}, {"2026/1", "2026/2"})
    assert c.ok is False and "2026/2" in c.detail


def test_confianca():
    assert confidence([Check("a", True), Check("b", True)]) == "alta"
    assert confidence([Check("a", True), Check("b", None)]) == "média"
    assert confidence([Check("a", True), Check("b", False)]) == "baixa"


def run_with_browser(fixture_text, st, http_listing, rendered_listing, extra=None):
    pages = make_pages(fixture_text, **{LIST_URL: http_listing, **(extra or {})})
    rendered = {LIST_URL: parse_html(rendered_listing, LIST_URL)}
    rendered.update({url: parse_html(html, url) for url, html in pages.items()
                     if url.startswith(BASE + "/publicacao/")})
    tg = Telegram(dry_run=True)
    Monitor([LISTING], st, FakeFetcher(pages, rendered), tg, date(2026, 10, 23)).run()
    return tg


def test_leituras_que_batem_aparecem_no_alerta(fixture_text):
    st = started(fixture_text)
    novo = PUB(9100)
    listing = fixture_text("listagem.html").replace(
        '<div class="publicacoes">',
        f'<div class="publicacoes"><a class="card" href="{novo}"><div>22/10/2026 10:00</div>'
        '<div>Edital nº 02/2027 - Admissão de Graduado 2027/1</div></a>')
    extra = {novo: "<html><body><p>Admissão de Graduados. Inscrições de 20/10/2026 a 30/10/2026.</p></body></html>"}
    tg = run_with_browser(fixture_text, st, listing, listing, extra)
    msg = tg.sent[0]
    assert "HTML×navegador ✔" in msg and "página×navegador ✔" in msg
    assert "Confiança: média" in msg            # Groq não configurado no teste
    assert st["sources"]["lista"]["verification"]["ok"] is True


def test_publicacao_que_so_o_navegador_ve_nao_se_perde(fixture_text, monkeypatch):
    monkeypatch.setenv("HEARTBEAT_DAYS", "0")
    st = started(fixture_text)
    http = fixture_text("listagem.html")
    novo = PUB(9100)
    rendered = http.replace(
        '<div class="publicacoes">',
        f'<div class="publicacoes"><a class="card" href="{novo}"><div>22/10/2026 10:00</div>'
        '<div>Edital nº 02/2027 - Admissão de Graduado 2027/1</div></a>')
    extra = {novo: "<html><body><p>Admissão de Graduados. Inscrições de 20/10/2026 a 30/10/2026.</p></body></html>"}
    tg = run_with_browser(fixture_text, st, http, rendered, extra)
    assert "INSCRIÇÕES ABERTAS" in tg.sent[0] and "Confiança: baixa" in tg.sent[0]
    tg = run_with_browser(fixture_text, st, http, rendered, extra)
    assert any("leituras divergentes" in m for m in tg.sent)
    tg = run_with_browser(fixture_text, st, rendered, rendered, extra)
    assert any("voltaram a bater" in m for m in tg.sent)
