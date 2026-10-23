import { test } from "node:test";
import assert from "node:assert/strict";

import { isProcessOption, processOptions } from "../src/filters.js";

test("reconhece opções de processos de ingresso", () => {
  assert.ok(isProcessOption({ value: "251", label: "Admissão de Graduado 2026/2" }));
  assert.ok(isProcessOption({ value: "240", label: "Transferência Facultativa 2026/1" }));
  assert.ok(isProcessOption({ value: "238", label: "Vagas Remanescentes 2026/1" }));
  assert.ok(!isProcessOption({ value: "1", label: "SISU 2026" }));
  assert.ok(!isProcessOption({ value: "", label: "Admissão de Graduado" }));
});

test("processOptions mantém o índice do select de origem", () => {
  const out = processOptions([
    { index: 0, name: "ordem", options: [{ value: "asc", label: "Mais antigos" }] },
    { index: 1, name: "category", options: [{ value: "244", label: "Admissão de Graduado 2026/1" }] },
  ]);
  assert.deepEqual(out, [{ selectIndex: 1, select: "category", value: "244", label: "Admissão de Graduado 2026/1" }]);
});
