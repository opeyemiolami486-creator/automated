#!/usr/bin/env python3
"""Local Webcade-style mock API for safe hackathon testing.

Endpoints:
  POST /api/dudas/start -> issues a short-lived, one-use run token
  POST /api/dudas/score -> validates token and stores a score
  GET  /api/dudas/board -> returns the local leaderboard
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

STATE_FILE = Path(os.getenv("MOCK_STATE_FILE", "mock_webcade_state.json"))
TOKEN_TTL_SECONDS = float(os.getenv("MOCK_TOKEN_TTL_SECONDS", "300"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"tokens": {}, "scores": []}
    try:
        import json
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("tokens", {})
            data.setdefault("scores", [])
            return data
    except (OSError, ValueError):
        pass
    return {"tokens": {}, "scores": []}


def save_state(state: dict) -> None:
    import json
    temp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


def validate_number(payload: dict, name: str, minimum: int = 0) -> int | None:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = int(value)
    return value if value >= minimum else None


async def start(request: web.Request) -> web.Response:
    state = request.app["state"]
    token = secrets.token_urlsafe(32)
    state["tokens"][token] = {"started_at": time.time(), "used": False}
    save_state(state)
    return web.json_response({"ok": True, "token": token, "expires_in": TOKEN_TTL_SECONDS})


async def submit_score(request: web.Request) -> web.Response:
    state = request.app["state"]
    try:
        payload = await request.json()
    except ValueError:
        return error("Request body must be JSON")
    if not isinstance(payload, dict):
        return error("Request body must be an object")

    address = str(payload.get("address", "")).strip()
    token = str(payload.get("token", "")).strip()
    if not address or not token:
        return error("address and token are required")

    token_record = state["tokens"].get(token)
    if not token_record:
        return error("invalid run token", 401)
    if token_record["used"]:
        return error("run token has already been used", 409)
    elapsed = time.time() - float(token_record["started_at"])
    if elapsed > TOKEN_TTL_SECONDS:
        return error("run token has expired", 401)

    score = validate_number(payload, "score")
    height = validate_number(payload, "height")
    coins = validate_number(payload, "coins")
    toads = validate_number(payload, "toads")
    combo = validate_number(payload, "combo")
    if None in (score, height, coins, toads, combo):
        return error("score, height, coins, toads, and combo must be non-negative numbers")

    token_record["used"] = True
    row = {
        "address": address,
        "name": address[:4] + ".." + address[-4:] if len(address) > 8 else address,
        "score": score,
        "height": height,
        "coins": coins,
        "toads": toads,
        "combo": combo,
        "secs": round(elapsed, 3),
        "submitted_at": utc_now(),
        "submission_id": hashlib.sha256(f"{token}:{address}".encode()).hexdigest()[:16],
    }
    state["scores"].append(row)
    state["scores"].sort(key=lambda item: item["score"], reverse=True)
    save_state(state)
    rank = next(i for i, item in enumerate(state["scores"], 1) if item["submission_id"] == row["submission_id"])
    return web.json_response({"ok": True, "rank": rank, "score": row})


async def board(request: web.Request) -> web.Response:
    state = request.app["state"]
    try:
        limit = min(max(int(request.query.get("limit", "10")), 1), 100)
    except ValueError:
        limit = 10
    rows = []
    for rank, row in enumerate(state["scores"][:limit], 1):
        rows.append({"rank": rank, **row})
    return web.json_response({"key": "local", "players": len(state["scores"]), "list": rows})


async def requirements(request: web.Request) -> web.Response:
    """Declare the public fields required by the local test submission API."""
    return web.json_response({
        "ok": True,
        "identity": {"field": "address", "label": "wallet address or username", "required": True},
        "token": {"start_endpoint": "/api/dudas/start", "field": "token", "required": True},
        "score_fields": ["score", "height", "coins", "toads", "combo"],
    })


def create_app() -> web.Application:
    app = web.Application()
    app["state"] = load_state()
    app.router.add_post("/api/dudas/start", start)
    app.router.add_post("/api/dudas/score", submit_score)
    app.router.add_get("/api/dudas/board", board)
    app.router.add_get("/api/dudas/requirements", requirements)
    return app


if __name__ == "__main__":
    port = int(os.getenv("MOCK_PORT", "8080"))
    web.run_app(create_app(), host=os.getenv("MOCK_HOST", "127.0.0.1"), port=port)
