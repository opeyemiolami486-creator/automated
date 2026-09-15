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
import time
from datetime import datetime
from urllib.parse import urlparse
from typing import Any, Awaitable, Callable

import aiohttp


_REQUIREMENTS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


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


def height_for_score(score: int, preferred_min: int = 1900, preferred_max: int = 2500, score_per_height: int | None = None) -> int:
    """Choose the tallest valid climb for a requested score.

    The reference default rejects a score below ``height * 300``. An
    authorized site's contract may override that ratio with
    ``score_rules.min_score_per_height``; otherwise ``MIN_SCORE_PER_HEIGHT``
    supplies the configured default. If a requested score cannot support the
    preferred minimum height, reduce the height rather than sending an
    internally inconsistent payload.
    """
    score_per_height = int(os.getenv("MIN_SCORE_PER_HEIGHT", "300")) if score_per_height is None else score_per_height
    if score_per_height <= 0 or score < score_per_height or preferred_min <= 0 or preferred_max < preferred_min:
        raise ValueError(f"score {score} is too small to support even a 1m climb")
    supported = score // score_per_height
    return min(preferred_max, max(1, min(preferred_min, supported)))


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
    cache_key = f"{base_url.rstrip('/')}|{requirements_path or os.getenv('REQUIREMENTS_PATH', '')}"
    cache_seconds = _positive_number(os.getenv("REQUIREMENTS_CACHE_SECONDS", "30")) or 0.0
    cached = _REQUIREMENTS_CACHE.get(cache_key)
    if cached and cache_seconds > 0 and time.monotonic() - cached[0] < cache_seconds:
        return cached[1]
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
                result = await json_request(session, "GET", url)
                if isinstance(result, dict) and cache_seconds > 0:
                    _REQUIREMENTS_CACHE[cache_key] = (time.monotonic(), result)
                return result
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        # The requirements document is optional. This fallback keeps judges
        # able to test a compatible site that exposes the conventional API but
        # does not publish a separate contract document.
        result = {
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
        if cache_seconds > 0:
            _REQUIREMENTS_CACHE[cache_key] = (time.monotonic(), result)
        return result


async def run_site(
    base_url: str,
    identity: str,
    increment: int = 50000,
    min_height: int = 1900,
    max_height: int = 2500,
    play_duration_seconds: float | None = None,
    on_timing: Callable[[float, str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
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
        board, start = await asyncio.gather(
            json_request(session, "GET", endpoint_url(base_url, endpoints["leaderboard"])),
            json_request(session, "POST", endpoint_url(base_url, endpoints["start"]), json={}),
        )
        rows = board.get("list", []) if isinstance(board, dict) else board
        current = int(rows[0].get(fields.get("score", "score"), 0)) if rows else 0
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
        total_wait = wait_seconds + safety_margin
        token_lifetime = _positive_number(start.get("expires_in")) if isinstance(start, dict) else None
        if token_lifetime is not None and total_wait >= token_lifetime:
            raise ValueError(
                f"calculated wait {total_wait:.1f}s is not valid: it reaches the token lifetime of {token_lifetime:.1f}s; "
                "provide the site's minimum timing factor or set AUTHORIZED_PLAY_DURATION_SECONDS"
            )
        if on_timing is not None:
            source = "explicit duration" if duration is not None else "time factor" if factor is not None else "override"
            await on_timing(total_wait, source)
        await asyncio.sleep(total_wait)
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


async def submit_at_deadline(
    base_url: str,
    identity: str,
    score: int,
    deadline: datetime,
    height: int = 1,
    on_wait: Callable[[float, float], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Submit a user-confirmed score at an exact local wall-clock deadline.

    This deliberately does not read the leaderboard. The run token is acquired
    after acknowledgement, then the client waits until ``deadline`` before
    posting. The server remains authoritative about whether the elapsed time is
    valid.
    """
    if score < 0 or height <= 0:
        raise ValueError("score must be non-negative and height must be positive")
    if deadline.tzinfo is None:
        raise ValueError("deadline must be timezone-aware")
    requirements = await inspect_site(base_url)
    endpoints = requirements.get("endpoints", {})
    identity_spec = requirements.get("identity", {})
    token_spec = requirements.get("token", {})
    fields = requirements.get("score_fields", {})
    if not isinstance(fields, dict):
        fields = {name: name for name in (fields if isinstance(fields, list) else ["score", "height"])}
    identity_field = identity_spec.get("field")
    if not isinstance(identity_field, str) or not isinstance(endpoints.get("start"), str) or not isinstance(endpoints.get("submit"), str):
        raise ValueError("site requirements must declare identity, start, and submit fields")
    token_field = token_spec.get("field", "token")
    token_path = token_spec.get("json_path", "token")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        start_url = endpoint_url(base_url, endpoints["start"])
        start_sent = time.monotonic()
        start = await json_request(session, "POST", start_url, json={})
        start_rtt = time.monotonic() - start_sent
        token = dotted(start, token_path)
        if not isinstance(token, str) or not token:
            raise ValueError("start response did not contain the declared token")
        remaining = (deadline - datetime.now(deadline.tzinfo)).total_seconds()
        if remaining <= 0:
            raise ValueError("deadline must be in the future")
        # The score request must leave before the wall-clock deadline because
        # the server timestamps receipt, not client-side dispatch. The start
        # request uses the same host and route, so half its RTT is the best
        # available estimate of one-way network travel. The estimate is capped
        # to avoid an unusual slow token response causing a visibly early post.
        compensation = min(start_rtt / 2.0, remaining / 2.0)
        if on_wait is not None:
            await on_wait(remaining, compensation)
        await asyncio.sleep(max(0.0, remaining - compensation))
        score_rule = requirements.get("score_rules", {})
        configured_ratio = score_rule.get("min_score_per_height") if isinstance(score_rule, dict) else None
        score_per_height = int(configured_ratio) if isinstance(configured_ratio, (int, float)) and configured_ratio > 0 else None
        height = height_for_score(score, preferred_min=height, preferred_max=height, score_per_height=score_per_height)
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
        return {
            "identity": identity,
            "payload": payload,
            "result": result,
            "deadline": deadline.isoformat(),
            "start_rtt_seconds": start_rtt,
            "latency_compensation_seconds": compensation,
        }
