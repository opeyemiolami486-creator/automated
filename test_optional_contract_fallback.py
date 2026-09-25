import asyncio

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from authorized_test_client import inspect_site


async def main():
    app = web.Application()
    async with TestClient(TestServer(app)) as client:
        report = await inspect_site(str(client.make_url("/")).rstrip("/"))
        assert report["contract_source"] == "undiscovered"
        assert report["contract_optional"] is True
        assert report["capabilities"]["bot_submission"] is False
        print("unknown site remains read-only until it publishes a contract")


if __name__ == "__main__":
    asyncio.run(main())
