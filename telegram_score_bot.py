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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp

from authorized_test_client import allowed_host, endpoint_url, inspect_site, run_site, submit_at_deadline
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
_configured_duration = os.getenv("AUTHORIZED_PLAY_DURATION_SECONDS")
PLAY_DURATION_SECONDS = float(_configured_duration) if _configured_duration else None
if PLAY_DURATION_SECONDS is not None and PLAY_DURATION_SECONDS <= 0:
    raise ValueError("AUTHORIZED_PLAY_DURATION_SECONDS must be greater than 0")
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


def selected_site(state: dict[str, Any], chat_id: str) -> str | None:
    """Return the explicitly selected site, not the localhost default."""
    value = state.setdefault("sites", {}).get(chat_id)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().rstrip("/")


def parse_deadline(value: str, timezone_name: str = "local") -> datetime:
    """Parse HH:MM:SS in UTC or the bot host's local timezone."""
    try:
        clock = datetime.strptime(value, "%H:%M:%S").time()
    except ValueError as exc:
        raise ValueError("time must use exact HH:MM:SS format, for example 11:59:59") from exc
    if timezone_name.upper() == "UTC":
        now = datetime.now(timezone.utc)
    elif timezone_name.lower() == "local":
        now = datetime.now().astimezone()
    else:
        raise ValueError("timezone must be UTC or LOCAL")
    deadline = datetime.combine(now.date(), clock, tzinfo=now.tzinfo)
    if deadline <= now:
        deadline += timedelta(days=1)
    return deadline


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
    schedules = state.setdefault("schedules", {})
    old_pending = state.pop("pending_schedules", {})
    for owner, proposal in old_pending.items():
        schedules.setdefault(owner, {})["request-1"] = {**proposal, "status": "pending"}
    owner_schedules = schedules.setdefault(chat_id, {})

    if command in {"/start", "/help"}:
        await send_message(session, chat_id, "Commands:\n/site <authorized test URL>\n/discover\n/inspect\n/identity <raw public address>\n/status\n/on\n/off\n/run\n/schedule <score> <HH:MM:SS> [UTC|LOCAL] [raw-address]\n/schedules\n/ack <request-id> or /ack all\n/cancel <request-id>\n/clear")
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
            await send_message(session, chat_id, "Usage: /identity <raw public address>")
        else:
            identities[chat_id] = value
            save_state(state)
            await send_message(session, chat_id, f"Saved test identity: {value}")
    elif command == "/clear":
        identities.pop(chat_id, None)
        sites.pop(chat_id, None)
        active.pop(chat_id, None)
        schedules.pop(chat_id, None)
        save_state(state)
        await send_message(session, chat_id, "Saved identity and site cleared.")
    elif command == "/schedule":
        parts = argument.split()
        if len(parts) < 2 or len(parts) > 4:
            await send_message(session, chat_id, "Usage: /schedule <target score> <HH:MM:SS> [UTC|LOCAL] [identity]\nExample: /schedule 100000 11:59:59 UTC player-two")
            return
        try:
            score = int(parts[0])
            if score < 0:
                raise ValueError("score must be a non-negative integer")
            remainder = parts[2:]
            zone = "LOCAL"
            request_identity = None
            if remainder and remainder[0].upper() in {"UTC", "LOCAL"}:
                zone = remainder.pop(0).upper()
            if remainder:
                request_identity = remainder[0]
            deadline = parse_deadline(parts[1], zone)
        except ValueError as exc:
            await send_message(session, chat_id, f"Invalid schedule: {exc}")
            return
        identity = request_identity or identities.get(chat_id)
        if not identity:
            await send_message(session, chat_id, "Set a default raw address with /identity, or specify a raw address at the end of /schedule.")
            return
        number = 1
        while f"request-{number}" in owner_schedules:
            number += 1
        request_id = f"request-{number}"
        owner_schedules[request_id] = {"score": score, "deadline": deadline.isoformat(), "identity": identity, "status": "pending"}
        save_state(state)
        await send_message(session, chat_id, f"Saved {request_id}\nIdentity: {identity}\nProposed score: {score}\nSubmit deadline: {deadline.strftime('%Y-%m-%d %H:%M:%S %Z')}\nNo leaderboard lookup will be used. Reply /ack {request_id} to obtain a token and schedule submission, or /cancel {request_id}.")
    elif command == "/schedules":
        if not owner_schedules:
            await send_message(session, chat_id, "No saved scheduled requests.")
            return
        lines = ["Saved scheduled requests:"]
        for request_id, proposal in owner_schedules.items():
            deadline = datetime.fromisoformat(str(proposal["deadline"]))
            lines.append(f"{request_id}: identity {proposal.get('identity', '(not set)')} — score {proposal['score']} at {deadline.strftime('%Y-%m-%d %H:%M:%S %Z')} [{proposal.get('status', 'pending')}]")
        await send_message(session, chat_id, "\n".join(lines))
    elif command in {"/ack", "/cancel"}:
        if command == "/ack" and argument.strip().lower() == "all":
            site = selected_site(state, chat_id)
            if not site:
                await send_message(session, chat_id, "Set /site before acknowledging scheduled submissions.")
                return
            pending_ids = [request_id for request_id, item in owner_schedules.items() if item.get("status") == "pending"]
            if not pending_ids:
                await send_message(session, chat_id, "There are no pending requests to activate.")
                return
            if any(not owner_schedules[request_id].get("identity") for request_id in pending_ids):
                await send_message(session, chat_id, "One or more requests has no saved identity. Recreate it after using /identity.")
                return
            for request_id in pending_ids:
                owner_schedules[request_id]["status"] = "active"
            save_state(state)
            await send_message(session, chat_id, f"Activating {len(pending_ids)} requests simultaneously. Each will use its own token and deadline.")
            for request_id in pending_ids:
                proposal = owner_schedules[request_id]
                asyncio.create_task(execute_scheduled(session, state, chat_id, request_id, site, proposal["identity"], proposal))
            return
        request_id = argument.strip() or (next(iter(owner_schedules)) if len(owner_schedules) == 1 else "")
        proposal = owner_schedules.get(request_id)
        if not proposal:
            await send_message(session, chat_id, "Specify a valid request ID, for example /ack request-1. Use /schedules to list requests.")
            return
        if command == "/cancel":
            if proposal.get("status") not in {"pending", "active"}:
                await send_message(session, chat_id, f"{request_id} cannot be cancelled because it is {proposal.get('status')}.")
                return
            proposal["status"] = "cancelled"
            save_state(state)
            await send_message(session, chat_id, f"{request_id} cancelled.")
            return
        if proposal.get("status") != "pending":
            await send_message(session, chat_id, f"{request_id} is already {proposal.get('status')}.")
            return
        identity = proposal.get("identity") or identities.get(chat_id)
        site = selected_site(state, chat_id)
        if not identity or not site:
            await send_message(session, chat_id, "This request has no saved identity or site. Recreate it after setting /site and /identity.")
            return
        proposal["status"] = "active"
        save_state(state)
        await send_message(session, chat_id, f"Activated {request_id}. Requesting its server token now; it runs independently of every other request and will submit at the requested HH:MM:SS deadline.")
        asyncio.create_task(execute_scheduled(session, state, chat_id, request_id, site, identity, proposal))
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
        async with session.get(endpoint_url(site, leaderboard_path)) as response:
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
        site = selected_site(state, chat_id)
        if not site:
            await send_message(session, chat_id, "No test site selected. Send /site https://your-deployed-test-site first; the localhost mock is not used automatically.")
            return
        await send_message(session, chat_id, "Inspecting the site API, requesting a fresh server token, and running the authorized test…")
        async def timing_status(seconds: float, source: str) -> None:
            await send_message(session, chat_id, f"Server timing found ({source}); waiting {seconds:.1f}s before submitting the test run…")
        result = await run_site(site, identity, INCREMENT, MIN_HEIGHT, MAX_HEIGHT, PLAY_DURATION_SECONDS, timing_status)
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
            result = await run_site(site, identity, INCREMENT, MIN_HEIGHT, MAX_HEIGHT, PLAY_DURATION_SECONDS)
            payload = result["payload"]
            await send_message(session, chat_id, f"Automatic test result for {identity}: score {payload.get('score')} at {payload.get('height')}m. Previous top {result['previous_score']}.")
            settings["next_run"] = now + ACTIVE_INTERVAL
            save_state(state)
        except Exception as exc:
            LOG.exception("Active run failed for chat %s", chat_id)
            await send_message(session, chat_id, f"Automatic run paused after an error: {exc}. Use /off, fix the site, then /on.")
            state["active"].pop(chat_id, None)
            save_state(state)


async def execute_scheduled(session: aiohttp.ClientSession, state: dict[str, Any], chat_id: str, request_id: str, site: str, identity: str, proposal: dict[str, Any]) -> None:
    try:
        deadline = datetime.fromisoformat(str(proposal["deadline"]))

        async def wait_status(seconds: float, compensation: float) -> None:
            await send_message(session, chat_id, f"Token acquired; measured latency compensation {compensation * 1000:.0f}ms; submitting toward {deadline.strftime('%H:%M:%S')}…")

        result = await submit_at_deadline(site, identity, int(proposal["score"]), deadline, MIN_HEIGHT, wait_status)
        payload = result["payload"]
        proposal["status"] = "submitted"
        proposal["result"] = result["result"]
        save_state(state)
        await send_message(session, chat_id, f"Scheduled submission sent at {deadline.strftime('%H:%M:%S')}\nScore: {payload.get('score')}\nResult: {json.dumps(result['result'])[:1200]}")
    except Exception as exc:
        LOG.exception("Scheduled run failed for chat %s", chat_id)
        proposal["status"] = "failed"
        proposal["error"] = f"{type(exc).__name__}: {exc}"
        save_state(state)
        await send_message(session, chat_id, f"Scheduled submission failed: {type(exc).__name__}: {exc}")


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
