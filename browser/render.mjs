#!/usr/bin/env node
// node render.mjs <url> [--discover] [--timeout <ms>]  ->  JSON {url, html, options} no stdout

import { chromium } from "playwright";

const PROCESS_RE = /graduad|transfer[eê]ncia\s+facultativa|vagas\s+remanescentes/i;
const USER_AGENT =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) " +
  "Chrome/126.0 Safari/537.36 UFMT-Graduados-Monitor/2.0 (uso pessoal; consultas espaçadas)";

function parseArgs(argv) {
  const args = { url: "", discover: false, timeout: 45000 };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--discover") args.discover = true;
    else if (arg === "--timeout") args.timeout = Number(argv[++i]);
    else if (!args.url) args.url = arg;
    else throw new Error(`argumento desconhecido: ${arg}`);
  }
  if (!/^(https?|file):\/\//.test(args.url)) throw new Error("informe uma URL http(s)");
  if (!Number.isFinite(args.timeout) || args.timeout <= 0) throw new Error("--timeout inválido");
  return args;
}

async function openPage(context, url, timeout) {
  const page = await context.newPage();
  await page.goto(url, { waitUntil: "domcontentloaded", timeout });
  // "networkidle" pode nunca chegar em páginas com analytics; não é motivo para falhar.
  await page.waitForLoadState("networkidle", { timeout: Math.min(timeout, 15000) }).catch(() => {});
  return page;
}

async function listSelects(page) {
  return page.$$eval("select", (els) =>
    els.map((el, index) => ({
      index,
      name: el.name || el.id || "",
      options: Array.from(el.options).map((o) => ({ value: o.value, label: o.textContent.trim() })),
    })),
  );
}

async function submitFilter(page, selectIndex, value, timeout) {
  const before = page.url();
  const select = page.locator("select").nth(selectIndex);
  await select.selectOption(value);
  await page.waitForURL((u) => u.toString() !== before, { timeout: 2000 }).catch(() => {});
  if (page.url() === before) {
    const button = page
      .locator('button[type="submit"], input[type="submit"], button:has-text("Buscar")')
      .first();
    if (await button.count()) {
      await Promise.all([
        page.waitForURL((u) => u.toString() !== before, { timeout }).catch(() => {}),
        button.click(),
      ]);
    } else {
      await select.evaluate((el) => el.form && el.form.requestSubmit());
      await page.waitForURL((u) => u.toString() !== before, { timeout }).catch(() => {});
    }
  }
  await page.waitForLoadState("networkidle", { timeout: Math.min(timeout, 15000) }).catch(() => {});
  return page.url();
}

async function discoverOptions(context, url, selects, timeout) {
  const found = [];
  for (const sel of selects) {
    for (const opt of sel.options) {
      if (!opt.value || !PROCESS_RE.test(opt.label)) continue;
      const page = await openPage(context, url, timeout);
      try {
        const target = await submitFilter(page, sel.index, opt.value, timeout);
        found.push({ select: sel.name, value: opt.value, label: opt.label, url: target });
      } catch (err) {
        process.stderr.write(`render: opção "${opt.label}" falhou: ${err.message}\n`);
      } finally {
        await page.close();
      }
    }
  }
  return found;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH || undefined,
  });
  try {
    const context = await browser.newContext({ userAgent: USER_AGENT, locale: "pt-BR" });
    const page = await openPage(context, args.url, args.timeout);
    const result = { url: page.url(), html: await page.content(), options: [] };
    if (args.discover) {
      const selects = await listSelects(page);
      result.options = await discoverOptions(context, args.url, selects, args.timeout);
    }
    process.stdout.write(JSON.stringify(result));
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  process.stderr.write(`render: ${err.message}\n`);
  process.exit(1);
});
