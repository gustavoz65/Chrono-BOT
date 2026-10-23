import { create, fromBinary, toBinary } from "@bufbuild/protobuf";

import {
  Error_Code,
  ErrorSchema,
  FilterOptionSchema,
  RenderedPageSchema,
  RenderResponseSchema,
  RequestSchema,
  ResponseSchema,
} from "./gen/ufmt/v1/worker_pb.js";

const DEFAULT_TIMEOUT_MS = 45000;

function errorResponse(code, message) {
  return toBinary(ResponseSchema, create(ResponseSchema, { error: create(ErrorSchema, { code, message }) }));
}

/**
 * Atende um ufmt.v1.Request serializado e devolve o Response serializado.
 * @param {Uint8Array} input
 * @param {() => Promise<{render: Function, close: Function}>} openSession
 */
export async function handleRequest(input, openSession) {
  let request;
  try {
    request = fromBinary(RequestSchema, input);
  } catch (err) {
    return errorResponse(Error_Code.INVALID_REQUEST, `Request protobuf inválido: ${err.message}`);
  }
  if (request.payload.case !== "render") {
    return errorResponse(Error_Code.UNSUPPORTED, "este worker só atende RenderRequest");
  }
  const { urls, discoverFilters, timeoutMs } = request.payload.value;
  if (urls.length === 0) {
    return errorResponse(Error_Code.INVALID_REQUEST, "RenderRequest sem urls");
  }

  let session;
  try {
    session = await openSession();
  } catch (err) {
    return errorResponse(Error_Code.UNAVAILABLE, `navegador indisponível: ${err.message}`);
  }
  try {
    const pages = [];
    for (const url of urls) {
      try {
        const page = await session.render(url, { timeoutMs: timeoutMs || DEFAULT_TIMEOUT_MS, discoverFilters });
        pages.push(
          create(RenderedPageSchema, {
            ...page,
            options: page.options.map((o) => create(FilterOptionSchema, o)),
          }),
        );
      } catch (err) {
        pages.push(create(RenderedPageSchema, { url, error: err.message }));
      }
    }
    const response = create(ResponseSchema, {
      payload: { case: "render", value: create(RenderResponseSchema, { pages }) },
    });
    return toBinary(ResponseSchema, response);
  } finally {
    await session.close();
  }
}
