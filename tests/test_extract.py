import pytest
import requests

from ufmt_monitor.extract import Fetcher, host_allowed


class Resp:
    def __init__(self, url, status=200, location=None, text="<html><body><p>ok</p></body></html>"):
        self.url, self.status_code, self.text = url, status, text
        self.headers = {"location": location} if location else {"content-type": "text/html"}
        self.is_redirect = location is not None
        self.encoding = "utf-8"
        self.content = text.encode()

    def raise_for_status(self):
        pass


class Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("allow_redirects", True)))
        return self.routes[url]


@pytest.mark.parametrize("url,ok", [
    ("https://www.ufmt.br/x", True),
    ("https://ufmt.br/x", True),
    ("https://cms.ufmt.br/a.pdf", True),
    ("https://notufmt.br/x", False),
    ("https://ufmt.br.evil.com/x", False),
    ("https://ufmt.br@evil.com/x", False),
    ("file:///etc/passwd", False),
])
def test_host_allowed(url, ok):
    assert host_allowed(url, ["ufmt.br"]) is ok


def test_redirect_para_fora_da_lista_e_recusado():
    session = Session({
        "https://www.ufmt.br/a": Resp("https://www.ufmt.br/a", 302, "https://cms.ufmt.br/b"),
        "https://cms.ufmt.br/b": Resp("https://cms.ufmt.br/b", 302, "https://evil.example.com/c"),
    })
    fetcher = Fetcher(delay=0, session=session, allowed_hosts=["ufmt.br"])
    with pytest.raises(ValueError, match="evil.example.com"):
        fetcher.page("https://www.ufmt.br/a")
    assert [u for u, _ in session.calls] == ["https://www.ufmt.br/a", "https://cms.ufmt.br/b"]


def test_redirect_dentro_da_lista_e_seguido():
    session = Session({
        "https://www.ufmt.br/a": Resp("https://www.ufmt.br/a", 301, "/b"),
        "https://www.ufmt.br/b": Resp("https://www.ufmt.br/b"),
    })
    page = Fetcher(delay=0, session=session, allowed_hosts=["ufmt.br"]).page("https://www.ufmt.br/a")
    assert page.text == "ok"


def test_loop_de_redirect_para():
    session = Session({"https://www.ufmt.br/a": Resp("https://www.ufmt.br/a", 302, "/a")})
    with pytest.raises(requests.TooManyRedirects):
        Fetcher(delay=0, session=session, allowed_hosts=["ufmt.br"]).page("https://www.ufmt.br/a")


def test_documento_ignora_anexo_fora_da_lista(monkeypatch):
    html = ('<html><body><p>Edital de admissão de graduados com anexos abaixo.</p>'
            '<a href="https://evil.example.com/edital.pdf">Edital completo</a></body></html>')
    session = Session({"https://www.ufmt.br/p": Resp("https://www.ufmt.br/p", text=html)})
    monkeypatch.setenv("BROWSER", "0")
    doc = Fetcher(delay=0, session=session, allowed_hosts=["ufmt.br"]).document("https://www.ufmt.br/p")
    assert doc.sources == ["https://www.ufmt.br/p"]
    assert [u for u, _ in session.calls] == ["https://www.ufmt.br/p"]


def test_sem_lista_o_monitor_segue_redirects_normalmente():
    session = Session({"https://fct.ufmt.br/": Resp("https://fct.ufmt.br/")})
    Fetcher(delay=0, session=session).page("https://fct.ufmt.br/")
    assert session.calls == [("https://fct.ufmt.br/", True)]
