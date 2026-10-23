from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import hashlib
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from . import llm
from .classify import PRECURSORES, Assessment, assess, is_graduado, is_important, process_kind
from .config import Source
from .dates import DATE_RE, fold, inscription_windows, overall_status
from .extract import Fetcher, Link, Page, normalize_url
from .notify import Telegram, esc
from .verify import Check, compare_listing, compare_text, merge, summary

log = logging.getLogger(__name__)

MAX_ITEMS_PER_SOURCE = 500
MAX_SNIPPETS_PER_SOURCE = 300
BASELINE_ANALYZE = 3
CLOSING_SOON_DAYS = 2


@dataclass
class Publication:
    title: str
    url: str
    source: Source
    assessment: Assessment
    read_from: list[str]
    error: str = ""
def _dedupe(links: list[Link]) -> list[Link]:
    seen: dict[str, Link] = {}
    for link in links:
        seen.setdefault(link.url, link)
    return list(seen.values())


def listing_items(page: Page, source: Source) -> list[Link]:
    links = page.links
    if source.item_pattern:
        rx = re.compile(source.item_pattern, re.I)
        links = [link for link in links if rx.search(link.url)]
    links = [link for link in links if len(link.text) >= 12 or link.is_pdf]
    dated = [link for link in links if DATE_RE.search(fold(link.context))]
    if len(dated) >= max(1, source.min_items):
        links = dated
    return _dedupe(links + page_items(page))


def page_items(page: Page) -> list[Link]:
    return [link for link in page.links + page.nav_links
            if (is_graduado(f"{link.text} {link.url}") or process_kind(link.text)) and not is_listing_url(link.url)]


@dataclass(frozen=True)
class Cycle:
    kind: str
    label: str
    url: str
def is_listing_url(url: str) -> bool:
    return "publicacoes" in fold(urlsplit(url).path)


def discover_cycles(page: Page, base_url: str) -> list[Cycle]:
    found: dict[str, Cycle] = {}
    for sel in page.selects:
        for value, label in sel.options:
            kind = process_kind(label)
            if kind:
                url = page.option_urls.get(value) or cycle_url(base_url, sel.name, value)
                found.setdefault(url, Cycle(kind, label, url))
    for link in page.links + page.nav_links:
        kind = process_kind(link.text)
        if kind and is_listing_url(link.url):
            found.setdefault(link.url, Cycle(kind, link.text, link.url))
    return list(found.values())


def cycle_key(label: str) -> tuple[int, int]:
    m = re.search(r"(20\d{2})(?:\s*[/.-]\s*(\d))?", label)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def cycle_url(base: str, name: str, value: str) -> str:
    if value.startswith(("http://", "https://", "/", "?")):
        return normalize_url(urljoin(base, value))
    parts = urlsplit(base)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    key = name if name in query else ("category" if "category" in query and value.isdigit() else name or "category")
    query[key] = value
    if "page" in query:
        query["page"] = "1"
    return normalize_url(urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), "")))


def follow_source(parent: Source, cycle: Cycle) -> Source:
    key = hashlib.sha1(cycle.url.encode()).hexdigest()[:8]
    return Source(f"{parent.id}@{key}", f"Central de Ingresso — {cycle.label}", cycle.url, kind="listing",
                  critical=False, verify_tls=parent.verify_tls, item_pattern=parent.item_pattern,
                  min_items=0, modalidade=cycle.kind)


def page_snippets(page: Page) -> list[str]:
    return [b[:600] for b in page.blocks if is_graduado(b)]


def analyze(fetcher: Fetcher, link: Link, source: Source, today: date,
            listing_check: Check | None = None) -> Publication:
    checks = [listing_check] if listing_check else []
    try:
        doc = fetcher.document(link.url, source.verify_tls)
        body, read_from, error = doc.text, doc.sources, ""
        checks += doc.checks
    except Exception as exc:
        body, read_from, error = link.context, [], f"{type(exc).__name__}: {exc}"
        checks.append(Check("leitura da publicação", False, error[:200]))
    result = assess(link.text, f"{link.context}\n{body}", today)
    result.checks = checks
    if source.kind == "listing" and result.modalidade == "outro" and source.modalidade:
        result.modalidade = source.modalidade
    result = llm.refine(link.text, body, result, today)
    return Publication(link.text, link.url, source, result, read_from, error)


def _pub_lines(pub: Publication) -> list[str]:
    a = pub.assessment
    lines = [
        f"<b>Publicação:</b> {esc(pub.title)}",
        f"<b>Modalidade:</b> {esc(a.modalidade_label)}",
        f"<b>Status:</b> {esc(a.status_label)}",
        f"<b>Inscrições:</b> {esc(a.window.label() if a.window else 'datas não identificadas')}",
    ]
    if a.vagas_cc is not None:
        lines.append(f"🎯 <b>Vagas de Ciência da Computação (Cuiabá):</b> {a.vagas_cc}")
    if a.computacao_cuiaba:
        lines.append("⭐ <b>Menciona Ciência da Computação em Cuiabá</b>")
    elif a.computacao:
        lines.append("⭐ Menciona Ciência da Computação")
    if a.resumo:
        lines.append(f"<b>Resumo:</b> {esc(a.resumo)}")
    if pub.error:
        lines.append(f"<i>Não consegui ler os detalhes ({esc(pub.error[:150])}).</i>")
    lines.append(f"🔒 <b>Confiança: {a.confidence}</b> — {esc(summary(a.checks))}")
    for c in merge(a.checks):
        if c.ok is False:
            lines.append(f"   ✖ {esc(c.name)}: {esc(c.detail)}")
    lines.append(f"<b>Análise:</b> {esc(a.fonte_analise)}")
    lines.append(f"<b>Fonte:</b> {esc(pub.source.name)}")
    lines.append(esc(pub.url))
    return lines


def new_publication_message(pub: Publication) -> str:
    a = pub.assessment
    note = []
    if a.modalidade in PRECURSORES:
        head = "📈 <b>Sinal antecipado — " + esc(a.modalidade_label.split(" (")[0]) + "</b>"
        note = ["", "<i>As vagas que sobrarem deste processo vão para a Admissão de Graduados: "
                    "é um indício de que a Admissão vem na sequência.</i>"]
    elif a.status == "aberta" and a.modalidade == "admissao_graduados":
        head = "🚨 <b>INSCRIÇÕES ABERTAS — ADMISSÃO DE GRADUADOS UFMT</b>"
    elif a.modalidade == "admissao_graduados":
        head = "📢 <b>Nova publicação de Admissão de Graduados — UFMT</b>"
    else:
        head = "📌 <b>Nova publicação relacionada — UFMT</b>"
    return "\n".join([head, "", *_pub_lines(pub), *note, "", "<i>Confirme datas e regras no edital oficial.</i>"])


def routine_digest_message(source: Source, pubs: list[Publication]) -> str:
    lines = [f"🗂 <b>{len(pubs)} publicação(ões) de rotina — UFMT</b>", f"<b>Fonte:</b> {esc(source.name)}", ""]
    for pub in pubs[:15]:
        lines.append(f"• {esc(pub.title)} — {esc(pub.assessment.status_label)}\n  {esc(pub.url)}")
    if len(pubs) > 15:
        lines.append(f"… e mais {len(pubs) - 15}.")
    return "\n".join(lines)


def new_cycle_message(cycle: Cycle, pubs: list[Publication]) -> str:
    if cycle.kind == "admissao_graduados":
        head = "🆕🚨 <b>NOVO CICLO DE ADMISSÃO DE GRADUADO NO SITE DA UFMT</b>"
        tail = "Esse é o sinal mais forte de que um processo novo começou. Confira o edital!"
    else:
        head = "🆕📈 <b>Novo ciclo de processo que alimenta a Admissão de Graduados</b>"
        tail = ("As vagas que sobrarem deste processo vão para a Admissão de Graduados — "
                "fique de olho, a Admissão costuma vir depois.")
    lines = [head, "", f"<b>Ciclo:</b> {esc(cycle.label)}", f"<b>Listagem:</b> {esc(cycle.url)}"]
    if pubs:
        lines += ["", "<b>Publicações do ciclo:</b>"]
        for pub in pubs:
            lines += ["", *_pub_lines(pub)]
    else:
        lines += ["", "<i>Ainda não consegui ler publicações deste ciclo; confira o link.</i>"]
    lines += ["", f"<i>{tail}</i>"]
    return "\n".join(lines)


def _record(state: dict[str, Any], pub: Publication, today: date) -> dict[str, Any]:
    w = pub.assessment.window
    rec = state["publications"].setdefault(pub.url, {"notified": []})
    rec.update({
        "title": pub.title,
        "source": pub.source.id,
        "modalidade": pub.assessment.modalidade,
        "status": pub.assessment.status,
        "vagas_cc": pub.assessment.vagas_cc,
        "confianca": pub.assessment.confidence,
        "start": w.start.isoformat() if w and w.start else None,
        "end": w.end.isoformat() if w and w.end else None,
        "first_seen": rec.get("first_seen", today.isoformat()),
    })
    if pub.assessment.status == "aberta" and "open" not in rec["notified"]:
        rec["notified"].append("open")
    return rec


class Monitor:
    def __init__(self, sources: list[Source], state: dict[str, Any], fetcher: Fetcher,
                 telegram: Telegram, today: date | None = None):
        self.sources = sources
        self.state = state
        self.fetcher = fetcher
        self.tg = telegram
        self.today = today or date.today()
        self.heartbeat_days = int(os.getenv("HEARTBEAT_DAYS", "7"))
        self.listing_checks: dict[str, Check] = {}

    def run(self) -> dict[str, int]:
        stats = {"ok": 0, "failed": 0, "new_items": 0, "alerts": 0}
        baseline_pubs: list[Publication] = []
        first_run = not self.state["sources"]
        queue = self.sources + self._followed()
        done: set[str] = set()
        while queue:
            source = queue.pop(0)
            if source.id in done:
                continue
            done.add(source.id)
            try:
                page = self.fetcher.page(source.url, source.verify_tls)
                items = listing_items(page, source) if source.kind == "listing" else page_items(page)
                page, items = self.verify(source, page, items)
                if source.kind == "listing" and len(items) < source.min_items:
                    raise RuntimeError(
                        f"só {len(items)} publicação(ões) encontradas — o layout da página pode ter mudado")
            except Exception as exc:
                stats["failed"] += 1
                self._failure(source, f"{type(exc).__name__}: {exc}")
                continue
            stats["ok"] += 1
            self._recovered(source)
            first_time = source.id not in self.state["sources"] or "items" not in self.state["sources"][source.id]
            if first_time:
                pubs = self._baseline(source, page, items)
                if first_run:
                    baseline_pubs += pubs
            else:
                stats["new_items"] += self._diff(source, page, items)
            if source.kind == "listing" and "@" not in source.id:
                stats["new_items"] += self._cycles(source, page, first_time)
                queue += [s for s in self._followed() if s.id not in done]

        stats["alerts"] += self._transitions()
        if baseline_pubs:
            self._baseline_summary(baseline_pubs)
        elif self._heartbeat_due():
            self._heartbeat(stats)
        return stats


    def _src(self, source: Source) -> dict[str, Any]:
        return self.state["sources"].setdefault(source.id, {"failures": 0, "alerted_failure": False})

    def _store(self, source: Source, items: list[Link], snippets: list[str]) -> None:
        src = self._src(source)
        known = src.setdefault("items", {})
        for link in items:
            known.setdefault(link.url, link.text)
        if len(known) > MAX_ITEMS_PER_SOURCE:
            src["items"] = dict(list(known.items())[-MAX_ITEMS_PER_SOURCE:])
        snips = src.setdefault("snippets", [])
        for s in snippets:
            if s not in snips:
                snips.append(s)
        src["snippets"] = snips[-MAX_SNIPPETS_PER_SOURCE:]

    def _baseline(self, source: Source, page: Page, items: list[Link]) -> list[Publication]:
        log.info("linha de base: %s (%d itens)", source.id, len(items))
        snippets = page_snippets(page) if source.kind == "page" else []
        self._store(source, items, snippets)
        if source.kind != "listing":
            return []
        picks = items[:BASELINE_ANALYZE]
        picks += [link for link in items[BASELINE_ANALYZE:] if is_important(link.text, "")][:1]
        pubs = [analyze(self.fetcher, link, source, self.today, self.listing_checks.get(source.id)) for link in picks]
        for pub in pubs:
            _record(self.state, pub, self.today)
        return pubs

    def _diff(self, source: Source, page: Page, items: list[Link]) -> int:
        src = self._src(source)
        known = src.get("items", {})
        new = [link for link in items if link.url not in known]
        routine: list[Publication] = []
        for link in new:
            pub = analyze(self.fetcher, link, source, self.today, self.listing_checks.get(source.id))
            _record(self.state, pub, self.today)
            if is_important(pub.title, pub.assessment.status):
                self.tg.send(new_publication_message(pub))
            else:
                routine.append(pub)
        if routine:
            self.tg.send(routine_digest_message(source, routine), silent=True)
        snippets = page_snippets(page) if source.kind == "page" else []
        old_snips = set(src.get("snippets", []))
        new_snips = [s for s in snippets if s not in old_snips]
        if new_snips:
            self.tg.send(self._snippet_message(source, new_snips))
        self._store(source, items, snippets)
        return len(new) + len(new_snips)

    def _snippet_message(self, source: Source, snippets: list[str]) -> str:
        lines = ["🔎 <b>Texto novo sobre Admissão de Graduados — UFMT</b>", "",
                 f"<b>Fonte:</b> {esc(source.name)}"]
        for s in snippets[:5]:
            status, win = overall_status(inscription_windows(s, self.today), self.today)
            extra = f" — {win.label()} ({status})" if win else ""
            lines.append(f"• {esc(s[:400])}{esc(extra)}")
        if len(snippets) > 5:
            lines.append(f"… e mais {len(snippets) - 5} trecho(s).")
        lines += [esc(source.url), "", "<i>Confirme no site oficial.</i>"]
        return "\n".join(lines)

    def verify(self, source: Source, page: Page, items: list[Link]) -> tuple[Page, list[Link]]:
        extract = (lambda pg: listing_items(pg, source)) if source.kind == "listing" else page_items
        try:
            rendered = self.fetcher.render(source.url)
        except Exception as exc:
            check = Check("HTML×navegador", None, f"navegador indisponível ({exc})"[:200])
            return page, self._record_check(source, check, items)
        rendered_items = extract(rendered)
        if source.kind == "listing" and not items and rendered_items:
            check = Check("HTML×navegador", None, "publicações só aparecem com JavaScript; lido pelo navegador")
            return rendered, self._record_check(source, check, rendered_items)
        if source.kind == "listing":
            labels = lambda pg: {c.label for c in discover_cycles(pg, source.url)}  # noqa: E731
            check = compare_listing([i.url for i in items], [i.url for i in rendered_items],
                                    labels(page), labels(rendered))
            if len(labels(rendered)) > len(labels(page)):
                page = rendered
        else:
            check = compare_text("HTML×navegador", page.text, rendered.text)
        return page, self._record_check(source, check, _dedupe(items + rendered_items))

    def _record_check(self, source: Source, check: Check, items: list[Link]) -> list[Link]:
        self.listing_checks[source.id] = check
        src = self._src(source)
        src["verification"] = check.to_dict()
        if check.ok is False:
            src["divergences"] = src.get("divergences", 0) + 1
            if src["divergences"] >= 2 and not src.get("alerted_divergence"):
                src["alerted_divergence"] = True
                self.tg.send("\n".join([
                    "⚠️ <b>Monitor UFMT: leituras divergentes</b>", "",
                    f"<b>Fonte:</b> {esc(source.name)}",
                    f"<b>HTML × navegador:</b> {esc(check.detail)}", esc(source.url), "",
                    "<i>Uso a união das duas leituras para não perder nada, mas vale conferir o site.</i>",
                ]))
        else:
            if src.get("alerted_divergence") and check.ok:
                self.tg.send(f"✅ <b>Leituras voltaram a bater:</b> {esc(source.name)}")
            src["divergences"] = 0
            src["alerted_divergence"] = False
        return items

    def _followed(self) -> list[Source]:
        out = []
        for source in self.sources:
            for url, info in self.state["sources"].get(source.id, {}).get("followed", {}).items():
                out.append(follow_source(source, Cycle(info["kind"], info["label"], url)))
        return out

    def _follow(self, source: Source, cycle: Cycle) -> None:
        followed = self._src(source).setdefault("followed", {})
        followed[cycle.url] = {"kind": cycle.kind, "label": cycle.label}
        for kind in {c["kind"] for c in followed.values()}:
            same = sorted((u for u, c in followed.items() if c["kind"] == kind),
                          key=lambda u: cycle_key(followed[u]["label"]))
            for url in same[:-2]:
                del followed[url]

    def _cycles(self, source: Source, page: Page, first_time: bool) -> int:
        src = self._src(source)
        known = src.setdefault("cycles", {})
        own = normalize_url(source.url)
        cycles = discover_cycles(page, source.url)
        labels = set(known.values())
        if any(c.label not in labels for c in cycles) and page.selects and not page.option_urls:
            try:
                cycles = discover_cycles(self.fetcher.render(source.url, discover=True), source.url)
            except Exception as exc:
                log.info("descoberta via navegador indisponível (%s); usando URL deduzida", exc)
        new = [c for c in cycles if c.url not in known and c.label not in labels and c.url != own]
        for c in cycles:
            known[c.url] = c.label
        if first_time:
            for kind in {c.kind for c in cycles}:
                newest = max((c for c in cycles if c.kind == kind), key=lambda c: cycle_key(c.label))
                if newest.url != own:
                    self._follow(source, newest)
            return 0
        for cycle in new:
            child = follow_source(source, cycle)
            pubs: list[Publication] = []
            try:
                child_items = listing_items(self.fetcher.page(cycle.url, source.verify_tls), child)
                self._store(child, child_items, [])
                pubs = [analyze(self.fetcher, link, child, self.today, self.listing_checks.get(child.id)) for link in child_items[:BASELINE_ANALYZE]]
                for pub in pubs:
                    _record(self.state, pub, self.today)
            except Exception as exc:
                log.warning("não consegui abrir a listagem do ciclo %s: %s", cycle.label, exc)
            self._follow(source, cycle)
            self.tg.send(new_cycle_message(cycle, pubs))
        return len(new)


    def _failure(self, source: Source, error: str) -> None:
        log.error("falha em %s: %s", source.id, error)
        src = self._src(source)
        src["failures"] = src.get("failures", 0) + 1
        threshold = 2 if source.critical else 4
        if src["failures"] >= threshold and not src.get("alerted_failure"):
            src["alerted_failure"] = True
            self.tg.send("\n".join([
                "⚠️ <b>Monitor UFMT: fonte com falha</b>", "",
                f"<b>Fonte:</b> {esc(source.name)}",
                f"<b>Falhas seguidas:</b> {src['failures']}",
                f"<b>Erro:</b> {esc(error[:500])}",
                esc(source.url), "",
                "<i>Enquanto isso, esta fonte NÃO está sendo monitorada. "
                "Você recebe outro aviso quando ela voltar.</i>",
            ]))

    def _recovered(self, source: Source) -> None:
        src = self._src(source)
        if src.get("alerted_failure"):
            self.tg.send(f"✅ <b>Fonte voltou a responder:</b> {esc(source.name)}")
        src["failures"] = 0
        src["alerted_failure"] = False


    def _transitions(self) -> int:
        sent = 0
        for url, rec in self.state["publications"].items():
            if rec.get("modalidade") not in ("admissao_graduados", None):
                continue
            start = date.fromisoformat(rec["start"]) if rec.get("start") else None
            end = date.fromisoformat(rec["end"]) if rec.get("end") else None
            if not (start or end):
                continue
            is_open = (not start or start <= self.today) and (not end or self.today <= end)
            notified = rec.setdefault("notified", [])
            period = " a ".join(d.strftime("%d/%m/%Y") for d in (start, end) if d)
            if is_open and "open" not in notified:
                notified.append("open")
                rec["status"] = "aberta"
                self.tg.send("\n".join([
                    "🚨 <b>INSCRIÇÕES ABERTAS — Admissão de Graduados UFMT</b>", "",
                    f"<b>Publicação:</b> {esc(rec.get('title', ''))}",
                    f"<b>Período:</b> {esc(period)}", esc(url), "",
                    "<i>Confirme no edital oficial.</i>",
                ]))
                sent += 1
            if is_open and end and (end - self.today) <= timedelta(days=CLOSING_SOON_DAYS) \
                    and "closing" not in notified:
                notified.append("closing")
                days = (end - self.today).days
                when = "HOJE é o último dia" if days == 0 else f"faltam {days} dia(s)"
                self.tg.send("\n".join([
                    f"⏰ <b>Inscrições terminando — {when}</b>", "",
                    f"<b>Publicação:</b> {esc(rec.get('title', ''))}",
                    f"<b>Encerra em:</b> {end.strftime('%d/%m/%Y')}", esc(url),
                ]))
                sent += 1
            if end and end < self.today and rec.get("status") != "encerrada":
                rec["status"] = "encerrada"
        return sent


    def _baseline_summary(self, pubs: list[Publication]) -> None:
        lines = ["✅ <b>Monitor UFMT ativado</b>", "",
                 f"Fontes configuradas: {len(self.sources)}. A partir de agora aviso sobre "
                 "publicações novas, aberturas de inscrição e falhas nas fontes.", "",
                 ]
        cycles = sorted({lbl for src in self.state["sources"].values() for lbl in src.get("cycles", {}).values()
                         if process_kind(lbl) == "admissao_graduados"}, key=cycle_key)
        if cycles:
            lines += [f"<b>Ciclos no site:</b> {esc(', '.join(cycles[-4:]))}",
                      "Aviso na hora se aparecer um ciclo novo nesse filtro.", ""]
        lines.append("<b>Publicações mais recentes encontradas:</b>")
        for pub in pubs:
            lines += ["", *_pub_lines(pub)]
        self.tg.send("\n".join(lines))
        self.state["last_heartbeat"] = self.today.isoformat()

    def _heartbeat_due(self) -> bool:
        if self.heartbeat_days <= 0:
            return False
        last = self.state.get("last_heartbeat")
        return not last or (self.today - date.fromisoformat(last)).days >= self.heartbeat_days

    def _heartbeat(self, stats: dict[str, int]) -> None:
        tracked = [r for r in self.state["publications"].values() if r.get("modalidade") == "admissao_graduados"]
        latest = max(tracked, key=lambda r: r.get("end") or r.get("start") or "", default=None)
        lines = ["💓 <b>Monitor UFMT funcionando</b>",
                 f"Fontes OK nesta execução: {stats['ok']}/{len(self.sources)}."]
        checks = list(self.listing_checks.values())
        if checks:
            confirmed = sum(1 for c in checks if c.ok)
            lines.append(f"Leituras confirmadas HTML×navegador: {confirmed}/{len(checks)}.")
        if latest:
            lines.append(f"Último processo conhecido: {esc(latest.get('title', ''))} — {esc(latest.get('status', '?'))}.")
        lines.append("Nenhuma inscrição aberta no momento." if not any(
            r.get("status") == "aberta" for r in tracked) else "Há inscrição aberta — veja os alertas anteriores.")
        self.tg.send("\n".join(lines))
        self.state["last_heartbeat"] = self.today.isoformat()
