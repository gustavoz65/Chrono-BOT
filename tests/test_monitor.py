from datetime import date

from conftest import FakeFetcher
from ufmt_monitor import state
from ufmt_monitor.config import Source, load_sources
from ufmt_monitor.extract import parse_html
from ufmt_monitor.monitor import Monitor, discover_cycles, listing_items
from ufmt_monitor.notify import Telegram

BASE = "https://www.ufmt.br/unidade/ingresso"
LIST_URL = f"{BASE}/publicacoes?id=61&category=244&page=1"
CAT = lambda n: f"{BASE}/publicacoes?category={n}&id=61&page=1"  # noqa: E731
PUB = lambda n: f"{BASE}/publicacao/{n}"  # noqa: E731
PDF_URL = "https://cms.ufmt.br/files/publication/61/EditalCompleto.pdf"

LISTING = Source("lista", "Publicações de Graduado", LIST_URL, kind="listing", critical=True,
                 modalidade="admissao_graduados")
HOME = Source("home", "Central de Ingresso", BASE, kind="page")

NOVO_CARD = ('<div class="publicacoes"><a class="card" href="{href}"><div class="meta">{data} 10:00</div>'
             '<div class="titulo">{titulo}</div></a>')


def card(listing: str, href: str, titulo: str, data: str = "22/10/2026") -> str:
    return listing.replace('<div class="publicacoes">', NOVO_CARD.format(href=href, titulo=titulo, data=data))


def child_listing(*cards):
    body = "".join(f'<a class="card" href="{h}"><div>{d} 08:00</div><div>{t}</div></a>' for h, t, d in cards)
    return f"<html><body><div class='publicacoes'>{body}</div></body></html>"


def make_pages(fixture_text, **extra):
    pages = {
        LIST_URL: fixture_text("listagem.html"),
        PUB(9001): fixture_text("edital_2026.html"),
        PDF_URL: "Admissão de Graduados 2026. Inscrições de 23 a 28 de junho de 2026. Ciência da Computação Cuiabá.",
        PUB(9008): "<html><body><p>Convocação para matrícula.</p></body></html>",
        PUB(9009): "<html><body><p>Resultado dos recursos.</p></body></html>",
        PUB(9010): "<html><body><p>Processo seletivo encerrado.</p></body></html>",
        BASE: "<html><body><main><p>Bem-vindo à Central de Ingresso da UFMT.</p></main></body></html>",
        CAT(240): child_listing((PUB(8001), "Transferência Facultativa 2026/1 - Resultado final", "01/05/2026")),
        CAT(238): child_listing((PUB(7001), "Vagas Remanescentes 2026/1 - Resultado final", "01/04/2026")),
    }
    for path in ("vagas-remanescentes/102", "transferencia-facultativa/103", "admissao-de-graduado/3194"):
        pages[f"{BASE}/pagina/{path}"] = "<html><body><p>Página institucional.</p></body></html>"
    pages.update(extra)
    return pages


def run(pages, st, today=date(2026, 10, 23), sources=(LISTING, HOME)):
    tg = Telegram(dry_run=True)
    Monitor(list(sources), st, FakeFetcher(pages), tg, today).run()
    return tg


def started(fixture_text):
    st = state.empty()
    run(make_pages(fixture_text), st)
    return st


def test_sources_toml_valido():
    sources = load_sources()
    assert any(s.kind == "listing" and "category=244" in s.url for s in sources)


def test_listagem_le_cards_e_ignora_menu_rodape_paginacao(fixture_text):
    items = listing_items(parse_html(fixture_text("listagem.html"), LIST_URL), LISTING)
    urls = [i.url for i in items]
    assert urls[:4] == [PUB(9010), PUB(9009), PUB(9008), PUB(9001)]
    assert items[0].text == "Admissão de Graduados 2026/1 - Processo Seletivo Encerrado."
    assert not any("sisu" in u or "publicacoes" in u or "ouvidoria" in u for u in urls)


def test_descobre_ciclos_no_filtro_e_no_menu(fixture_text):
    cycles = {(c.kind, c.label, c.url) for c in discover_cycles(parse_html(fixture_text("listagem.html"), LIST_URL), LIST_URL)}
    assert ("admissao_graduados", "Admissão de Graduado 2025/1", CAT(230)) in cycles
    assert ("transferencia_facultativa", "Transferência Facultativa 2026/1", CAT(240)) in cycles
    assert ("vagas_remanescentes", "Vagas Remanescentes 2026/1", CAT(238)) in cycles


def test_primeira_execucao_mostra_que_2026_1_esta_encerrado(fixture_text):
    st = state.empty()
    tg = run(make_pages(fixture_text), st)
    assert len(tg.sent) == 1
    msg = tg.sent[0]
    assert "Monitor UFMT ativado" in msg
    assert "Processo Seletivo Encerrado" in msg and "Inscrições encerradas" in msg
    assert "23/06/2026 a 28/06/2026" in msg
    assert "Vagas de Ciência da Computação (Cuiabá):</b> 3" in msg
    assert "Admissão de Graduado 2025/1, Admissão de Graduado 2026/1" in msg
    assert st["publications"][PUB(9010)]["status"] == "encerrada"
    assert {v["kind"] for v in st["sources"]["lista"]["followed"].values()} == {
        "transferencia_facultativa", "vagas_remanescentes"}
    assert run(make_pages(fixture_text), st).sent == []


def test_novo_edital_aberto_dispara_alerta_urgente(fixture_text):
    st = started(fixture_text)
    novo = PUB(9100)
    pages = make_pages(fixture_text, **{
        LIST_URL: card(fixture_text("listagem.html"), novo, "Edital nº 02/2027 - Admissão de Graduado 2027/1"),
        novo: "<html><body><p>Admissão de Graduados 2027/1. Inscrições de 20/10/2026 a 30/10/2026.</p></body></html>",
    })
    tg = run(pages, st)
    assert len(tg.sent) == 1 and not tg.silent_sent
    assert "INSCRIÇÕES ABERTAS — ADMISSÃO DE GRADUADOS" in tg.sent[0]
    assert "20/10/2026 a 30/10/2026" in tg.sent[0]
    assert run(pages, st).sent == []
    tg = run(pages, st, today=date(2026, 10, 29))
    assert len(tg.sent) == 1 and "terminando" in tg.sent[0]


def test_publicacao_de_rotina_vai_silenciosa_e_agrupada(fixture_text):
    st = started(fixture_text)
    listing = fixture_text("listagem.html")
    listing = card(listing, PUB(9011), "Admissão de Graduados 2026/1 - Resultado final - 4ª Chamada")
    listing = card(listing, PUB(9012), "Admissão de Graduados 2026/1 - Relação de Matrículas Deferidas")
    tg = run(make_pages(fixture_text, **{LIST_URL: listing}), st)
    assert len(tg.sent) == 1 and tg.silent_sent == tg.sent
    assert "2 publicação(ões) de rotina" in tg.sent[0]


def test_edital_previsto_avisa_no_dia_que_abre(fixture_text, monkeypatch):
    monkeypatch.setenv("HEARTBEAT_DAYS", "0")
    st = started(fixture_text)
    novo = PUB(9200)
    pages = make_pages(fixture_text, **{
        LIST_URL: card(fixture_text("listagem.html"), novo, "Edital - Admissão de Graduado 2027/1"),
        novo: "<html><body><p>Admissão de Graduados. Inscrições de 10/11/2026 a 15/11/2026.</p></body></html>",
    })
    tg = run(pages, st)
    assert "Nova publicação de Admissão de Graduados" in tg.sent[0] and "previstas" in tg.sent[0]
    assert run(pages, st, today=date(2026, 11, 9)).sent == []
    tg = run(pages, st, today=date(2026, 11, 10))
    assert any("INSCRIÇÕES ABERTAS" in m for m in tg.sent)


def test_novo_ciclo_no_filtro_ano(fixture_text):
    st = started(fixture_text)
    listing = fixture_text("listagem.html").replace(
        '<option value="244" selected>', '<option value="251">Admissão de Graduado 2026/2</option><option value="244" selected>')
    pages = make_pages(fixture_text, **{
        LIST_URL: listing,
        CAT(251): child_listing((PUB(9300), "Edital nº 09/2026 - Admissão de Graduado 2026/2", "22/10/2026")),
        PUB(9300): "<html><body><p>Admissão de Graduados 2026/2. Inscrições de 10/11/2026 a 16/11/2026. "
                   "Ciência da Computação - Cuiabá - 4 vagas.</p></body></html>",
    })
    tg = run(pages, st)
    assert len(tg.sent) == 1
    msg = tg.sent[0]
    assert "NOVO CICLO DE ADMISSÃO DE GRADUADO" in msg and "2026/2" in msg
    assert "10/11/2026 a 16/11/2026" in msg and "(Cuiabá):</b> 4" in msg
    assert run(pages, st).sent == []
    assert CAT(251) in st["sources"]["lista"]["followed"]


def test_novo_ciclo_de_transferencia_e_sinal_antecipado(fixture_text):
    st = started(fixture_text)
    listing = fixture_text("listagem.html").replace(
        '<ul><li><a href="/unidade/ingresso/publicacoes?id=61&category=240&page=1">',
        '<ul><li><a href="/unidade/ingresso/publicacoes?id=61&category=260&page=1">Transferência Facultativa 2027/1</a></li>'
        '<li><a href="/unidade/ingresso/publicacoes?id=61&category=240&page=1">')
    pages = make_pages(fixture_text, **{
        LIST_URL: listing,
        CAT(260): child_listing((PUB(8100), "Edital - Transferência Facultativa 2027/1", "22/10/2026")),
        PUB(8100): "<html><body><p>Transferência Facultativa. Inscrições de 03/11/2026 a 07/11/2026.</p></body></html>",
    })
    tg = run(pages, st)
    assert len(tg.sent) == 1
    assert "processo que alimenta a Admissão de Graduados" in tg.sent[0]
    assert "Transferência Facultativa" in tg.sent[0]


def test_publicacao_nova_de_transferencia_explica_o_motivo(fixture_text):
    st = started(fixture_text)
    pages = make_pages(fixture_text, **{
        CAT(240): child_listing((PUB(8002), "Transferência Facultativa 2026/1 - Edital de vagas", "22/10/2026"),
                                (PUB(8001), "Transferência Facultativa 2026/1 - Resultado final", "01/05/2026")),
        PUB(8002): "<html><body><p>Inscrições de 01/12/2026 a 05/12/2026.</p></body></html>",
    })
    tg = run(pages, st)
    assert len(tg.sent) == 1 and "Sinal antecipado — Transferência Facultativa" in tg.sent[0]
    assert "vão para a Admissão de Graduados" in tg.sent[0]


def test_trecho_novo_em_pagina_generica(fixture_text):
    st = started(fixture_text)
    home = ("<html><body><main><p>Bem-vindo à Central de Ingresso da UFMT.</p>"
            "<p>Admissão de Graduados 2027: inscrições de 01/12/2026 a 05/12/2026.</p></main></body></html>")
    tg = run(make_pages(fixture_text, **{BASE: home}), st)
    assert len(tg.sent) == 1 and "Texto novo sobre Admissão de Graduados" in tg.sent[0]
    assert "01/12/2026 a 05/12/2026" in tg.sent[0]


def test_falhas_alertam_uma_vez_e_avisam_recuperacao(fixture_text):
    st = started(fixture_text)
    pages = make_pages(fixture_text)
    broken = {k: v for k, v in pages.items() if k != LIST_URL}
    assert run(broken, st).sent == []
    tg = run(broken, st)
    assert len(tg.sent) == 1 and "fonte com falha" in tg.sent[0]
    assert run(broken, st).sent == []
    tg = run(pages, st)
    assert len(tg.sent) == 1 and "voltou a responder" in tg.sent[0]


def test_layout_quebrado_conta_como_falha(fixture_text):
    st = started(fixture_text)
    vazio = make_pages(fixture_text, **{LIST_URL: "<html><body><main><p>Nada aqui</p></main></body></html>"})
    run(vazio, st)
    tg = run(vazio, st)
    assert any("layout da página pode ter mudado" in m for m in tg.sent)


def test_heartbeat_semanal(fixture_text):
    st = started(fixture_text)
    pages = make_pages(fixture_text)
    assert run(pages, st, today=date(2026, 10, 29)).sent == []
    tg = run(pages, st, today=date(2026, 10, 30))
    assert len(tg.sent) == 1 and "funcionando" in tg.sent[0]


def test_estado_roundtrip(tmp_path, fixture_text):
    st = started(fixture_text)
    path = tmp_path / "state.json"
    state.save(st, path)
    assert state.load(path) == st


def test_listagem_montada_por_javascript_usa_o_navegador(fixture_text):
    st = state.empty()
    vazio = "<html><body><div class='publicacoes'></div></body></html>"
    rendered = parse_html(fixture_text("listagem.html"), LIST_URL)
    tg = Telegram(dry_run=True)
    fetcher = FakeFetcher(make_pages(fixture_text, **{LIST_URL: vazio}), {LIST_URL: rendered})
    Monitor([LISTING], st, fetcher, tg, date(2026, 10, 23)).run()
    assert "Monitor UFMT ativado" in tg.sent[0] and "Processo Seletivo Encerrado" in tg.sent[0]


def test_ciclo_novo_usa_url_real_descoberta_no_navegador(fixture_text):
    st = started(fixture_text)
    listing = fixture_text("listagem.html").replace(
        '<option value="244" selected>', '<option value="251">Admissão de Graduado 2026/2</option><option value="244" selected>')
    real = f"{BASE}/publicacoes?ano=2026-2&id=61"
    rendered = parse_html(listing, LIST_URL)
    rendered.option_urls = {"251": real, "244": CAT(244), "230": CAT(230)}
    pages = make_pages(fixture_text, **{LIST_URL: listing, real: child_listing()})
    tg = Telegram(dry_run=True)
    Monitor([LISTING, HOME], st, FakeFetcher(pages, {LIST_URL: rendered}), tg, date(2026, 10, 23)).run()
    assert len(tg.sent) == 1 and real.replace("&", "&amp;") in tg.sent[0]
    assert run(pages, st).sent == []


def test_ciclo_novo_com_listagem_fora_do_ar_avisa_quando_ela_voltar(fixture_text, monkeypatch):
    monkeypatch.setenv("HEARTBEAT_DAYS", "0")
    st = started(fixture_text)
    listing = fixture_text("listagem.html").replace(
        '<option value="244" selected>', '<option value="251">Admissão de Graduado 2026/2</option><option value="244" selected>')
    pages = make_pages(fixture_text, **{
        LIST_URL: listing,
        PUB(9300): "<html><body><p>Admissão de Graduados 2026/2. Inscrições de 20/10/2026 a 30/10/2026.</p></body></html>",
    })
    tg = run(pages, st)
    assert len(tg.sent) == 1 and "Ainda não consegui ler publicações" in tg.sent[0]
    pages[CAT(251)] = child_listing((PUB(9300), "Edital nº 09/2026 - Admissão de Graduado 2026/2", "22/10/2026"))
    tg = run(pages, st)
    assert any("INSCRIÇÕES ABERTAS" in m for m in tg.sent)


def test_enxurrada_de_links_novos_nao_analisa_tudo(fixture_text, monkeypatch):
    monkeypatch.setenv("HEARTBEAT_DAYS", "0")
    st = started(fixture_text)
    listing = fixture_text("listagem.html")
    for n in range(30):
        listing = card(listing, PUB(20000 + n), f"Admissão de Graduados 2026/1 - Resultado da etapa {n}")
    pages = make_pages(fixture_text, **{LIST_URL: listing})
    fetcher = FakeFetcher(pages)
    tg = Telegram(dry_run=True)
    Monitor([LISTING, HOME], st, fetcher, tg, date(2026, 10, 23)).run()
    assert sum(u.startswith(PUB(2)) for u in fetcher.requested) <= 8
    assert any("não analisadas" in m for m in tg.sent)
    assert run(pages, st).sent == []


def test_heartbeat_conta_so_as_fontes_configuradas(fixture_text):
    st = started(fixture_text)
    tg = run(make_pages(fixture_text), st, today=date(2026, 11, 5))
    assert any("Fontes OK nesta execução: 2/2." in m for m in tg.sent)
