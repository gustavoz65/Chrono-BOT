from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

from . import browser, llm, ocr, state
from .classify import assess
from .config import load_sources
from .extract import Fetcher
from .monitor import (BASELINE_ANALYZE, Monitor, analyze, discover_cycles, listing_items, page_items,
                      page_snippets)
from .notify import Telegram, esc
from .verify import merge, summary


def cmd_run(args) -> int:
    st = state.load(args.state)
    monitor = Monitor(load_sources(args.sources), st, Fetcher(), Telegram(dry_run=args.dry_run))
    stats = monitor.run()
    state.save(st, args.state)
    print(f"Concluído: {stats}")
    return 0


def cmd_check(args) -> int:
    fetcher, today = Fetcher(), date.today()
    report = ["# Validação das fontes UFMT", f"Data: {today.isoformat()}",
              f"Navegador: {'sim' if browser.available() else 'não'} · OCR: {'sim' if ocr.available() else 'não'}"
              f" · Groq: {'sim' if llm.enabled() else 'não'}", ""]
    critical_failed = False
    for source in load_sources(args.sources):
        report.append(f"## {source.name}\n`{source.url}` — tipo `{source.kind}`")
        try:
            page = fetcher.page(source.url, source.verify_tls)
        except Exception as exc:
            critical_failed |= source.critical
            report.append(f"- ❌ **FALHA**: `{type(exc).__name__}: {exc}`\n")
            continue
        items = listing_items(page, source) if source.kind == "listing" else page_items(page)
        checker = Monitor([source], state.empty(), fetcher, Telegram(dry_run=True), today)
        page, items = checker.verify(source, page, items)
        check = checker.listing_checks[source.id]
        snippets = page_snippets(page)
        report.append(f"- ✅ HTTP {page.status_code} · título: {page.title!r} · {len(page.text)} caracteres")
        report.append(f"- Publicações/links considerados: **{len(items)}** · trechos sobre graduados: {len(snippets)}")
        report.append(f"- Verificação {check.name}: {check.icon} {check.detail}")
        if source.kind == "listing" and len(items) < source.min_items:
            critical_failed |= source.critical
            report.append("- ⚠️ Nenhuma publicação reconhecida — ajuste `item_pattern` em sources.toml")
        for link in items[:10]:
            report.append(f"  - {link.text[:120]} → {link.url}")
        for s in snippets[:3]:
            report.append(f"  - trecho: _{s[:200]}_")
        if source.kind == "listing":
            cycles = discover_cycles(page, source.url)
            if browser.available():
                try:
                    rendered = fetcher.render(source.url, discover=True)
                    cycles = discover_cycles(rendered, source.url)
                except Exception as exc:
                    report.append(f"- ⚠️ Navegador falhou: `{exc}`")
            report.append(f"- Ciclos encontrados (filtro \"Ano\" + menu): {len(cycles)}")
            for c in cycles:
                report.append(f"  - [{c.kind}] {c.label} → {c.url}")
            report.append(f"\n**Análise das {BASELINE_ANALYZE} publicações mais recentes:**")
            for link in items[:BASELINE_ANALYZE]:
                pub = analyze(fetcher, link, source, today, check)
                a = pub.assessment
                win = a.window.label() if a.window else "datas não identificadas"
                vagas = f" · vagas CC Cuiabá: {a.vagas_cc}" if a.vagas_cc is not None else ""
                report.append(f"  - **{a.status_label}** · {pub.title[:100]} · inscrições: {win} "
                              f"· {a.modalidade_label}{vagas} · lido de {len(pub.read_from)} doc(s) "
                              f"· confiança {a.confidence} ({summary(a.checks)})"
                              + (f" · erro: {pub.error}" if pub.error else ""))
        report.append("")
    text = "\n".join(report)
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    if args.telegram:
        Telegram().send("🧪 <b>Validação das fontes UFMT</b>\n\n" + esc(text[:3500]))
    return 1 if critical_failed else 0


def cmd_analyze(args) -> int:
    today = date.fromisoformat(args.today) if args.today else date.today()
    doc = Fetcher().document(args.url)
    heuristic = assess("", doc.text, today)
    heuristic.checks = doc.checks
    result = llm.refine("", doc.text, heuristic, today, args.url)
    print(f"Lido de: {doc.sources}")
    print(f"Confiança: {result.confidence}")
    for c in merge(result.checks):
        print(f"  {c.icon} {c.name}: {c.detail}")
    print(f"Modalidade: {result.modalidade_label}\nStatus: {result.status_label}")
    print(f"Período principal: {result.window.label() if result.window else '-'}")
    for w in result.windows:
        print(f"  - {w.label()} [{w.status(today)}] ← {w.context[:120]!r}")
    print(f"Ciência da Computação: {result.computacao} · em Cuiabá: {result.computacao_cuiaba}"
          f" · vagas: {result.vagas_cc if result.vagas_cc is not None else '?'}")
    if result.resumo:
        print(f"Resumo: {result.resumo}")
    return 0


def cmd_test_telegram(args) -> int:
    tg = Telegram()
    if tg.dry_run:
        print("Defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID para testar o envio.", file=sys.stderr)
        return 1
    tg.send("🤖 <b>Teste do monitor UFMT</b>\nSe você recebeu isto, as notificações estão funcionando.")
    print("Mensagem enviada.")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="ufmt_monitor", description="Monitor de Admissão de Graduados da UFMT")
    parser.add_argument("--sources", default=None, help="caminho do sources.toml")
    parser.add_argument("--state", default=state.DEFAULT_PATH, help="caminho do state.json")
    sub = parser.add_subparsers(dest="cmd")
    p_run = sub.add_parser("run", help="execução normal (padrão)")
    p_run.add_argument("--dry-run", action="store_true", help="não envia nada ao Telegram")
    p_check = sub.add_parser("check", help="valida as fontes e mostra o que foi extraído")
    p_check.add_argument("--telegram", action="store_true", help="envia o relatório ao Telegram")
    p_an = sub.add_parser("analyze", help="analisa uma publicação/edital específico")
    p_an.add_argument("url")
    p_an.add_argument("--today", help="simula outra data (AAAA-MM-DD)")
    sub.add_parser("test-telegram", help="envia uma mensagem de teste")
    args = parser.parse_args(argv)
    handlers = {"check": cmd_check, "analyze": cmd_analyze, "test-telegram": cmd_test_telegram}
    if args.cmd in handlers:
        return handlers[args.cmd](args)
    if args.cmd is None:
        args.dry_run = False
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
