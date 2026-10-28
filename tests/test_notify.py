import pytest
import requests

from ufmt_monitor import notify
from ufmt_monitor.config import load_sources
from ufmt_monitor.extract import parse_html


class Resp:
    def __init__(self, status, body=""):
        self.status_code, self.text, self.ok = status, body, status < 400

    def json(self):
        raise ValueError("não é JSON")


def test_chat_invalido_nao_impede_os_outros_nem_aborta(monkeypatch):
    posted = []

    def post(url, json, timeout):
        posted.append(json["chat_id"])
        if json["chat_id"] == "ruim":
            raise requests.ConnectionError(f"falhou em {url}")
        return Resp(200)

    monkeypatch.setattr(notify.requests, "post", post)
    tg = notify.Telegram(token="SEGREDO", chat_ids="ruim,bom")
    tg.send("oi")
    assert posted == ["ruim", "bom"]
    assert len(tg.errors) == 1 and "SEGREDO" not in tg.errors[0]


def test_429_sem_json_nao_quebra(monkeypatch):
    calls = iter([Resp(429, "slow down"), Resp(200)])
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: next(calls))
    monkeypatch.setattr(notify.time, "sleep", lambda s: None)
    tg = notify.Telegram(token="t", chat_ids="1")
    tg.send("oi")
    assert tg.errors == []


def test_link_malformado_nao_derruba_a_pagina():
    page = parse_html('<a href="http://[ufmt">Edital de Admissão de Graduados</a>'
                      '<a href="/edital">Edital de Admissão de Graduados 2027</a>', "https://www.ufmt.br/")
    assert [link.url for link in page.links] == ["https://www.ufmt.br/edital"]


def test_item_pattern_invalido_falha_ao_carregar(tmp_path):
    cfg = tmp_path / "sources.toml"
    cfg.write_text('[[source]]\nid="a"\nname="A"\nurl="https://ufmt.br"\nitem_pattern="(["\n', encoding="utf-8")
    with pytest.raises(ValueError, match="item_pattern"):
        load_sources(cfg)
