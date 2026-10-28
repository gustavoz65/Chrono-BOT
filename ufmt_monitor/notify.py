from __future__ import annotations

import html
import logging
import os
import time

import requests

log = logging.getLogger(__name__)
LIMIT = 4000


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _chunks(message: str) -> list[str]:
    if len(message) <= LIMIT:
        return [message]
    parts, current = [], ""
    for line in message.split("\n"):
        if len(current) + len(line) + 1 > LIMIT and current:
            parts.append(current)
            current = ""
        current += line[:LIMIT] + "\n"
    if current:
        parts.append(current)
    return parts


class Telegram:
    def __init__(self, token: str | None = None, chat_ids: str | None = None, dry_run: bool = False):
        self.token = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
        raw = chat_ids if chat_ids is not None else os.getenv("TELEGRAM_CHAT_ID", "")
        self.chat_ids = [c.strip() for c in raw.split(",") if c.strip()]
        self.dry_run = dry_run or not (self.token and self.chat_ids)
        self.sent: list[str] = []
        self.silent_sent: list[str] = []
        self.errors: list[str] = []

    def send(self, message: str, silent: bool = False) -> None:
        self.sent.append(message)
        if silent:
            self.silent_sent.append(message)
        if self.dry_run:
            print("— [Telegram não configurado / dry-run] mensagem que seria enviada —\n" + message + "\n")
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        # Um chat inválido ou o Telegram fora do ar não pode abortar a execução: o estado deixaria de
        # ser salvo e tudo seria reprocessado (e reenviado aos outros chats) na próxima.
        for chat_id in self.chat_ids:
            try:
                for part in _chunks(message):
                    self._post(url, chat_id, part, silent)
            except Exception as exc:
                # Nunca logar a URL com o token.
                error = str(exc).replace(self.token, "***")
                log.error("Telegram: falha ao enviar para %s: %s", chat_id, error)
                self.errors.append(f"{chat_id}: {error}")

    def _post(self, url: str, chat_id: str, text: str, silent: bool) -> None:
        for _ in range(4):
            resp = requests.post(url, json={
                "chat_id": chat_id, "text": text, "parse_mode": "HTML",
                "disable_web_page_preview": True, "disable_notification": silent,
            }, timeout=30)
            if resp.status_code == 429:
                try:
                    retry = int(resp.json().get("parameters", {}).get("retry_after", 5))
                except (ValueError, AttributeError):
                    retry = 5
                time.sleep(min(retry, 60))
                continue
            if not resp.ok:
                raise RuntimeError(f"Telegram respondeu {resp.status_code}: {resp.text[:300]}")
            return
        raise RuntimeError("Telegram: limite de envio excedido (429)")
