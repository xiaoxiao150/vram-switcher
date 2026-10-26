#!/usr/bin/env python3
"""JSON-lines daemon used by the native DSH Cordis plugin.

The native plugin deliberately does not reimplement GPU orchestration in
JavaScript.  It owns the Cordis tool surface and this long-lived Python process
owns the same coordinator used by AstrBot/MCP.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vram_switcher import load_config  # noqa: E402
from vram_switcher.service import VramService  # noqa: E402


def send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def handle(service: VramService, message: dict[str, Any]) -> dict[str, Any]:
    request_id = message.get("id")
    op = str(message.get("op") or "")
    try:
        if op == "status":
            data = service.status()
        elif op == "acquire":
            data = await service.acquire(
                str(message.get("mode") or ""),
                str(message.get("reason") or ""),
                message.get("lease_seconds"),
            )
        elif op == "release":
            data = await service.release(str(message.get("reason") or "adapter_release"))
        elif op == "ensure_tts":
            data = await service.ensure_tts()
        elif op == "shutdown":
            data = {"ok": True}
        else:
            raise ValueError(f"Unknown op: {op}")
        return {"id": request_id, "ok": True, "data": data}
    except Exception as exc:
        service.logger.warning("daemon op %s failed: %s", op, exc)
        return {"id": request_id, "ok": False, "error": str(exc)}


async def run(args: argparse.Namespace) -> None:
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
        plugin_dir=ROOT,
        data_dir=data_dir,
    )
    if config.errors:
        for error in config.errors:
            logger.error(error)
    else:
        logger.info("profile loaded: %s", profile)

    service = VramService(config, lease_seconds=args.lease_seconds, logger=logger)
    await service.start()
    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                send({"id": None, "ok": False, "error": f"Invalid JSON: {exc}"})
                continue
            response = await handle(service, message)
            send(response)
            if str(message.get("op") or "") == "shutdown":
                break
    finally:
        await service.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="VRAM Switcher native DSH daemon")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--lease-seconds", type=float, default=900.0)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
