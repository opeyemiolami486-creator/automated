#!/usr/bin/env python3
"""Authorized, read-only leaderboard monitor.

The script polls every 0.5 seconds only from 22:00:00 UTC through the end of
23:59:59 UTC. It exits before 00:00:00 UTC and never submits or changes scores.

Install:
    python3 -m pip install aiohttp

Run:
    python3 leaderboard_monitor.py

Set LEADERBOARD_URL to the organizers' documented read-only JSON endpoint.
"""

from __future__ import annotations

import asyncio
import json
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LEADERBOARD_URL = "https://YOUR-HACKATHON-DOMAIN.example/api/leaderboard"
LOCAL_SUBMIT_URL = "http://127.0.0.1:12"
LOCAL_USERNAME = "local-demo"
POLL_INTERVAL_SECONDS = 0.5
START_HOUR_UTC = 22
END_HOUR_UTC = 24  # midnight; the script stops before this instant
REQUEST_TIMEOUT_SECONDS = 5
STATE_FILE = Path("leaderboard_state.json")

# Local-only demonstration value. It is never sent to the website.
LOCAL_SCORE_INCREMENT = 100_000


UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def next_window(now: datetime) -> tuple[datetime, datetime]:
    """Return the next 22:00:00–00:00:00 UTC monitoring window."""
    day = now.date()
    start = datetime(day.year, day.month, day.day, START_HOUR_UTC, tzinfo=UTC)

    if now >= start:
        start += timedelta(days=1)

    end = start + timedelta(hours=2)
    return start, end


def current_window(now: datetime) -> tuple[datetime, datetime] | None:
    """Return today's active window, or None when outside the window."""
    start = datetime(
        now.year, now.month, now.day, START_HOUR_UTC, tzinfo=UTC
    )
    end = start + timedelta(hours=2)

    if start <= now < end:
        return start, end
    return None


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"scores": {}, "local_demo_scores": {}, "last_updated": None}

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("Warning: state file could not be read; starting with empty state.")
        return {"scores": {}, "local_demo_scores": {}, "last_updated": None}


def save_state(state: dict) -> None:
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


def extract_entries(payload) -> list[dict[str, int | str]]:
    """Accept {leaderboard: [...]}, {entries: [...]}, or a bare list."""
    if isinstance(payload, dict):
        entries = payload.get("leaderboard", payload.get("entries", []))
    else:
        entries = payload

    if not isinstance(entries, list):
        raise ValueError("Expected a JSON list of leaderboard entries")

    result = []
    for item in entries:
        if not isinstance(item, dict):
            continue

        username = item.get("username") or item.get("name") or item.get("user")
        score = item.get("score")
        if username is None or score is None:
            continue

        result.append({"username": str(username), "score": int(score)})

    return result


async def fetch_leaderboard(session: aiohttp.ClientSession):
    async with session.get(
        LEADERBOARD_URL,
        headers={"Accept": "application/json"},
        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
    ) as response:
        if response.status == 429:
            retry_after = response.headers.get("Retry-After", "unknown")
            raise RuntimeError(f"Rate limited; Retry-After: {retry_after}")

        response.raise_for_status()
        return await response.json()


async def submit_local_score(
    session: aiohttp.ClientSession, score: int
) -> None:
    """Submit only to the explicitly local localhost test service."""
    payload = {"username": LOCAL_USERNAME, "score": score}

    async with session.post(
        LOCAL_SUBMIT_URL,
        json=payload,
        headers={"Accept": "application/json"},
        timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
    ) as response:
        response.raise_for_status()
        print(f"[LOCAL SUBMIT] {payload} -> HTTP {response.status}")


def process_entries(state: dict, entries: list[dict[str, int | str]]) -> None:
    previous = state["scores"]
    local_demo = state["local_demo_scores"]

    for entry in entries:
        username = str(entry["username"])
        score = int(entry["score"])
        old_score = previous.get(username)

        if old_score is None:
            print(f"[NEW] {username}: {score}")
        elif score != old_score:
            print(f"[CHANGE] {username}: {old_score} -> {score} ({score - old_score:+d})")

        # Local simulation only. This value is never POSTed anywhere.
        local_demo[username] = score + LOCAL_SCORE_INCREMENT
        previous[username] = score

    state["last_updated"] = utc_now().isoformat()
    save_state(state)


async def wait_until_next_window() -> tuple[datetime, datetime]:
    while True:
        now = utc_now()
        active = current_window(now)
        if active is not None:
            return active

        start, _ = next_window(now)
        seconds = max(0.0, (start - now).total_seconds())
        print(f"Outside monitoring window. Next start: {start.isoformat()}")
        await asyncio.sleep(min(seconds, 60.0))


async def monitor() -> None:
    state = load_state()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            pass

    connector = aiohttp.TCPConnector(limit=10)

    async with aiohttp.ClientSession(connector=connector) as session:
        print(f"Read-only monitor: {LEADERBOARD_URL}")
        print(f"Local score target: {LOCAL_SUBMIT_URL}")
        print(f"Polling interval: {POLL_INTERVAL_SECONDS} seconds")
        print("UTC schedule: 22:00:00 inclusive through 23:59:59.999... inclusive")
        print("Submission mode: localhost only")

        while not stop_event.is_set():
            start, end = await wait_until_next_window()
            print(f"Window started: {start.isoformat()}")
            retry_delay = 1.0

            while not stop_event.is_set():
                now = utc_now()
                remaining = (end - now).total_seconds()
                if remaining <= 0:
                    # Deliberately exit the inner loop before 00:00:00 UTC.
                    print(f"Window ended before {end.isoformat()}")
                    break

                cycle_started = time.monotonic()
                try:
                    payload = await fetch_leaderboard(session)
                    entries = extract_entries(payload)
                    process_entries(state, entries)

                    # The +100,000 value is submitted only to localhost for
                    # testing the user's local scoring service.
                    for entry in entries:
                        demo_score = int(entry["score"]) + LOCAL_SCORE_INCREMENT
                        await submit_local_score(session, demo_score)

                    retry_delay = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    print(f"[ERROR] {error}")
                    await asyncio.sleep(min(retry_delay, remaining))
                    retry_delay = min(retry_delay * 2, 30.0)

                # Schedule the next cycle at a 0.5-second cadence without
                # accumulating request duration into the interval.
                elapsed = time.monotonic() - cycle_started
                delay = min(max(0.0, POLL_INTERVAL_SECONDS - elapsed), remaining)
                if delay > 0:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass


if __name__ == "__main__":
    try:
        asyncio.run(monitor())
    except KeyboardInterrupt:
        print("Monitor stopped.")
