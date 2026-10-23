#!/usr/bin/env node
import { buffer } from "node:stream/consumers";

import { BrowserSession } from "./session.js";
import { handleRequest } from "./worker.js";

const USAGE = `uso: ufmt-browser --worker                  (Request protobuf no stdin, Response no stdout)
     ufmt-browser render <url> [--discover]  (resumo em JSON no stdout)`;

async function main(argv) {
  if (argv[0] === "--worker") {
    const output = await handleRequest(new Uint8Array(await buffer(process.stdin)), () => BrowserSession.launch());
    process.stdout.write(output);
    return 0;
  }
  if (argv[0] === "render" && argv[1]) {
    const session = await BrowserSession.launch();
    try {
      const page = await session.render(argv[1], { discoverFilters: argv.includes("--discover") });
      const { html, ...summary } = page;
      process.stdout.write(JSON.stringify({ ...summary, htmlLength: html.length, text: page.text.slice(0, 2000) }, null, 2) + "\n");
      return 0;
    } finally {
      await session.close();
    }
  }
  process.stderr.write(USAGE + "\n");
  return 2;
}

main(process.argv.slice(2)).then(
  (code) => (process.exitCode = code),
  (err) => {
    process.stderr.write(`ufmt-browser: ${err.message}\n`);
    process.exitCode = 1;
  },
);
