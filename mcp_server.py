#!/usr/bin/env python3
"""MCP stdio adapter for DSH and other MCP-capable agents.

The protocol is intentionally implemented with the standard library only, so
DSH's bundled Python can run it without installing a framework.  The actual
GPU orchestration is the same ``vram_switcher`` core used by the AstrBot plugin.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from vram_switcher import load_config
from vram_switcher.service import VramService

SERVER_NAME = "vram-switcher"
SERVER_VERSION = "2.0.2"
PROTOCOL_VERSION = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "vram_status",
        "description": (
            "Show the local-GPU coordinator status: configured services, whether "
            "LLM/TTS/image are running, current lease, and last task state. Call "
            "this before deciding to acquire or release GPU resources."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "vram_acquire",
        "description": (
            "Acquire the local GPU coordinator lease before starting a local "
            "ComfyUI/image or TTS job that must not compete with the local LLM. "
            "This may stop the local LLM and start the requested service; it can "
            "take minutes on a cold ComfyUI start. The lease is held until "
            "vram_release is called, or until the server-side lease timeout."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["image", "tts"],
                    "description": "The local GPU task type.",
                },
                "reason": {
                    "type": "string",
                    "description": "Short reason shown in logs, e.g. 'run SDXL workflow'.",
                },
                "lease_seconds": {
                    "type": "number",
                    "description": "Optional lease timeout for a long GPU job (30-7200 seconds).",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "vram_release",
        "description": (
            "Release a lease created by vram_acquire. This stops leftover image/TTS "
            "services as configured and restores the local LLM. Call it in a "
            "finally-style path after the GPU job finishes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional release reason for logs.",
                }
            },
        },
    },
    {
        "name": "vram_ensure_tts",
        "description": (
            "Ensure the configured TTS service is running without starting a full "
            "task lease. Rarely needed; vram_acquire(mode='tts') is preferred."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class McpServer:
    def __init__(self, service: VramService) -> None:
        self.service = service

    async def dispatch(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return None
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        if method == "initialize":
            requested = params.get("protocolVersion")
            return self._result(request_id, {
                "protocolVersion": requested or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Local GPU coordinator. Before running a local ComfyUI/image "
                    "or TTS job, call vram_acquire with mode=image or mode=tts. "
                    "Call vram_release after the job, including on failure. "
                    "Do not acquire while the current DSH turn is using the same "
                    "local LLM provider; that would stop the model under you."
                ),
            })
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": TOOLS})
        if method == "resources/list":
            return self._result(request_id, {"resources": []})
        if method == "prompts/list":
            return self._result(request_id, {"prompts": []})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            try:
                if name == "vram_status":
                    payload = self.service.status()
                elif name == "vram_acquire":
                    payload = await self.service.acquire(
                        str(arguments.get("mode") or ""),
                        str(arguments.get("reason") or ""),
                        arguments.get("lease_seconds"),
                    )
                elif name == "vram_release":
                    payload = await self.service.release(
                        str(arguments.get("reason") or "mcp_release")
                    )
                elif name == "vram_ensure_tts":
                    payload = await self.service.ensure_tts()
                else:
                    raise ValueError(f"Unknown tool: {name}")
                return self._result(request_id, {
                    "content": [{
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False, indent=2),
                    }],
                    "isError": False,
                })
            except Exception as exc:
                self.service.logger.warning("tool %s failed: %s", name, exc)
                return self._result(request_id, {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                })
        if request_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    async def run(self) -> None:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._send({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                })
                continue
            response = await self.dispatch(message)
            if response is not None:
                self._send(response)

    @staticmethod
    def _send(message: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VRAM Switcher MCP stdio server")
    parser.add_argument(
        "--profile",
        default=str(Path(__file__).with_name("profile.local.json")),
        help="Path to profile.local.json",
    )
    parser.add_argument("--data-dir", default=None, help="Shared flag/lock directory")
    parser.add_argument("--lease-seconds", type=float, default=900.0)
    parser.add_argument("--log-level", default="INFO")
    return parser


async def async_main(args: argparse.Namespace) -> None:
    profile = Path(args.profile).expanduser().resolve()
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else profile.parent
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        stream=sys.stderr,
        format="[%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("vram-switcher")
    config = load_config(
        {"profile_path": str(profile)},
        plugin_dir=Path(__file__).resolve().parent,
        data_dir=data_dir,
    )
    if config.errors:
        for error in config.errors:
            logger.error(error)
    else:
        logger.info("profile loaded: %s", profile)
    service = VramService(config, lease_seconds=args.lease_seconds, logger=logger)
    await service.start()
    server = McpServer(service)
    try:
        await server.run()
    finally:
        await service.stop()


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
