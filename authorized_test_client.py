#!/usr/bin/env python3
"""Run a higher test score against an explicitly authorized team test site.

The site must expose GET /requirements (or the configured requirements path)
that declares leaderboard/start/submit endpoints and JSON field names. Hosts
must be listed in AUTHORIZED_TEST_DOMAINS.
"""
from __future__ import annotations

import os
import random
from urllib.parse import urlparse
from typing import Any

import aiohttp


def allowed_host(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").rstrip(".").lower()
    allowed = []
    for raw in os.getenv("AUTHORIZED_TEST_DOMAINS", "").split(","):
        raw = raw.strip().lower().rstrip("/")
        if not raw:
            continue
        if "://" in raw:
            raw = urlparse(raw).hostname or ""
        elif "/" in raw or ":" in raw:
            raw = urlparse("//" + raw).hostname or raw.split("/", 1)[0].split(":", 1)[0]
        allowed.append(raw.rstrip("."))
    if host in {"127.0.0.1", "localhost", "::1"}:
        return True
    return any(host == item or (item.startswith("*.") and host.endswith(item[1:])) for item in allowed)


def allowlist_description() -> str:
    return os.getenv("AUTHORIZED_TEST_DOMAINS", "(none; localhost only)")


def dotted(data: Any, path: str) -> Any:
    value = data
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


async def json_request(session: aiohttp.ClientSession, method: str, url: str, **kwargs: Any) -> Any:
    async with session.request(method, url, **kwargs) as response:
        data = await response.json(content_type=None)
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}: {data}")
        return data


async def inspect_site(base_url: str, requirements_path: str | None = None) -> dict[str, Any]:
    if not allowed_host(base_url):
        host = urlparse(base_url).hostname or "(missing host)"
        raise ValueError(f"host {host!r} is not allowlisted; configured entries: {allowlist_description()}")
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        parsed = urlparse(base_url.rstrip("/"))
        configured = requirements_path or os.getenv("REQUIREMENTS_PATH")
        if configured:
            candidates = [configured]
        else:
            prefix = parsed.path.rstrip("/")
            candidates = []
            if prefix:
                candidates.append(prefix + "/requirements")
            candidates.extend(["/requirements", "/api/requirements", "/api/dudas/requirements"])
        errors = []
        for path in dict.fromkeys(candidates):
            url = f"{base_url.rstrip('/')}{path}"
            try:
                return await json_request(session, "GET", url)
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        raise ValueError("could not find a JSON requirements contract; tried " + " | ".join(errors))


async def run_site(base_url: str, identity: str, increment: int = 50000, min_height: int = 1900, max_height: int = 2500) -> dict[str, Any]:
    if increment < 50000 or not 0 < min_height <= max_height:
        raise ValueError("invalid score increment or height range")
    requirements = await inspect_site(base_url)
    endpoints = requirements.get("endpoints", {})
    identity_spec = requirements.get("identity", {})
    token_spec = requirements.get("token", {})
    fields = requirements.get("score_fields", {})
    required = [endpoints.get(k) for k in ("leaderboard", "start", "submit")]
    if not all(isinstance(x, str) and x.startswith("/") for x in required):
        raise ValueError("site requirements must declare relative leaderboard/start/submit endpoints")
    identity_field = identity_spec.get("field")
    token_field = token_spec.get("field", "token")
    token_path = token_spec.get("json_path", "token")
    if not isinstance(fields, dict):
        fields = {name: name for name in (fields if isinstance(fields, list) else ["score", "height"])}
    if not isinstance(identity_field, str):
        raise ValueError("site requirements must declare an identity field")
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        board = await json_request(session, "GET", base_url.rstrip("/") + endpoints["leaderboard"])
        rows = board.get("list", []) if isinstance(board, dict) else board
        current = int(rows[0].get(fields.get("score", "score"), 0)) if rows else 0
        start = await json_request(session, "POST", base_url.rstrip("/") + endpoints["start"], json={})
        token = dotted(start, token_path)
        if not isinstance(token, str) or not token:
            raise ValueError("start response did not contain the declared token")
        height = random.randint(min_height, max_height)
        score = max(current + increment, height * 300)
        payload: dict[str, Any] = {
            identity_field: identity,
            token_field: token,
            fields.get("score", "score"): score,
            fields.get("height", "height"): height,
        }
        for logical, default in (("coins", 0), ("toads", 0), ("combo", 1)):
            if logical in fields:
                payload[fields[logical]] = max(default, score // (1000 if logical == "coins" else 5000))
        result = await json_request(session, "POST", base_url.rstrip("/") + endpoints["submit"], json=payload)
        return {"previous_score": current, "identity": identity, "payload": payload, "result": result}
