import asyncio
import json
import tempfile
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from mock_webcade_api import create_app


async def run_tests():
    with tempfile.TemporaryDirectory() as directory:
        import mock_webcade_api
        old_state = mock_webcade_api.STATE_FILE
        mock_webcade_api.STATE_FILE = Path(directory) / "state.json"
        try:
            async with TestClient(TestServer(create_app())) as client:
                requirements = await client.get("/api/dudas/requirements")
                assert requirements.status == 200
                contract = await requirements.json()
                assert contract["identity"]["field"] == "address"
                assert contract["endpoints"]["start"] == "/api/dudas/start"

                board = await client.get("/api/dudas/board?limit=10")
                assert (await board.json())["list"] == []

                start = await client.post("/api/dudas/start", json={})
                assert start.status == 200
                token = (await start.json())["token"]
                payload = {
                    "address": "judge-player",
                    "token": token,
                    "score": 100000,
                    "height": 2000,
                    "coins": 100,
                    "toads": 20,
                    "combo": 2,
                }
                submitted = await client.post("/api/dudas/score", json=payload)
                assert submitted.status == 200
                result = await submitted.json()
                assert result["rank"] == 1
                assert result["score"]["height"] == 2000
                assert "." in result["score"]["submitted_at"]

                replay = await client.post("/api/dudas/score", json=payload)
                assert replay.status == 409

                missing = await client.post("/api/dudas/score", json={"address": "x"})
                assert missing.status == 400

                invalid = await client.post("/api/dudas/score", json={**payload, "token": "wrong"})
                assert invalid.status == 401

                board = await client.get("/api/dudas/board?limit=10")
                rows = (await board.json())["list"]
                assert rows[0]["score"] == 100000
        finally:
            mock_webcade_api.STATE_FILE = old_state


if __name__ == "__main__":
    asyncio.run(run_tests())
    print("mock API integration tests passed")
