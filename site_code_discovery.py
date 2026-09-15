#!/usr/bin/env python3
"""Read-only discovery of likely API endpoints in an authorized test site.

This reports candidates from HTML/JavaScript; it does not POST, execute site
code, bypass login, or claim that a regex match is a valid API contract.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse
from typing import Any

import aiohttp

API_RE = re.compile(r"(?:fetch|axios\.(?:get|post)|XMLHttpRequest|['\"])([^'\"\s]*(?:/api/|/graphql|leaderboard|score|submit|start|run|token)[^'\"\s]*)", re.I)
SCRIPT_RE = re.compile(r"<script[^>]+src=['\"]([^'\"]+)['\"]", re.I)
LINK_RE = re.compile(r"<(?:a|iframe)[^>]+(?:href|src)=['\"]([^'\"]+)['\"]", re.I)
API_BASE_RE = re.compile(r"(?:const|let|var)\s+API\s*=\s*['\"]([^'\"]+)['\"]")
POST_PATH_RE = re.compile(r"(?:post|fetch)\(\s*['\"]([^'\"]+)['\"]", re.I)


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
        script_urls = [urljoin(base_url, src) for src in SCRIPT_RE.findall(html)[:20]]
        linked_pages = [urljoin(base_url, href) for href in LINK_RE.findall(html)[:20]]
        linked_pages = [url for url in linked_pages if urlparse(url).netloc == urlparse(base_url).netloc and "/play" in url]
        for page_url in linked_pages[:5]:
            try:
                async with session.get(page_url) as response:
                    if response.status < 400:
                        page_html = await response.text()
                        sources.append((page_url, page_html))
                        script_urls.extend(urljoin(page_url, src) for src in SCRIPT_RE.findall(page_html)[:20])
            except Exception:
                continue
        for script_url in script_urls:
            try:
                async with session.get(script_url) as response:
                    if response.status < 400:
                        sources.append((script_url, await response.text()))
            except Exception:
                continue
        found: dict[str, set[str]] = {}
        for source, text in sources:
            bases = API_BASE_RE.findall(text)
            for candidate in API_RE.findall(text):
                if candidate.startswith(("http://", "https://")):
                    endpoint = candidate
                elif candidate.startswith("/"):
                    endpoint = urljoin(base_url, candidate)
                else:
                    continue
                found.setdefault(endpoint.split("?", 1)[0], set()).update(classify(endpoint))
            for base in bases:
                for path in POST_PATH_RE.findall(text):
                    if path.startswith("/") and any(key in path.lower() for key in ("start", "run", "token", "score", "submit")):
                        endpoint = urljoin(urljoin(source, base.rstrip("/") + "/"), path.lstrip("/"))
                        found.setdefault(endpoint.split("?", 1)[0], set()).update(classify(endpoint))
        return {
            "site": base_url,
            "sources_scanned": [source for source, _ in sources],
            "candidates": [{"url": url, "labels": sorted(labels)} for url, labels in sorted(found.items())],
            "warning": "Candidates require team confirmation; source-code matches are not a submission contract.",
        }
