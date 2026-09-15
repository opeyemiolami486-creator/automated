#!/usr/bin/env python3
"""Submit a higher score to the local mock Webcade API.

This client never targets the public Webcade service: MOCK_BASE_URL must point
at localhost or a private test host.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from typing import Any

import aiohttp


async def request_json(session: aiohttp.ClientSession, method: str, url: str, **kwargs: Any) -> dict:
    async with session.request(method, url, **kwargs) as response:
        data = await response.json(content_type=None)
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}: {data}")
        return data


async def submit_higher_score(
    base_url: str, identity: str | None, identity_field: str | None,
    increment: int, min_height: int, max_height: int
) -> dict:
    if not base_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
        raise ValueError("This test client only permits localhost MOCK_BASE_URL")
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        board = await request_json(session, "GET", f"{base_url}/api/dudas/board?limit=1&window=today")
        requirements = await request_json(session, "GET", f"{base_url}/api/dudas/requirements")
        declared = requirements.get("identity", {}) if isinstance(requirements, dict) else {}
        field = identity_field or declared.get("field") or "address"
        label = declared.get("label", "wallet address or username")
        identity = identity or input(f"Enter {label} for this test run: ").strip()
        if not identity:
            raise ValueError("an identity value is required for the test run")
        current = int(board.get("list", [{}])[0].get("score", 0)) if board.get("list") else 0
        height = random.randint(min_height, max_height)
        # Keep the test result above the current local top while making the
        # score proportional to a long reference-style climb.
        proposed = max(current + increment, height * 300)
        run = await request_json(session, "POST", f"{base_url}/api/dudas/start", json={})
        token = run.get("token")
        if not token:
            raise RuntimeError("Mock start endpoint did not return a token")
        payload = {
            "token": token,
            "score": proposed,
            "height": height,
            "coins": max(1, proposed // 1000),
            "toads": max(1, proposed // 5000),
            "combo": 1,
        }
        payload[field] = identity
        result = await request_json(session, "POST", f"{base_url}/api/dudas/score", json=payload)
        return {"previous_top_score": current, "submitted": payload, "result": result}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("MOCK_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--identity", default=os.getenv("MOCK_IDENTITY"))
    parser.add_argument("--identity-field", default=os.getenv("MOCK_IDENTITY_FIELD"))
    parser.add_argument("--increment", type=int, default=int(os.getenv("MOCK_SCORE_INCREMENT", "50000")))
    parser.add_argument("--min-height", type=int, default=int(os.getenv("MOCK_MIN_HEIGHT", "1900")))
    parser.add_argument("--max-height", type=int, default=int(os.getenv("MOCK_MAX_HEIGHT", "2500")))
    args = parser.parse_args()
    if args.increment < 50000:
        raise ValueError("increment must be at least 50000")
    if not 0 < args.min_height <= args.max_height:
        raise ValueError("height range must satisfy 0 < min-height <= max-height")
    result = await submit_higher_score(
        args.base_url.rstrip("/"), args.identity, args.identity_field,
        args.increment, args.min_height, args.max_height
    )
    print(result)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
