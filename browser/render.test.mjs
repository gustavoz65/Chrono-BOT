import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const run = promisify(execFile);
const script = fileURLToPath(new URL("./render.mjs", import.meta.url));

const CICLOS = { 244: "Admissão de Graduado 2026/1", 251: "Admissão de Graduado 2026/2" };

function pagina(category) {
  const options = Object.entries(CICLOS)
    .map(([v, l]) => `<option value="${v}" ${v === category ? "selected" : ""}>${l}</option>`)
    .join("");
  return `<!doctype html><html><head><meta charset="utf-8"><title>Ingresso</title></head><body>
    <form method="get" action="/publicacoes">
      <input type="hidden" name="id" value="61">
      <select name="category"><option value="">Outros</option>${options}</select>
      <button type="submit">Buscar</button>
    </form>
    <div id="lista"></div>
    <script>
      setTimeout(() => {
        document.getElementById("lista").innerHTML =
          '<a class="card" href="/publicacao/${category}01"><div>22/10/2026 10:00</div><div>Edital ${CICLOS[category]}</div></a>';
      }, 300);
    </script></body></html>`;
}

let server;
let base;

before(async () => {
  server = createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(pagina(url.searchParams.get("category") || "244"));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});

after(() => server.close());

test("renderiza conteúdo montado por JavaScript", async () => {
  const { stdout } = await run("node", [script, `${base}/publicacoes?id=61&category=244&page=1`]);
  const out = JSON.parse(stdout);
  assert.match(out.html, /Edital Admissão de Graduado 2026\/1/);
  assert.deepEqual(out.options, []);
});

test("--discover descobre a URL de cada ciclo do filtro", async () => {
  const { stdout } = await run("node", [script, `${base}/publicacoes?id=61&category=244&page=1`, "--discover"]);
  const out = JSON.parse(stdout);
  const byLabel = Object.fromEntries(out.options.map((o) => [o.label, new URL(o.url)]));
  assert.equal(Object.keys(byLabel).length, 2); // a opção "Outros" é ignorada
  assert.equal(byLabel["Admissão de Graduado 2026/2"].searchParams.get("category"), "251");
  assert.equal(byLabel["Admissão de Graduado 2026/1"].searchParams.get("category"), "244");
});

test("URL inválida sai com erro", async () => {
  await assert.rejects(run("node", [script, "nao-e-url"]), /informe uma URL/);
});
