#!/usr/bin/env python3
"""Read-only discovery of likely API endpoints in an authorized test site.

This reports candidates from HTML/JavaScript; it does not POST, execute site
code, bypass login, or claim that a regex match is a valid API contract.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin
from typing import Any

import aiohttp

API_RE = re.compile(r"(?:fetch|axios\.(?:get|post)|XMLHttpRequest|['\"])([^'\"\s]*(?:/api/|/graphql|leaderboard|score|submit|start|run|token)[^'\"\s]*)", re.I)
SCRIPT_RE = re.compile(r"<script[^>]+src=['\"]([^'\"]+)['\"]", re.I)


def classify(url: str) -> list[str]:
    labels = []
    low = url.lower()
    if any(x in low for x in ("leaderboard", "board", "ranking")):
        labels.append("leaderboard candidate")
    if any(x in low for x in ("start", "run", "token", "nonce")):
        labels.append("run-token candidate")
    if any(x in low for x in ("score", "submit", "answer", "result")):
        labels.append("submission candidate")
    return labels or ["unclassified API candidate"]


async def discover(base_url: str) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(base_url, headers={"Accept": "text/html,application/javascript"}) as response:
            response.raise_for_status()
            html = await response.text()
        sources = [(base_url, html)]
        for src in SCRIPT_RE.findall(html)[:20]:
            script_url = urljoin(base_url, src)
            try:
                async with session.get(script_url) as response:
                    if response.status < 400:
                        sources.append((script_url, await response.text()))
            except Exception:
                continue
        found: dict[str, set[str]] = {}
        for source, text in sources:
            for candidate in API_RE.findall(text):
                if candidate.startswith(("http://", "https://")):
                    endpoint = candidate
                elif candidate.startswith("/"):
                    endpoint = urljoin(base_url, candidate)
                else:
                    continue
                found.setdefault(endpoint.split("?", 1)[0], set()).update(classify(endpoint))
        return {
            "site": base_url,
            "sources_scanned": [source for source, _ in sources],
            "candidates": [{"url": url, "labels": sorted(labels)} for url, labels in sorted(found.items())],
            "warning": "Candidates require team confirmation; source-code matches are not a submission contract.",
        }
