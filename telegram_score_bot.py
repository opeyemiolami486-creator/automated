#!/usr/bin/env python3
"""Telegram control bot for the authorized local Webcade-style test API.

Commands:
  /start       Show help
  /identity X  Save the public wallet address or username for this chat
  /status      Read the local leaderboard
  /run         Request a token and submit a higher local test score
  /clear       Remove the saved identity for this chat

Set TELEGRAM_BOT_TOKEN and keep the bot restricted to your own test chat.
This bot only permits a localhost MOCK_BASE_URL by default.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp

from mock_score_automation import submit_higher_score

LOG = logging.getLogger("telegram_score_bot")
STATE_FILE = Path(os.getenv("TELEGRAM_STATE_FILE", "telegram_bot_state.json"))
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = os.getenv("MOCK_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_ALLOWED_CHAT_ID")
MIN_HEIGHT = int(os.getenv("MOCK_MIN_HEIGHT", "1900"))
MAX_HEIGHT = int(os.getenv("MOCK_MAX_HEIGHT", "2500"))
INCREMENT = int(os.getenv("MOCK_SCORE_INCREMENT", "50000"))
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"identities": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"identities": {}}
    except (OSError, json.JSONDecodeError):
        return {"identities": {}}


def save_state(state: dict[str, Any]) -> None:
    temp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


async def telegram_call(session: aiohttp.ClientSession, method: str, params: dict[str, Any]) -> Any:
    async with session.post(f"{API}/{method}", data=params) as response:
        data = await response.json(content_type=None)
        if response.status >= 400 or not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data}")
        return data.get("result")


async def send_message(session: aiohttp.ClientSession, chat_id: str, text: str) -> None:
    await telegram_call(session, "sendMessage", {"chat_id": chat_id, "text": text})


def allowed(chat_id: str) -> bool:
    return not ALLOWED_CHAT_ID or str(chat_id) == str(ALLOWED_CHAT_ID)


async def handle_update(session: aiohttp.ClientSession, state: dict[str, Any], update: dict[str, Any]) -> None:
    message = update.get("message") or update.get("edited_message")
    if not message or not message.get("text"):
        return
    chat_id = str((message.get("chat") or {}).get("id"))
    if not allowed(chat_id):
        LOG.warning("Ignored message from unauthorized chat %s", chat_id)
        return
    text = str(message["text"]).strip()
    command, _, argument = text.partition(" ")
    command = command.split("@", 1)[0].lower()
    identities = state.setdefault("identities", {})

    if command in {"/start", "/help"}:
        await send_message(session, chat_id, "Commands:\n/identity <wallet or username>\n/status\n/run\n/clear")
    elif command == "/identity":
        value = argument.strip()
        if not value:
            await send_message(session, chat_id, "Usage: /identity <public wallet address or username>")
        else:
            identities[chat_id] = value
            save_state(state)
            await send_message(session, chat_id, f"Saved test identity: {value}")
    elif command == "/clear":
        identities.pop(chat_id, None)
        save_state(state)
        await send_message(session, chat_id, "Saved identity cleared.")
    elif command == "/status":
        async with session.get(f"{BASE_URL}/api/dudas/board?limit=5&window=today") as response:
            response.raise_for_status()
            board = await response.json()
        rows = board.get("list", [])
        if not rows:
            await send_message(session, chat_id, "Local test leaderboard is empty.")
        else:
            lines = [f"Local test leaderboard ({len(rows)} shown):"]
            lines.extend(f"#{r.get('rank')} {r.get('name')} — {r.get('score')} score, {r.get('height')}m" for r in rows)
            await send_message(session, chat_id, "\n".join(lines))
    elif command == "/run":
        identity = identities.get(chat_id)
        if not identity:
            await send_message(session, chat_id, "No identity saved. Send /identity <public wallet address or username> first.")
            return
        await send_message(session, chat_id, "Starting a local test run and requesting a fresh server token…")
        result = await submit_higher_score(BASE_URL, identity, None, INCREMENT, MIN_HEIGHT, MAX_HEIGHT)
        submitted = result["submitted"]
        outcome = result["result"]
        await send_message(
            session,
            chat_id,
            f"Submitted to local test API.\nIdentity: {identity}\n"
            f"Previous top: {result['previous_top_score']}\n"
            f"New score: {submitted['score']} (+at least {INCREMENT})\n"
            f"Height: {submitted['height']}m\nRank: {outcome.get('rank')}",
        )
    else:
        await send_message(session, chat_id, "Unknown command. Use /start for help.")


async def run_bot() -> None:
    state = load_state()
    offset = 0
    timeout = aiohttp.ClientTimeout(total=40)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await telegram_call(session, "deleteWebhook", {"drop_pending_updates": "true"})
        LOG.info("Telegram bot active; local API=%s", BASE_URL)
        while True:
            params = {"timeout": 30, "offset": offset, "allowed_updates": json.dumps(["message"])}
            try:
                updates = await telegram_call(session, "getUpdates", params)
                for update in updates or []:
                    offset = max(offset, int(update["update_id"]) + 1)
                    try:
                        await handle_update(session, state, update)
                    except Exception:
                        LOG.exception("Update failed")
                        chat_id = str(((update.get("message") or {}).get("chat") or {}).get("id", ""))
                        if chat_id and allowed(chat_id):
                            await send_message(session, chat_id, "Run failed; check the bot log and test API.")
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.exception("Telegram polling failed; retrying")
                await asyncio.sleep(3)


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_bot())
