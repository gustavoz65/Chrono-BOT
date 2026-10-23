import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";

import { BrowserSession } from "../src/session.js";
import { RenderRequestSchema, RequestSchema, ResponseSchema } from "../src/gen/ufmt/v1/worker_pb.js";

const CICLOS = { 244: "Admissão de Graduado 2026/1", 251: "Admissão de Graduado 2026/2" };
const cli = fileURLToPath(new URL("../src/cli.js", import.meta.url));

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
let session;

before(async () => {
  server = createServer((req, res) => {
    const url = new URL(req.url, "http://localhost");
    res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
    res.end(pagina(url.searchParams.get("category") || "244"));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  session = await BrowserSession.launch();
});

after(async () => {
  await session?.close();
  server.close();
});

test("renderiza conteúdo montado por JavaScript", async () => {
  const page = await session.render(`${base}/publicacoes?id=61&category=244&page=1`);
  assert.match(page.html, /Edital Admissão de Graduado 2026\/1/);
  assert.match(page.text, /Edital Admissão de Graduado 2026\/1/);
  assert.deepEqual(page.options, []);
});

test("descobre a URL real de cada ciclo do filtro", async () => {
  const page = await session.render(`${base}/publicacoes?id=61&category=244&page=1`, { discoverFilters: true });
  const byLabel = Object.fromEntries(page.options.map((o) => [o.label, new URL(o.url)]));
  assert.equal(Object.keys(byLabel).length, 2);
  assert.equal(byLabel["Admissão de Graduado 2026/2"].searchParams.get("category"), "251");
});

test("CLI --worker fala protobuf de ponta a ponta", async () => {
  const input = toBinary(RequestSchema, create(RequestSchema, {
    payload: { case: "render", value: create(RenderRequestSchema, { urls: [`${base}/publicacoes?category=251`] }) },
  }));
  const stdout = await new Promise((resolve, reject) => {
    const child = execFile("node", [cli, "--worker"], { encoding: "buffer" }, (err, out) => (err ? reject(err) : resolve(out)));
    child.stdin.end(Buffer.from(input));
  });
  const res = fromBinary(ResponseSchema, new Uint8Array(stdout));
  assert.match(res.payload.value.pages[0].text, /2026\/2/);
});
