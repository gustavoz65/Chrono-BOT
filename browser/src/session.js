import { chromium } from "playwright";

import { SUBMIT_SELECTOR, processOptions } from "./filters.js";

const USER_AGENT =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) " +
  "Chrome/126.0 Safari/537.36 UFMT-Graduados-Monitor/2.0 (uso pessoal; consultas espaçadas)";
const BLOCKED_RESOURCES = new Set(["image", "media", "font"]);
const IDLE_TIMEOUT_MS = 15000;

/**
 * Um Chromium por sessão, reaproveitado entre páginas: abrir o navegador custa
 * mais do que renderizar cada página.
 */
export class BrowserSession {
  #browser;
  #context;

  constructor(browser, context) {
    this.#browser = browser;
    this.#context = context;
  }

  static async launch({ executablePath = process.env.CHROMIUM_PATH || undefined } = {}) {
    const browser = await chromium.launch({ headless: true, executablePath });
    const context = await browser.newContext({ userAgent: USER_AGENT, locale: "pt-BR" });
    await context.route("**/*", (route) =>
      BLOCKED_RESOURCES.has(route.request().resourceType()) ? route.abort() : route.continue(),
    );
    return new BrowserSession(browser, context);
  }

  async #open(url, timeoutMs) {
    const page = await this.#context.newPage();
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: timeoutMs });
      // "networkidle" pode nunca chegar em páginas com analytics; não é motivo para falhar.
      await page.waitForLoadState("networkidle", { timeout: Math.min(timeoutMs, IDLE_TIMEOUT_MS) }).catch(() => {});
      return page;
    } catch (err) {
      await page.close();
      throw err;
    }
  }

  /**
   * @param {string} url
   * @param {{timeoutMs?: number, discoverFilters?: boolean}} [opts]
   */
  async render(url, { timeoutMs = 45000, discoverFilters = false } = {}) {
    const page = await this.#open(url, timeoutMs);
    try {
      const result = {
        url,
        finalUrl: page.url(),
        html: await page.content(),
        text: await page.evaluate(() => document.body?.innerText ?? ""),
        options: [],
      };
      if (discoverFilters) {
        result.options = await this.#discover(url, await listSelects(page), timeoutMs);
      }
      return result;
    } finally {
      await page.close();
    }
  }

  async #discover(url, selects, timeoutMs) {
    const found = [];
    for (const opt of processOptions(selects)) {
      const page = await this.#open(url, timeoutMs);
      try {
        const target = await submitFilter(page, opt.selectIndex, opt.value, timeoutMs);
        found.push({ select: opt.select, value: opt.value, label: opt.label, url: target });
      } catch (err) {
        process.stderr.write(`browser: opção "${opt.label}" falhou: ${err.message}\n`);
      } finally {
        await page.close();
      }
    }
    return found;
  }

  async close() {
    await this.#browser.close();
  }
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

async function submitFilter(page, selectIndex, value, timeoutMs) {
  const before = page.url();
  const changed = (u) => u.toString() !== before;
  const select = page.locator("select").nth(selectIndex);
  await select.selectOption(value);
  await page.waitForURL(changed, { timeout: 2000 }).catch(() => {});
  if (page.url() === before) {
    const button = page.locator(SUBMIT_SELECTOR).first();
    if (await button.count()) {
      await Promise.all([page.waitForURL(changed, { timeout: timeoutMs }).catch(() => {}), button.click()]);
    } else {
      await select.evaluate((el) => el.form && el.form.requestSubmit());
      await page.waitForURL(changed, { timeout: timeoutMs }).catch(() => {});
    }
  }
  await page.waitForLoadState("networkidle", { timeout: Math.min(timeoutMs, IDLE_TIMEOUT_MS) }).catch(() => {});
  return page.url();
}
