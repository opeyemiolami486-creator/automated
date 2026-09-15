#!/usr/bin/env python3
"""Monitor a question/answer source and submit approved answers to an authorized API.

The worker is dry-run by default. Set ENABLE_SUBMISSION=true only for a website
that you own or are explicitly authorized to automate.

Webcade-style API reference:
  POST /start -> {"token": "..."}
  POST /score -> JSON answer/score payload including that token
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

LOG = logging.getLogger("answer_automation")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    value = os.getenv(name, str(default))
    try:
        result = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if result <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return result


@dataclass(frozen=True)
class Config:
    question_url: str
    start_url: str
    submit_url: str
    answer_file: Path
    state_file: Path
    poll_seconds: float
    request_timeout: float
    enable_submission: bool
    once: bool

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            question_url=os.environ["QUESTION_URL"],
            start_url=os.environ["START_URL"],
            submit_url=os.environ["SUBMIT_URL"],
            answer_file=Path(os.getenv("ANSWER_FILE", "answers.json")),
            state_file=Path(os.getenv("STATE_FILE", "answer_automation_state.json")),
            poll_seconds=env_float("POLL_SECONDS", 1.0),
            request_timeout=env_float("REQUEST_TIMEOUT_SECONDS", 20.0),
            enable_submission=env_bool("ENABLE_SUBMISSION", False),
            once=env_bool("RUN_ONCE", False),
        )


class AutomationWorker:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.stop_event = asyncio.Event()
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.config.state_file.exists():
            return {"submitted": {}, "last_check": None}
        try:
            data = json.loads(self.config.state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"submitted": {}, "last_check": None}
        except (OSError, json.JSONDecodeError):
            LOG.warning("Could not read state file; starting with empty state")
            return {"submitted": {}, "last_check": None}

    def _save_state(self) -> None:
        self.state["last_check"] = utc_now()
        temp = self.config.state_file.with_suffix(self.config.state_file.suffix + ".tmp")
        temp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        temp.replace(self.config.state_file)

    @staticmethod
    def item_id(item: dict[str, Any]) -> str:
        explicit = item.get("id") or item.get("question_id") or item.get("key")
        if explicit is not None:
            return str(explicit)
        encoded = json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    async def get_json(self, session: aiohttp.ClientSession, url: str) -> Any:
        async with session.get(url, headers={"Accept": "application/json"}) as response:
            if response.status == 429:
                raise RuntimeError(f"Rate limited by {url}; Retry-After={response.headers.get('Retry-After', 'unknown')}")
            response.raise_for_status()
            return await response.json()

    async def post_json(self, session: aiohttp.ClientSession, url: str, payload: dict[str, Any]) -> Any:
        async with session.post(
            url,
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        ) as response:
            if response.status == 429:
                raise RuntimeError(f"Rate limited by {url}; Retry-After={response.headers.get('Retry-After', 'unknown')}")
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status} from {url}: {data}")
            return data

    def load_answers(self) -> dict[str, Any]:
        if not self.config.answer_file.exists():
            return {}
        data = json.loads(self.config.answer_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("ANSWER_FILE must contain a JSON object keyed by question id")
        return data

    async def process(self, session: aiohttp.ClientSession) -> int:
        payload = await self.get_json(session, self.config.question_url)
        items = payload.get("questions", payload.get("items", payload)) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise ValueError("QUESTION_URL must return a list, or an object with questions/items")

        answers = self.load_answers()
        processed = 0
        for raw in items:
            if not isinstance(raw, dict):
                continue
            item_id = self.item_id(raw)
            if item_id in self.state.setdefault("submitted", {}):
                continue
            if item_id not in answers:
                LOG.info("No approved answer for question %s; skipping", item_id)
                continue

            answer = answers[item_id]
            token_data = await self.post_json(session, self.config.start_url, {})
            token = token_data.get("token") if isinstance(token_data, dict) else None
            if not token:
                raise ValueError("START_URL response did not contain a token")

            submission = {"question_id": item_id, "answer": answer, "token": token}
            if self.config.enable_submission:
                result = await self.post_json(session, self.config.submit_url, submission)
                LOG.info("Submitted answer for %s: %s", item_id, result)
            else:
                LOG.warning("DRY RUN: would submit %s to %s", submission, self.config.submit_url)

            # Mark only after a live submission. Dry-run remains repeatable.
            if self.config.enable_submission:
                self.state["submitted"][item_id] = {"submitted_at": utc_now(), "response": result}
                self._save_state()
            processed += 1
        return processed

    async def run(self) -> None:
        timeout = aiohttp.ClientTimeout(total=self.config.request_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            LOG.info("Monitoring %s every %ss", self.config.question_url, self.config.poll_seconds)
            LOG.info("Submission mode: %s", "LIVE" if self.config.enable_submission else "DRY RUN")
            backoff = 1.0
            while not self.stop_event.is_set():
                started = time.monotonic()
                try:
                    await self.process(session)
                    backoff = 1.0
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOG.exception("Check failed")
                    await wait_or_stop(self.stop_event, backoff)
                    backoff = min(backoff * 2, 60.0)
                    continue
                if self.config.once:
                    return
                delay = max(0.0, self.config.poll_seconds - (time.monotonic() - started))
                await wait_or_stop(self.stop_event, delay)


def utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


async def wait_or_stop(event: asyncio.Event, delay: float) -> None:
    if delay <= 0:
        return
    try:
        await asyncio.wait_for(event.wait(), timeout=delay)
    except asyncio.TimeoutError:
        pass


async def main() -> None:
    config = Config.from_env()
    worker = AutomationWorker(config)
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, worker.stop_event.set)
        except NotImplementedError:
            pass
    await worker.run()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
