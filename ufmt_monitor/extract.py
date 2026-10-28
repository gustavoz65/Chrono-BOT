from __future__ import annotations

import io
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import browser, ocr
from .dates import fold, inscription_windows
from .verify import Check, compare_text, compare_windows

PAGE_CHECK = "página×navegador"

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 UFMT-Graduados-Monitor/2.0 (uso pessoal; consultas espaçadas)"
)
MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 40
BOILERPLATE_TAGS = ("nav", "header", "footer", "aside", "form")
BLOCK_TAGS = ("p", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "blockquote",
              "div", "section", "article")
LEADING_DATETIME_RE = re.compile(r"^(?:\d{1,2}/\d{1,2}/\d{2,4}\s*)?(?:\d{1,2}[:h]\d{2}\s*)?")
PAGINATION_RE = re.compile(r"^(?:\d+|«|»|‹|›|<|>|anterior|proxima|próxima|primeira|ultima|última|ver mais|mais)$", re.I)


@dataclass
class Link:
    text: str
    url: str
    context: str = ""

    @property
    def is_pdf(self) -> bool:
        return urlsplit(self.url).path.lower().endswith(".pdf")


@dataclass
class Select:
    name: str
    options: list[tuple[str, str]]
    selected: str = ""
@dataclass
class Page:
    url: str
    title: str
    text: str
    links: list[Link] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    status_code: int = 200
    nav_links: list[Link] = field(default_factory=list)
    selects: list[Select] = field(default_factory=list)
    option_urls: dict[str, str] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_"))
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, connect=3, read=3, backoff_factor=2,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET", "POST"))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"})
    return session


def _in_boilerplate(tag) -> bool:
    return any(p.name in BOILERPLATE_TAGS for p in tag.parents)


def parse_html(html: str, base_url: str, status_code: int = 200) -> Page:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "svg", "iframe", "template"]):
        node.decompose()
    title = clean(soup.title.get_text(" ")) if soup.title else base_url
    text = clean(soup.get_text(" "))

    links: list[Link] = []
    nav_links: list[Link] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        # Quando o link envolve o "card" inteiro, o texto começa com data/hora de publicação.
        label = LEADING_DATETIME_RE.sub("", clean(a.get_text(" "))).strip() or clean(a.get("title", ""))
        if not label or PAGINATION_RE.match(label):
            continue
        try:
            url = normalize_url(urljoin(base_url, href))
        except ValueError:  # href malformado (ex.: "http://[x") não pode derrubar a página inteira
            continue
        if url == normalize_url(base_url) or url in seen:
            continue
        seen.add(url)
        container = a.find_parent(["li", "tr", "article", "p", "div"])
        context = clean(container.get_text(" "))[:400] if container else label
        (nav_links if _in_boilerplate(a) else links).append(Link(label[:300], url, context))

    selects: list[Select] = []
    for sel in soup.find_all("select"):
        options, selected = [], ""
        for opt in sel.find_all("option"):
            label = clean(opt.get_text(" "))
            value = (opt.get("value") or label).strip()
            if not label or not value:
                continue
            options.append((value, label))
            if opt.has_attr("selected"):
                selected = value
        if options:
            selects.append(Select(sel.get("name") or sel.get("id") or "", options, selected))

    blocks: list[str] = []
    for el in soup.find_all(BLOCK_TAGS):
        if el.find(BLOCK_TAGS):
            continue
        t = clean(el.get_text(" "))
        if 25 <= len(t) <= 1500:
            blocks.append(t)
    return Page(base_url, title, text, links, list(dict.fromkeys(blocks)), status_code, nav_links, selects)


@dataclass
class Document:
    text: str
    sources: list[str]
    checks: list[Check] = field(default_factory=list)


def check_pdf_readings(layer_pages: list[str], scanned: str) -> Check:
    name = "texto×OCR"
    layer = clean(" ".join(layer_pages))
    if not scanned:
        return Check(name, None, "OCR indisponível ou sem resultado")
    if not layer:
        return Check(name, None, "PDF escaneado: só o OCR consegue ler")
    words = compare_text(name, layer, scanned)
    windows = compare_windows(name, inscription_windows(layer), inscription_windows(scanned))
    return Check(name, bool(words.ok and windows.ok), f"{words.detail}; {windows.detail}")


def pdf_reading(data: bytes) -> tuple[str, Check]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        try:
            parts.append(clean(page.extract_text() or ""))
        except Exception as exc:
            log.warning("falha ao extrair página de PDF: %s", exc)
    text = clean(" ".join(parts))
    if ocr.needs_ocr(text, len(reader.pages)):
        scanned = clean(ocr.ocr_pdf(data))
        check = check_pdf_readings([], scanned)
        return (scanned if len(scanned) > len(text) else text), check
    pages = min(ocr.VERIFY_PAGES, len(parts))
    scanned = clean(ocr.ocr_pdf(data, max_pages=pages)) if ocr.available() else ""
    return text, check_pdf_readings(parts[:pages], scanned)


def pdf_text(data: bytes) -> str:
    return pdf_reading(data)[0]


class Fetcher:
    def __init__(self, timeout: int = 30, delay: float = 1.5, session: requests.Session | None = None):
        self.session = session or make_session()
        self.timeout = timeout
        self.delay = delay
        self._last = 0.0

    def _get(self, url: str, verify: bool = True) -> requests.Response:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = self.session.get(url, timeout=self.timeout, verify=verify)
        finally:
            self._last = time.monotonic()
        resp.raise_for_status()
        return resp

    def page(self, url: str, verify: bool = True) -> Page:
        resp = self._get(url, verify)
        ctype = resp.headers.get("content-type", "").lower()
        if "pdf" in ctype or urlsplit(url).path.lower().endswith(".pdf"):
            if len(resp.content) > MAX_PDF_BYTES:
                raise ValueError(f"PDF grande demais ({len(resp.content)} bytes)")
            text, check = pdf_reading(resp.content)
            return Page(url, urlsplit(url).path.rsplit("/", 1)[-1], text, status_code=resp.status_code, checks=[check])
        resp.encoding = resp.encoding if resp.encoding and resp.encoding.lower() != "iso-8859-1" else resp.apparent_encoding
        return parse_html(resp.text, resp.url or url, resp.status_code)

    def render(self, url: str, discover: bool = False) -> Page:
        if not browser.available():
            raise RuntimeError("navegador indisponível (instale com `npm ci` em browser/)")
        data = browser.render(url, discover)
        page = parse_html(data["html"], data["url"] or url)
        page.option_urls = {o["value"]: normalize_url(o["url"]) for o in data.get("options", [])}
        return page

    def _cross_check_page(self, page: Page) -> tuple[Page, Check]:
        try:
            rendered = self.render(page.url)
        except Exception as exc:
            return page, Check(PAGE_CHECK, None, f"navegador indisponível ({exc})"[:200])
        if len(rendered.text) > 2 * len(page.text) + 200:
            return rendered, Check(PAGE_CHECK, None, "conteúdo só aparece com JavaScript; lido pelo navegador")
        check = compare_text(PAGE_CHECK, page.text, rendered.text)
        return page, check

    def document(self, url: str, verify: bool = True, max_pdfs: int = 3) -> Document:
        page = self.page(url, verify)
        checks = list(page.checks)
        if not checks:
            page, check = self._cross_check_page(page)
            checks.append(check)
        texts, sources = [page.text], [url]
        if page.links:
            pdfs = [link for link in page.links if link.is_pdf]
            priority = lambda link: 0 if re.search(r"edital|cronograma|retifica|aviso", fold(link.text + link.url)) else 1  # noqa: E731
            for link in sorted(pdfs, key=priority)[:max_pdfs]:
                try:
                    attachment = self.page(link.url, verify)
                    texts.append(attachment.text)
                    sources.append(link.url)
                    checks += attachment.checks
                except Exception as exc:
                    log.warning("não foi possível ler anexo %s: %s", link.url, exc)
        return Document("\n\n".join(t for t in texts if t), sources, checks)
