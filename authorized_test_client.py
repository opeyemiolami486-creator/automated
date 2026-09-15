#!/usr/bin/env python3
"""Run a higher test score against an explicitly authorized team test site.

A site may expose an optional JSON requirements document. When it does not,
the adapter uses the conventional Dudas endpoints and field names. Hosts must
still be listed in AUTHORIZED_TEST_DOMAINS, and the caller must explicitly
authorize the live test site.
"""
from __future__ import annotations

import os
import random
import asyncio
import math
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


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value > 0 else None


def extract_timing(data: Any) -> tuple[float | None, float | None]:
    """Extract an explicit minimum duration or seconds-per-height factor."""
    if not isinstance(data, dict):
        return None, None
    duration_keys = (
        "allowed_seconds", "allowed_time_seconds", "minimum_seconds",
        "min_duration_seconds", "minDurationSeconds", "minimumDurationSeconds",
    )
    factor_keys = (
        "time_factor", "timeFactor", "seconds_per_height", "secondsPerHeight",
        "min_seconds_per_height", "minSecondsPerHeight",
    )
    duration = next((_positive_number(data.get(k)) for k in duration_keys if _positive_number(data.get(k)) is not None), None)
    factor = next((_positive_number(data.get(k)) for k in factor_keys if _positive_number(data.get(k)) is not None), None)
    for key in ("timing", "rules", "game", "run", "data"):
        nested_duration, nested_factor = extract_timing(data.get(key))
        duration = duration or nested_duration
        factor = factor or nested_factor
    return duration, factor


def derive_time_factor(board: Any) -> float | None:
    """Use the fastest completed public run as a conservative speed baseline."""
    rows = board.get("list", []) if isinstance(board, dict) else []
    ratios = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        height = _positive_number(row.get("height"))
        seconds = _positive_number(row.get("secs"))
        if height is not None and seconds is not None:
            ratios.append(seconds / height)
    return min(ratios) if ratios else None


def endpoint_url(base_url: str, endpoint: str) -> str:
    """Resolve an endpoint against the site page or its origin.

    API roots such as ``/api/dudas/score`` are origin-relative, while a
    path such as ``/game/start`` is relative to the selected site page.
    """
    if endpoint.startswith("/api/"):
        parsed = urlparse(base_url.rstrip("/"))
        return f"{parsed.scheme}://{parsed.netloc}{endpoint}"
    return base_url.rstrip("/") + endpoint


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
            configured = configured.strip()
            if configured.rstrip("/") == parsed.path.rstrip("/"):
                configured = configured.rstrip("/") + "/requirements"
            if configured.startswith(("http://", "https://")):
                candidates = [configured]
            else:
                candidates = [f"{parsed.scheme}://{parsed.netloc}/{configured.lstrip('/')}" ]
        else:
            prefix = parsed.path.rstrip("/")
            candidates = []
            if prefix:
                candidates.append(f"{parsed.scheme}://{parsed.netloc}{prefix}/requirements")
            origin = f"{parsed.scheme}://{parsed.netloc}"
            candidates.extend([origin + "/requirements", origin + "/api/requirements", origin + "/api/dudas/requirements"])
        errors = []
        for path in dict.fromkeys(candidates):
            url = path if path.startswith(("http://", "https://")) else f"{base_url.rstrip('/')}/{path.lstrip('/')}"
            try:
                return await json_request(session, "GET", url)
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        # The requirements document is optional. This fallback keeps judges
        # able to test a compatible site that exposes the conventional API but
        # does not publish a separate contract document.
        return {
            "contract_source": "inferred-conventional-endpoints",
            "contract_optional": True,
            "endpoints": {
                "leaderboard": "/api/dudas/board?limit=10&window=today",
                "start": "/api/dudas/start",
                "submit": "/api/dudas/score",
            },
            "identity": {"field": "address"},
            "token": {"field": "token", "json_path": "token"},
            "score_fields": {
                "score": "score",
                "height": "height",
                "coins": "coins",
                "toads": "toads",
                "combo": "combo",
            },
            "discovery_errors": errors,
        }


async def run_site(base_url: str, identity: str, increment: int = 50000, min_height: int = 1900, max_height: int = 2500, play_duration_seconds: float | None = None) -> dict[str, Any]:
    if increment < 0 or not 0 < min_height <= max_height or (play_duration_seconds is not None and play_duration_seconds <= 0):
        raise ValueError("score increment must be non-negative, optional play duration must be greater than 0, and height range must be positive")
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
        board = await json_request(session, "GET", endpoint_url(base_url, endpoints["leaderboard"]))
        rows = board.get("list", []) if isinstance(board, dict) else board
        current = int(rows[0].get(fields.get("score", "score"), 0)) if rows else 0
        start = await json_request(session, "POST", endpoint_url(base_url, endpoints["start"]), json={})
        token = dotted(start, token_path)
        if not isinstance(token, str) or not token:
            raise ValueError("start response did not contain the declared token")
        height = random.randint(min_height, max_height)
        duration, factor = extract_timing(start)
        if duration is None and factor is None:
            duration, factor = extract_timing(requirements.get("timing"))
        factor = factor or derive_time_factor(board)
        if play_duration_seconds is not None:
            wait_seconds = play_duration_seconds
        elif duration is not None:
            wait_seconds = duration
        elif factor is not None:
            wait_seconds = height * factor
        else:
            raise ValueError("site did not declare or expose an allowed run time; set AUTHORIZED_PLAY_DURATION_SECONDS or provide timing metadata")
        safety_margin = _positive_number(os.getenv("TIMING_SAFETY_MARGIN_SECONDS", "0.25")) or 0.0
        await asyncio.sleep(wait_seconds + safety_margin)
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
        result = await json_request(session, "POST", endpoint_url(base_url, endpoints["submit"]), json=payload)
        return {"previous_score": current, "identity": identity, "payload": payload, "result": result}
