"""Stateful coordinator service shared by MCP and native DSH adapters."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from .config import VramConfig
from .orchestrator import Orchestrator
from .system import SystemAdapter


class VramService:
    def __init__(
        self,
        config: VramConfig,
        *,
        lease_seconds: float = 900.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.lease_seconds = max(30.0, float(lease_seconds))
        self.logger = logger or logging.getLogger("vram-switcher")
        self.orchestrator = Orchestrator(
            config,
            SystemAdapter(self.logger),
            logger=self.logger,
        )
        self.lease: dict[str, Any] | None = None
        self._lease_task: asyncio.Task | None = None

    async def start(self) -> None:
        await self.orchestrator.start()

    async def stop(self) -> None:
        lease = self.lease
        self.lease = None
        task = self._lease_task
        self._lease_task = None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if lease:
            try:
                await self.orchestrator.recover("adapter:shutdown", hold_lock=True)
            except Exception as exc:
                self.logger.warning("shutdown recovery failed: %s", exc)
            finally:
                self.orchestrator.release_lease()
        await self.orchestrator.stop()

    def status(self) -> dict[str, Any]:
        result = self.orchestrator.status()
        result["config"] = self.config.summary()
        if self.lease:
            remaining = max(0.0, self.lease["expires_at"] - time.time())
            result["lease"] = {
                "lease_id": self.lease["lease_id"],
                "mode": self.lease["mode"],
                "reason": self.lease["reason"],
                "remaining_seconds": round(remaining, 1),
            }
        else:
            result["lease"] = None
        return result

    async def acquire(
        self,
        mode: str,
        reason: str = "",
        lease_seconds: float | None = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            raise RuntimeError(
                "VRAM coordinator is disabled or profile invalid: "
                + "; ".join(self.config.errors or ["enabled=false"])
            )
        if mode not in {"image", "tts"}:
            raise ValueError("mode must be 'image' or 'tts'")
        if self.lease:
            return {
                "ok": True,
                "already_acquired": True,
                "lease": self.status()["lease"],
            }

        prefix = "tool:adapter" if mode == "image" else "tts:adapter"
        task_reason = f"{prefix}:{reason or 'gpu_task'}"
        acquired = await self.orchestrator.acquire_lease()
        if not acquired:
            raise RuntimeError("Timed out waiting for another AstrBot/DSH GPU task")
        try:
            prepared = await self.orchestrator.prepare(task_reason, hold_lock=True)
            if not prepared:
                raise RuntimeError("Coordinator could not prepare the GPU task")
        except Exception:
            self.orchestrator.release_lease()
            raise

        timeout = self.lease_seconds
        if lease_seconds is not None:
            timeout = min(7200.0, max(30.0, float(lease_seconds)))
        lease_id = uuid.uuid4().hex[:12]
        now = time.time()
        self.lease = {
            "lease_id": lease_id,
            "mode": mode,
            "reason": reason or "gpu_task",
            "started_at": now,
            "expires_at": now + timeout,
            "timeout_seconds": timeout,
        }
        self._lease_task = asyncio.create_task(self._lease_watchdog(lease_id))
        self.logger.info(
            "lease acquired id=%s mode=%s reason=%s timeout=%.0fs",
            lease_id,
            mode,
            reason or "gpu_task",
            timeout,
        )
        return {
            "ok": True,
            "already_acquired": False,
            "lease": self.status()["lease"],
            "message": (
                "GPU lease acquired. Run the local task now, then call "
                "vram_release even on failure."
            ),
        }

    async def _lease_watchdog(self, lease_id: str) -> None:
        try:
            await asyncio.sleep(self.lease_seconds)
            if self.lease and self.lease.get("lease_id") == lease_id:
                self.logger.warning("lease %s timed out; releasing", lease_id)
                await self.release("lease_timeout")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.error("lease watchdog failed: %s", exc)

    async def release(self, reason: str = "adapter_release") -> dict[str, Any]:
        if not self.lease:
            return {"ok": True, "released": False, "message": "No active lease"}
        lease = self.lease
        self.lease = None
        task = self._lease_task
        self._lease_task = None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await self.orchestrator.recover(f"adapter:{reason}", hold_lock=True)
        finally:
            self.orchestrator.release_lease()
        self.logger.info("lease released id=%s reason=%s", lease["lease_id"], reason)
        return {
            "ok": True,
            "released": True,
            "lease_id": lease["lease_id"],
            "status": self.status(),
        }

    async def ensure_tts(self) -> dict[str, Any]:
        await self.orchestrator.ensure_tts()
        return {"ok": True, "status": self.status()}
