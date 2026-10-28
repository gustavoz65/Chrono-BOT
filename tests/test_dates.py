from datetime import date

import pytest

from ufmt_monitor.classify import assess
from ufmt_monitor.dates import inscription_windows, overall_status, parse_dates

HOJE = date(2026, 10, 23)


@pytest.mark.parametrize("text,expected", [
    ("23/06/2026", [date(2026, 6, 23)]),
    ("23.06.26", [date(2026, 6, 23)]),
    ("1º de julho de 2026", [date(2026, 7, 1)]),
    ("23 a 28 de junho de 2026", [date(2026, 6, 23), date(2026, 6, 28)]),
    ("de 23/06 a 28/06/2026", [date(2026, 6, 23), date(2026, 6, 28)]),
    ("Edital nº 05/2026", []),
    ("31/02/2026", []),
])
def test_parse_dates(text, expected):
    assert parse_dates(text) == expected


@pytest.mark.parametrize("text,start,end", [
    ("Período de inscrições: 23 a 28 de junho de 2026.", date(2026, 6, 23), date(2026, 6, 28)),
    ("Inscrições de 23/06/2026 a 28/06/2026", date(2026, 6, 23), date(2026, 6, 28)),
    ("As inscrições vão das 8h do dia 23/06/2026 às 23h59 do dia 28/06/2026.", date(2026, 6, 23), date(2026, 6, 28)),
    ("Inscrições até 5 de novembro de 2026", None, date(2026, 11, 5)),
    ("INSCRIÇÕES: 23 de junho a 2 de julho de 2026", date(2026, 6, 23), date(2026, 7, 2)),
])
def test_inscription_windows(text, start, end):
    (w,) = inscription_windows(text, HOJE)
    assert (w.start, w.end) == (start, end)


def test_ignora_homologacao_e_resultado():
    text = "Homologação das inscrições: 02/07/2026. Resultado das inscrições deferidas: 05/07/2026."
    assert inscription_windows(text, HOJE) == []


def test_processo_2026_ja_encerrado():
    a = assess("Admissão de Graduado 2026",
               "Edital de Admissão de Graduados. Inscrições de 23 a 28 de junho de 2026.", HOJE)
    assert a.status == "encerrada"
    assert a.modalidade == "admissao_graduados"


def test_segundo_ciclo_e_outra_modalidade_e_esta_aberto():
    a = assess("Processo seletivo - Segundo Ciclo FCT",
               "Segundo ciclo em Engenharia de Software. Inscrições até 05/11/2026.", HOJE)
    assert a.modalidade == "segundo_ciclo"
    assert a.status == "aberta"


def test_prioriza_prorrogacao_aberta():
    text = "Inscrições de 01/10/2026 a 10/10/2026. Retificação: inscrições prorrogadas até 30/10/2026."
    status, win = overall_status(inscription_windows(text, HOJE), HOJE)
    assert status == "aberta" and win.end == date(2026, 10, 30)


def test_prevista():
    status, win = overall_status(inscription_windows("Inscrições: 10/01/2027 a 20/01/2027", HOJE), HOJE)
    assert status == "prevista" and win.start == date(2027, 1, 10)


def test_vagas_de_computacao_em_cuiaba():
    from ufmt_monitor.classify import vagas_computacao
    assert vagas_computacao("Ciência da Computação - Campus Cuiabá - 3 vagas; Engenharia Civil - 2 vagas") == 3
    assert vagas_computacao("Campus Cuiabá: Ciência da Computação, vagas: 5. Direito, vagas: 1") == 5
    assert vagas_computacao("Ciência da Computação - Campus Rondonópolis - 3 vagas") is None


def test_modalidade_usa_o_titulo_primeiro():
    from ufmt_monitor.classify import modalidade
    corpo = "As vagas não preenchidas serão ofertadas na Admissão de Graduados."
    assert modalidade("Edital - Transferência Facultativa 2027/1", corpo) == "transferencia_facultativa"
    assert modalidade("Edital - Vagas Remanescentes 2027", corpo) == "vagas_remanescentes"
    assert modalidade("Edital nº 05/2026", "Admissão de Graduado: vagas remanescentes da transferência") == \
        "admissao_graduados"


def test_ocr_em_c_le_edital_escaneado():
    from pathlib import Path

    import pytest

    from ufmt_monitor import ocr
    from ufmt_monitor.extract import pdf_text
    if not ocr.available():
        pytest.skip("binário ocr/bin/ufmt-ocr ausente (make -C ocr)")
    data = (Path(__file__).parent / "fixtures" / "edital_escaneado.pdf").read_bytes()
    a = assess("", pdf_text(data), HOJE)
    assert a.status == "prevista" and a.window.label() == "10/11/2026 a 16/11/2026"
    assert a.vagas_cc == 4 and a.computacao_cuiaba


@pytest.mark.parametrize("text", [
    "Inscrições de 28 de dezembro a 5 de janeiro de 2027",
    "Período de inscrições: 28/12 a 05/01/2027",
])
def test_periodo_que_atravessa_o_ano(text):
    [w] = inscription_windows(text, date(2026, 12, 1))
    assert (w.start, w.end) == (date(2026, 12, 28), date(2027, 1, 5))
    assert w.status(date(2026, 12, 30)) == "aberta"


def test_data_invalida_no_inicio_nao_vira_inicio_do_periodo():
    [w] = inscription_windows("Inscrições: 31/02/2026 a 10/03/2026", HOJE)
    assert (w.start, w.end) == (None, date(2026, 3, 10))


def test_etapa_da_frase_anterior_nao_anula_o_periodo():
    [w] = inscription_windows("Admissão de Graduado 2026/2 - Resultado\nInscrições de 20/10/2026 a 30/10/2026.", HOJE)
    assert (w.start, w.end) == (date(2026, 10, 20), date(2026, 10, 30))
    assert inscription_windows("Resultado da homologação das inscrições: 20/10/2026", HOJE) == []
