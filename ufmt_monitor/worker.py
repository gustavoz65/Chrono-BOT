from __future__ import annotations

import logging
import os
import sys
from datetime import date

from google.protobuf.message import DecodeError

from .classify import assess
from .config import today as local_today
from .dates import parse_dates
from .extract import Fetcher, host_allowed
from .gen.ufmt.v1 import worker_pb2 as pb

RESULT = {True: "ok", False: "falhou", None: "nao_executada"}


def allowed_hosts() -> list[str]:
    # Mesma lista do agente Go: quem pede FetchRequest é a IA, que lê texto de terceiros.
    raw = os.getenv("AI_ALLOWED_HOSTS", "")
    return [h.strip().lower() for h in raw.split(",") if h.strip()] or ["ufmt.br"]


def _error(code: int, message: str) -> pb.Response:
    return pb.Response(error=pb.Error(code=code, message=message))


def _extract(req: pb.ExtractRequest) -> pb.Response:
    try:
        today = date.fromisoformat(req.today) if req.today else local_today()
    except ValueError:
        return _error(pb.Error.CODE_INVALID_REQUEST, f"today inválido: {req.today!r}")
    a = assess(req.title, req.text, today)
    windows = [pb.InscriptionWindow(start=w.start.isoformat() if w.start else "",
                                    end=w.end.isoformat() if w.end else "",
                                    status=w.status(today), context=w.context[:300]) for w in a.windows]
    return pb.Response(extract=pb.ExtractResponse(
        modalidade=a.modalidade, status=a.status, windows=windows,
        dates=[d.isoformat() for d in parse_dates(req.text, today.year)],
        computacao=a.computacao, computacao_cuiaba=a.computacao_cuiaba,
        vagas_cc=a.vagas_cc if a.vagas_cc is not None else -1,
    ))


def _fetch(req: pb.FetchRequest, fetcher: Fetcher) -> pb.Response:
    if not req.url.startswith(("http://", "https://")):
        return _error(pb.Error.CODE_INVALID_REQUEST, "FetchRequest.url precisa ser http(s)")
    if not host_allowed(req.url, allowed_hosts()):
        return _error(pb.Error.CODE_INVALID_REQUEST, f"domínio não permitido: {req.url}")
    try:
        doc = fetcher.document(req.url)
    except Exception as exc:
        return _error(pb.Error.CODE_UNAVAILABLE, f"{type(exc).__name__}: {exc}")
    checks = [pb.Check(name=c.name, result=RESULT[c.ok], detail=c.detail) for c in doc.checks]
    return pb.Response(fetch=pb.FetchResponse(text=doc.text, sources=doc.sources, checks=checks))


def handle(data: bytes, fetcher: Fetcher | None = None) -> bytes:
    request = pb.Request()
    try:
        request.ParseFromString(data)
    except DecodeError as exc:
        return _error(pb.Error.CODE_INVALID_REQUEST, f"Request protobuf inválido: {exc}").SerializeToString()
    kind = request.WhichOneof("payload")
    if kind == "extract":
        response = _extract(request.extract)
    elif kind == "fetch":
        response = _fetch(request.fetch, fetcher or Fetcher(allowed_hosts=allowed_hosts()))
    else:
        response = _error(pb.Error.CODE_UNSUPPORTED, "este worker atende ExtractRequest e FetchRequest")
    return response.SerializeToString()


def main() -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    sys.stdout.buffer.write(handle(sys.stdin.buffer.read()))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
