#!/usr/bin/env python3
"""Submit a higher score to the local mock Webcade API.

This client never targets the public Webcade service: MOCK_BASE_URL must point
at localhost or a private test host.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import aiohttp


async def request_json(session: aiohttp.ClientSession, method: str, url: str, **kwargs: Any) -> dict:
    async with session.request(method, url, **kwargs) as response:
        data = await response.json(content_type=None)
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}: {data}")
        return data


async def submit_higher_score(base_url: str, address: str, increment: int) -> dict:
    if not base_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
        raise ValueError("This test client only permits localhost MOCK_BASE_URL")
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        board = await request_json(session, "GET", f"{base_url}/api/dudas/board?limit=1&window=today")
        current = int(board.get("list", [{}])[0].get("score", 0)) if board.get("list") else 0
        proposed = current + increment
        run = await request_json(session, "POST", f"{base_url}/api/dudas/start", json={})
        token = run.get("token")
        if not token:
            raise RuntimeError("Mock start endpoint did not return a token")
        payload = {
            "address": address,
            "token": token,
            "score": proposed,
            "height": max(1, proposed // 300),
            "coins": max(1, proposed // 1000),
            "toads": max(1, proposed // 5000),
            "combo": 1,
        }
        result = await request_json(session, "POST", f"{base_url}/api/dudas/score", json=payload)
        return {"previous_top_score": current, "submitted": payload, "result": result}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("MOCK_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--address", default=os.getenv("MOCK_ADDRESS", "hackathon-local-player"))
    parser.add_argument("--increment", type=int, default=int(os.getenv("MOCK_SCORE_INCREMENT", "1000")))
    args = parser.parse_args()
    if args.increment <= 0:
        raise ValueError("increment must be greater than 0")
    result = await submit_higher_score(args.base_url.rstrip("/"), args.address, args.increment)
    print(result)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
