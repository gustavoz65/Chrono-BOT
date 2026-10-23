import { test } from "node:test";
import assert from "node:assert/strict";
import { create, fromBinary, toBinary } from "@bufbuild/protobuf";

import { handleRequest } from "../src/worker.js";
import {
  Error_Code,
  OcrRequestSchema,
  RenderRequestSchema,
  RequestSchema,
  ResponseSchema,
} from "../src/gen/ufmt/v1/worker_pb.js";

class FakeSession {
  closed = false;
  async render(url, opts) {
    if (url.includes("quebrada")) throw new Error("net::ERR_NAME_NOT_RESOLVED");
    return {
      url,
      finalUrl: url + "#ok",
      html: "<html></html>",
      text: "Edital",
      options: opts.discoverFilters ? [{ select: "category", value: "251", label: "Admissão de Graduado 2026/2", url: url + "?c=251" }] : [],
    };
  }
  async close() {
    this.closed = true;
  }
}

const encode = (payload) => toBinary(RequestSchema, create(RequestSchema, { payload }));
const decode = (bytes) => fromBinary(ResponseSchema, bytes);

test("renderiza várias urls e isola o erro de cada página", async () => {
  const session = new FakeSession();
  const input = encode({
    case: "render",
    value: create(RenderRequestSchema, { urls: ["https://a/ok", "https://a/quebrada"], discoverFilters: true }),
  });
  const res = decode(await handleRequest(input, async () => session));
  assert.equal(res.payload.case, "render");
  const [ok, broken] = res.payload.value.pages;
  assert.equal(ok.finalUrl, "https://a/ok#ok");
  assert.equal(ok.options[0].label, "Admissão de Graduado 2026/2");
  assert.match(broken.error, /ERR_NAME_NOT_RESOLVED/);
  assert.ok(session.closed, "a sessão precisa ser fechada");
});

test("recusa payload de outro worker", async () => {
  const res = decode(await handleRequest(encode({ case: "ocr", value: create(OcrRequestSchema, {}) }), async () => new FakeSession()));
  assert.equal(res.error.code, Error_Code.UNSUPPORTED);
});

test("recusa protobuf inválido e request vazio", async () => {
  let res = decode(await handleRequest(new Uint8Array([0xff, 0xff, 0xff]), async () => new FakeSession()));
  assert.equal(res.error.code, Error_Code.INVALID_REQUEST);
  res = decode(await handleRequest(encode({ case: "render", value: create(RenderRequestSchema, {}) }), async () => new FakeSession()));
  assert.equal(res.error.code, Error_Code.INVALID_REQUEST);
});

test("navegador que não abre vira UNAVAILABLE", async () => {
  const input = encode({ case: "render", value: create(RenderRequestSchema, { urls: ["https://a"] }) });
  const res = decode(await handleRequest(input, async () => { throw new Error("chromium ausente"); }));
  assert.equal(res.error.code, Error_Code.UNAVAILABLE);
  assert.match(res.error.message, /chromium ausente/);
});
