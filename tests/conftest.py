from __future__ import annotations

from pathlib import Path

import pytest

from ufmt_monitor.extract import Fetcher, Page, parse_html

FIXTURES = Path(__file__).parent / "fixtures"


class FakeFetcher(Fetcher):

    def __init__(self, pages: dict[str, str], rendered: dict[str, Page] | None = None):
        super().__init__(delay=0)
        self.pages = pages
        self.rendered = rendered or {}
        self.requested: list[str] = []

    def render(self, url: str, discover: bool = False) -> Page:
        if url not in self.rendered:
            raise RuntimeError("navegador indisponível")
        return self.rendered[url]

    def page(self, url: str, verify: bool = True) -> Page:
        self.requested.append(url)
        if url not in self.pages:
            raise ConnectionError(f"sem rede para {url}")
        content = self.pages[url]
        if url.endswith(".pdf"):
            return Page(url, url.rsplit("/", 1)[-1], content)
        return parse_html(content, url)


@pytest.fixture
def fixture_text():
    return lambda name: (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def no_external_services(monkeypatch):
    monkeypatch.setenv("BROWSER", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
