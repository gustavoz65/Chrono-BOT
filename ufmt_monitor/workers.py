from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from .config import ROOT
from .gen.ufmt.v1 import worker_pb2 as pb

log = logging.getLogger(__name__)


class WorkerError(RuntimeError):
    def __init__(self, worker: str, message: str, code: int = 0):
        super().__init__(f"{worker}: {message}")
        self.code = code


def call(worker: str, cmd: list[str], request: pb.Request, timeout: float, cwd: Path | None = None,
         env: dict[str, str] | None = None) -> pb.Response:
    try:
        proc = subprocess.run(cmd, input=request.SerializeToString(), capture_output=True,
                              timeout=timeout, cwd=cwd, env=env)
    except FileNotFoundError as exc:
        raise WorkerError(worker, f"executável não encontrado ({exc.filename})") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkerError(worker, f"sem resposta em {timeout:.0f}s") from exc
    if proc.stderr:
        log.debug("%s stderr: %s", worker, proc.stderr.decode(errors="replace")[-2000:])
    if proc.returncode != 0:
        raise WorkerError(worker, f"saiu com código {proc.returncode}: "
                                  f"{proc.stderr.decode(errors='replace')[-300:]}")
    response = pb.Response()
    response.ParseFromString(proc.stdout)
    if response.HasField("error"):
        raise WorkerError(worker, response.error.message, response.error.code)
    return response


def binary(env: str, default: Path) -> Path:
    return Path(os.getenv(env, default))


OCR_BIN = binary("OCR_BIN", ROOT / "ocr" / "bin" / "ufmt-ocr")
AI_BIN = binary("AI_BIN", ROOT / "ai" / "bin" / "ufmt-ai")
