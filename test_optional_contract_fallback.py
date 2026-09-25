import asyncio

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from authorized_test_client import inspect_site


async def board(_request):
    return web.json_response({"list": []})


async def start(_request):
    return web.json_response({"token": "test-token"})


async def score(_request):
    return web.json_response({"rank": 1})


async def main():
    app = web.Application()
    app.router.add_get("/api/dudas/board", board)
    app.router.add_post("/api/dudas/start", start)
    app.router.add_post("/api/dudas/score", score)
    async with TestClient(TestServer(app)) as client:
        report = await inspect_site(str(client.make_url("/")).rstrip("/"))
        assert report["contract_source"] == "inferred-conventional-endpoints"
        assert report["contract_optional"] is True
        assert report["endpoints"]["start"] == "/api/dudas/start"
        assert report["endpoints"]["submit"] == "/api/dudas/score"
        print("optional contract fallback passed")


if __name__ == "__main__":
    asyncio.run(main())
