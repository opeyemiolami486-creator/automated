#!/usr/bin/env python3
"""Telegram control bot for explicitly authorized team test sites.

Commands:
  /start
  /site <authorized test URL>
  /inspect
  /identity <public wallet address or username>
  /status
  /on
  /off
  /run
  /clear

The selected site may expose a requirements contract and must be allowlisted by
AUTHORIZED_TEST_DOMAINS. Sites without a contract use the conventional Dudas
API defaults. The default remains the local mock API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import aiohttp

from authorized_test_client import allowed_host, inspect_site, run_site
from site_code_discovery import discover
from site_contract import validate_contract

LOG = logging.getLogger("telegram_score_bot")
STATE_FILE = Path(os.getenv("TELEGRAM_STATE_FILE", "telegram_bot_state.json"))
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BASE_URL = (os.getenv("MOCK_BASE_URL") or "http://127.0.0.1:8080").rstrip("/")
ALLOWED_CHAT_ID = os.getenv("TELEGRAM_ALLOWED_CHAT_ID")
MIN_HEIGHT = int(os.getenv("MOCK_MIN_HEIGHT", "1900"))
MAX_HEIGHT = int(os.getenv("MOCK_MAX_HEIGHT", "2500"))
INCREMENT = int(os.getenv("MOCK_SCORE_INCREMENT", "50000"))
ACTIVE_INTERVAL = max(0.5, float(os.getenv("ACTIVE_INTERVAL_SECONDS", "0.5")))
API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"identities": {}, "sites": {}, "active": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"identities": {}, "sites": {}, "active": {}}
    except (OSError, json.JSONDecodeError):
        return {"identities": {}, "sites": {}, "active": {}}


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


def site_for(state: dict[str, Any], chat_id: str) -> str:
    value = str(state.setdefault("sites", {}).get(chat_id) or BASE_URL).strip()
    return value.rstrip("/") or BASE_URL


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
    sites = state.setdefault("sites", {})
    active = state.setdefault("active", {})

    if command in {"/start", "/help"}:
        await send_message(session, chat_id, "Commands:\n/site <authorized test URL>\n/discover\n/inspect\n/identity <wallet or username>\n/status\n/on\n/off\n/run\n/clear")
    elif command == "/site":
        value = argument.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            await send_message(session, chat_id, "Usage: /site https://authorized-team-test.example")
        elif not allowed_host(value):
            await send_message(session, chat_id, "That host is not allowlisted. Add its hostname to AUTHORIZED_TEST_DOMAINS, restart the bot, then try /site again.")
        else:
            sites[chat_id] = value
            save_state(state)
            await send_message(session, chat_id, "Site saved. Use /inspect to read its declared requirements.")
    elif command == "/inspect":
        site = site_for(state, chat_id)
        try:
            report = await validate_contract(site)
            await send_message(session, chat_id, "Contract validation for " + site + ":\n" + json.dumps(report, indent=2)[:3800])
        except Exception as exc:
            await send_message(session, chat_id, f"Could not inspect {site}: {exc}")
    elif command == "/discover":
        site = site_for(state, chat_id)
        try:
            result = await discover(site)
            await send_message(session, chat_id, "Read-only endpoint candidates:\n" + json.dumps(result, indent=2)[:3800])
        except Exception as exc:
            await send_message(session, chat_id, f"Could not scan site code: {exc}")
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
        sites.pop(chat_id, None)
        active.pop(chat_id, None)
        save_state(state)
        await send_message(session, chat_id, "Saved identity and site cleared.")
    elif command == "/on":
        if not identities.get(chat_id):
            await send_message(session, chat_id, "Send /identity <public wallet address or username> first.")
            return
        site = site_for(state, chat_id)
        try:
            report = await validate_contract(site)
            if not report.get("ready_for_run"):
                await send_message(session, chat_id, "Automation not activated; contract validation failed:\n" + json.dumps(report, indent=2)[:3000])
                return
        except Exception as exc:
            await send_message(session, chat_id, f"Cannot activate this site: {exc}")
            return
        active[chat_id] = {"next_run": 0}
        save_state(state)
        await send_message(session, chat_id, f"Automation ON for {site}. It will continue until /off. Interval: {ACTIVE_INTERVAL:g}s.")
    elif command == "/off":
        active.pop(chat_id, None)
        save_state(state)
        await send_message(session, chat_id, "Automation OFF. No more automatic test submissions will run for this chat.")
    elif command == "/status":
        site = site_for(state, chat_id)
        requirements = await inspect_site(site)
        leaderboard_path = requirements.get("endpoints", {}).get("leaderboard")
        if not isinstance(leaderboard_path, str) or not leaderboard_path.startswith("/"):
            raise ValueError("site requirements do not declare a relative leaderboard endpoint")
        async with session.get(f"{site.rstrip('/')}{leaderboard_path}") as response:
            response.raise_for_status()
            board = await response.json(content_type=None)
        rows = board.get("list", [])
        if not rows:
            await send_message(session, chat_id, f"Leaderboard at {site} is empty.")
        else:
            lines = [f"Leaderboard at {site} ({len(rows)} shown):"]
            lines.extend(f"#{r.get('rank')} {r.get('name')} — {r.get('score')} score, {r.get('height')}m" for r in rows)
            mode = "ON" if chat_id in active else "OFF"
            await send_message(session, chat_id, "\n".join(lines) + f"\nAutomation: {mode}")
    elif command == "/run":
        identity = identities.get(chat_id)
        if not identity:
            await send_message(session, chat_id, "No identity saved. Send /identity <public wallet address or username> first.")
            return
        site = site_for(state, chat_id)
        await send_message(session, chat_id, "Inspecting the site API, requesting a fresh server token, and running the authorized test…")
        result = await run_site(site, identity, INCREMENT, MIN_HEIGHT, MAX_HEIGHT)
        payload = result["payload"]
        outcome = result["result"]
        await send_message(session, chat_id, f"Submitted to {site}.\nIdentity: {identity}\nPrevious top: {result['previous_score']}\nNew score: {payload.get('score', 'reported by site')} (+at least {INCREMENT})\nHeight: {payload.get('height', 'reported by site')}m\nResult: {json.dumps(outcome)[:1200]}")
    else:
        await send_message(session, chat_id, "Unknown command. Use /start for help.")


async def run_active_chats(session: aiohttp.ClientSession, state: dict[str, Any]) -> None:
    import time
    now = time.time()
    for chat_id, settings in list(state.setdefault("active", {}).items()):
        if float(settings.get("next_run", 0)) > now:
            continue
        identity = state.setdefault("identities", {}).get(chat_id)
        if not identity:
            state["active"].pop(chat_id, None)
            continue
        try:
            site = site_for(state, chat_id)
            result = await run_site(site, identity, INCREMENT, MIN_HEIGHT, MAX_HEIGHT)
            payload = result["payload"]
            await send_message(session, chat_id, f"Automatic test result for {identity}: score {payload.get('score')} at {payload.get('height')}m. Previous top {result['previous_score']}.")
            settings["next_run"] = now + ACTIVE_INTERVAL
            save_state(state)
        except Exception as exc:
            LOG.exception("Active run failed for chat %s", chat_id)
            await send_message(session, chat_id, f"Automatic run paused after an error: {exc}. Use /off, fix the site, then /on.")
            state["active"].pop(chat_id, None)
            save_state(state)


async def run_bot() -> None:
    state = load_state()
    offset = 0
    timeout = aiohttp.ClientTimeout(total=40)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await telegram_call(session, "deleteWebhook", {"drop_pending_updates": "true"})
        LOG.info("Telegram bot active; default site=%s", BASE_URL)
        while True:
            # Short polling keeps active test runs close to the configured
            # 0.5-second cadence instead of waiting on a long Telegram poll.
            params = {"timeout": 0, "offset": offset, "allowed_updates": json.dumps(["message"])}
            try:
                updates = await telegram_call(session, "getUpdates", params)
                for update in updates or []:
                    offset = max(offset, int(update["update_id"]) + 1)
                    try:
                        await handle_update(session, state, update)
                    except Exception as exc:
                        LOG.exception("Update failed")
                        chat_id = str(((update.get("message") or {}).get("chat") or {}).get("id", ""))
                        if chat_id and allowed(chat_id):
                            detail = f"{type(exc).__name__}: {exc}".strip()
                            await send_message(session, chat_id, f"Run failed: {detail[:2500]}")
                await run_active_chats(session, state)
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except RuntimeError as exc:
                if "error_code': 409" in str(exc) or 'error_code": 409' in str(exc):
                    LOG.warning("Another process is polling this Telegram bot token; retrying in 15 seconds")
                    await asyncio.sleep(15)
                else:
                    LOG.exception("Telegram polling failed; retrying")
                    await asyncio.sleep(3)
            except Exception:
                LOG.exception("Telegram polling failed; retrying")
                await asyncio.sleep(3)


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_bot())
