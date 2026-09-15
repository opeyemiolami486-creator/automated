#!/usr/bin/env python3
"""Validate a team test site's API, using an optional declared contract."""
from __future__ import annotations

from typing import Any

import aiohttp

from authorized_test_client import dotted, endpoint_url, inspect_site


async def validate_contract(base_url: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "site": base_url,
        "requirements_ok": False,
        "checks": [],
        "ready_for_run": False,
    }
    try:
        requirements = await inspect_site(base_url)
        report["requirements"] = requirements
        report["requirements_ok"] = True
    except Exception as exc:
        report["checks"].append({"name": "requirements", "ok": False, "error": str(exc)})
        return report

    endpoints = requirements.get("endpoints")
    identity = requirements.get("identity")
    token = requirements.get("token")
    fields = requirements.get("score_fields")
    for name, value in (("endpoints", endpoints), ("identity", identity), ("token", token), ("score_fields", fields)):
        ok = isinstance(value, dict)
        report["checks"].append({"name": name, "ok": ok, "detail": value if ok else "must be an object"})

    if not all(isinstance(x, dict) for x in (endpoints, identity, token, fields)):
        return report

    for name in ("leaderboard", "start", "submit"):
        value = endpoints.get(name)
        report["checks"].append({"name": f"endpoint.{name}", "ok": isinstance(value, str) and value.startswith("/"), "detail": value})
    identity_field = identity.get("field")
    token_field = token.get("field")
    token_path = token.get("json_path", "token")
    report["checks"].append({"name": "identity.field", "ok": isinstance(identity_field, str) and bool(identity_field), "detail": identity_field})
    report["checks"].append({"name": "token.field", "ok": isinstance(token_field, str) and bool(token_field), "detail": token_field})
    report["checks"].append({"name": "token.json_path", "ok": isinstance(token_path, str) and bool(token_path), "detail": token_path})
    for name in ("score", "height"):
        value = fields.get(name)
        report["checks"].append({"name": f"score_fields.{name}", "ok": isinstance(value, str) and bool(value), "detail": value})

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for name, method, path in (
            ("leaderboard", "GET", endpoints.get("leaderboard")),
            ("start", "POST", endpoints.get("start")),
        ):
            if not isinstance(path, str) or not path.startswith("/"):
                continue
            url = endpoint_url(base_url, path)
            try:
                async with session.request(method, url, json={} if method == "POST" else None) as response:
                    body = await response.json(content_type=None)
                    ok = response.status < 400
                    detail: Any = {"status": response.status}
                    if name == "leaderboard" and ok:
                        rows = body.get("list", body.get("entries", body)) if isinstance(body, dict) else body
                        detail["rows_is_list"] = isinstance(rows, list)
                        ok = ok and isinstance(rows, list)
                    if name == "start" and ok:
                        found = dotted(body, token_path)
                        detail["token_found"] = isinstance(found, str) and bool(found)
                        # The token is intentionally not returned in the report.
                        ok = ok and detail["token_found"]
                    report["checks"].append({"name": f"probe.{name}", "ok": ok, "detail": detail})
            except Exception as exc:
                report["checks"].append({"name": f"probe.{name}", "ok": False, "error": str(exc)})

    report["ready_for_run"] = bool(report["requirements_ok"] and report["checks"] and all(c.get("ok") for c in report["checks"]))
    return report
