import logging
import unittest

from mcp_server import McpServer


class FakeService:
    def __init__(self):
        self.logger = logging.getLogger("test-mcp")
        self.calls = []

    def status(self):
        return {"enabled": True, "llm_running": True, "lease": None}

    async def acquire(self, mode, reason="", lease_seconds=None):
        self.calls.append(("acquire", mode, reason))
        return {"ok": True, "lease": {"lease_id": "test", "mode": mode}}

    async def release(self, reason="mcp_release"):
        self.calls.append(("release", reason))
        return {"ok": True, "released": True}

    async def ensure_tts(self):
        self.calls.append(("ensure_tts",))
        return {"ok": True}


class McpProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = FakeService()
        self.server = McpServer(self.service)

    async def test_initialize(self):
        response = await self.server.dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
        })
        result = response["result"]
        self.assertEqual(result["serverInfo"]["name"], "vram-switcher")
        self.assertIn("vram_acquire", result["instructions"])

    async def test_tools_list(self):
        response = await self.server.dispatch({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertIn("vram_status", names)
        self.assertIn("vram_acquire", names)
        self.assertIn("vram_release", names)

    async def test_status_and_acquire(self):
        status = await self.server.dispatch({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "vram_status", "arguments": {}},
        })
        self.assertFalse(status["result"]["isError"])
        acquired = await self.server.dispatch({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {
                "name": "vram_acquire",
                "arguments": {"mode": "image", "reason": "unit"},
            },
        })
        self.assertFalse(acquired["result"]["isError"])
        self.assertIn(("acquire", "image", "unit"), self.service.calls)

    async def test_unknown_tool_is_error(self):
        response = await self.server.dispatch({
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        })
        self.assertTrue(response["result"]["isError"])

    async def test_notification_is_ignored(self):
        response = await self.server.dispatch({
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        self.assertIsNone(response)


if __name__ == "__main__":
    unittest.main()
