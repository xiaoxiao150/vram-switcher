"""The local-GPU task coordinator state machine.

It knows three roles, not specific products:

* ``llm``   - the service that normally occupies VRAM and must step aside.
* ``tts``   - an on-demand voice service with busy/ready flag files.
* ``image`` - an on-demand image service with a queue endpoint.

Actual commands, ports and process names come from the profile.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import time
from pathlib import Path
from typing import Any

from .config import VramConfig
from .lock import FileLock


class NullProviderHooks:
    """Optional integration point for switching an LLM provider.

    The current production setup keeps provider selection in AstrBot's normal
    fallback chain, so these hooks are no-ops.  Integrators can subclass this.
    """

    async def task_started(self, reason: str) -> None:
        return None

    async def llm_ready(self) -> None:
        return None

    async def llm_deferred(self) -> None:
        return None


class Orchestrator:
    def __init__(
        self,
        config: VramConfig,
        system: Any,
        logger: Any = None,
        provider: Any = None,
    ) -> None:
        self.config = config
        self.system = system
        self.logger = logger
        self.provider = provider or NullProviderHooks()
        self._lock = asyncio.Lock()
        self._tts_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._tts_engaged = False
        self._recovering = False
        self._image_active = False
        self._tts_stopped_for_image = False
        self._llm_deferred = False
        self._last_reason = ""
        self._ready_flag: Path | None = None
        self._busy_flag: Path | None = None
        self._prepare_tts_flag_paths()
        self._file_lock = FileLock(
            self.config.raw.get("lock_file")
            or self.config.data_dir / "coordinator.lock"
        )
        self._lock_timeout = float(self.config.raw.get("lock_timeout_seconds") or 600)

    # ------------------------------------------------------------------
    # Logging / basic state
    # ------------------------------------------------------------------
    def _log(self, level: str, message: str) -> None:
        logger = self.logger
        if logger is None:
            return
        method = getattr(logger, level, None)
        if callable(method):
            method(f"[VRAMSwitcher] {message}")

    @property
    def recovering(self) -> bool:
        return self._recovering

    @property
    def llm_deferred(self) -> bool:
        return self._llm_deferred

    @property
    def tts_stopped_for_image(self) -> bool:
        return self._tts_stopped_for_image

    def is_llm_running(self) -> bool:
        # 本地 LLM 的“已恢复”判据是进程存在；端口刚起但模型未加载完成时，
        # 不应该被当成可切回的状态。
        return self.config.service_enabled("llm") and self.system.service_process_running(
            self.config.service("llm")
        )

    def is_tts_running(self) -> bool:
        return self.config.service_enabled("tts") and self.system.service_running(
            self.config.service("tts")
        )

    def is_image_running(self) -> bool:
        return self.config.service_enabled("image") and self.system.service_running(
            self.config.service("image")
        )

    # ------------------------------------------------------------------
    # Flag files
    # ------------------------------------------------------------------
    def _prepare_tts_flag_paths(self) -> None:
        service = self.config.service("tts")
        busy = service.get("busy_flag")
        ready = service.get("ready_flag")
        self._busy_flag = Path(str(busy)) if busy else None
        self._ready_flag = Path(str(ready)) if ready else None

    def _flag_age(self, path: Path) -> float | None:
        try:
            return time.time() - path.stat().st_mtime
        except OSError:
            return None

    def tts_busy(self) -> bool:
        path = self._busy_flag
        if path is None:
            return False
        if not path.exists():
            return False
        age = self._flag_age(path)
        if age is None:
            return False
        stale = float(self.config.service("tts").get("flag_stale_seconds") or 900)
        if age > stale:
            self._log("warning", f"TTS 合成信号残留（{age:.0f}s），清理")
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            self.clear_ready()
            return False
        return True

    def touch_ready(self) -> None:
        path = self._ready_flag
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(int(time.time())), encoding="ascii")
        except OSError:
            pass

    def clear_ready(self) -> None:
        path = self._ready_flag
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Service lifecycle
    # ------------------------------------------------------------------
    def _start_llm(self) -> bool:
        service = self.config.service("llm")
        start = service.get("start") if isinstance(service.get("start"), dict) else {}
        profiles = start.get("profiles") if isinstance(start.get("profiles"), dict) else {}
        engine = str(start.get("engine") or "moe")
        profile = profiles.get(engine)
        if not isinstance(profile, dict):
            profile = start if start.get("args") else None
        if not isinstance(profile, dict):
            self._log("warning", f"llm: 找不到引擎 {engine!r} 的启动配置")
            return False
        return self.system.start_service("llm", service, profile)

    def _start_tts(self, version: str) -> bool:
        if self._night_quiet():
            self._log("info", "夜间静默：不启动 TTS 服务")
            return False
        service = self.config.service("tts")
        versions = service.get("versions")
        if not isinstance(versions, dict):
            self._log("warning", "tts: 没有 versions，无法启动")
            return False
        profile = versions.get(version)
        if not isinstance(profile, dict):
            default = str(service.get("default_version") or "2.5")
            profile = versions.get(default)
            version = default
        if not isinstance(profile, dict):
            self._log("warning", f"tts: 找不到版本 {version!r} 的启动配置")
            return False
        self._log("info", f"tts: 启动版本 v{version}")
        return self.system.start_service("tts", service, profile)

    def _start_image(self) -> bool:
        service = self.config.service("image")
        profile = service.get("start")
        if not isinstance(profile, dict):
            self._log("warning", "image: 没有启动配置")
            return False
        return self.system.start_service("image", service, profile)

    def _stop_llm(self) -> bool:
        service = self.config.service("llm")
        if not self.config.service_enabled("llm"):
            return True
        return self.system.stop_service("llm", service)

    def _stop_tts(self) -> bool:
        service = self.config.service("tts")
        if not self.config.service_enabled("tts"):
            return True
        return self.system.stop_service("tts", service)

    def _stop_image(self) -> bool:
        service = self.config.service("image")
        if not self.config.service_enabled("image"):
            return True
        return self.system.stop_service("image", service)

    def _stop_aux(self) -> None:
        for service in self.config.aux_services():
            key = str(service.get("key") or "aux")
            self.system.stop_service(key, service)

    def _night_quiet(self) -> bool:
        service = self.config.service("llm")
        quiet = service.get("night_quiet")
        if not isinstance(quiet, dict) or not quiet.get("enabled", True):
            return False
        start = str(quiet.get("start") or "23:00")
        end = str(quiet.get("end") or "07:00")

        def minutes(value: str) -> int:
            try:
                hour, minute = value.split(":", 1)
                return max(0, min(23, int(hour))) * 60 + max(0, min(59, int(minute)))
            except Exception:
                return 0

        now = _dt.datetime.now()
        current = now.hour * 60 + now.minute
        start_m, end_m = minutes(start), minutes(end)
        if start_m <= end_m:
            return start_m <= current < end_m
        return current >= start_m or current < end_m

    def _tts_version_for_recovery(self) -> str:
        service = self.config.service("tts")
        selector = self.config.tts_selector
        detected = None
        if selector == "auto":
            detect = getattr(self.system, "detect_version", None)
            if callable(detect):
                try:
                    detected = detect(service)
                except Exception as exc:
                    self._log("debug", f"tts 版本探测失败: {exc}")
            self._log(
                "info",
                f"tts: auto 模式，检测到 v{detected or 'None'}，"
                f"目标 v{detected or service.get('default_version') or '2.5'}",
            )
        return self.config.tts_version(detected)

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------
    async def acquire_lease(self, timeout: float | None = None) -> bool:
        """Acquire the cross-process coordinator lease and keep it until release."""
        return await asyncio.to_thread(
            self._file_lock.acquire,
            self._lock_timeout if timeout is None else timeout,
        )

    def release_lease(self) -> None:
        self._file_lock.release()

    async def prepare(self, reason: str, *, hold_lock: bool = False) -> bool:
        """Free VRAM and prepare the service needed by ``reason``.

        The file lock serializes AstrBot and DSH when both adapters share this
        profile/data directory.  With ``hold_lock=True`` the caller keeps the
        lease until it calls ``recover(..., hold_lock=True)`` and
        ``release_lease()``.
        """
        if not self.config.enabled:
            return False
        if hold_lock:
            acquired = await asyncio.to_thread(self._file_lock.acquire, self._lock_timeout)
            if not acquired:
                self._log("warning", "准备任务时等待协调锁超时")
                return False
            return await self._prepare_locked(reason)
        acquired = await asyncio.to_thread(self._file_lock.acquire, self._lock_timeout)
        if not acquired:
            self._log("warning", "等待协调锁超时，另一个 AstrBot/DSH 任务仍在运行")
            return False
        try:
            return await self._prepare_locked(reason)
        finally:
            self._file_lock.release()

    async def _prepare_locked(self, reason: str) -> bool:
        kind = "image" if str(reason).startswith(("comfyui:", "tool:")) else "tts"
        async with self._lock:
            self._last_reason = str(reason)
            self._log("info", f"触发切换，原因: {reason}（类型 {kind}）")
            try:
                await self.provider.task_started(str(reason))
            except Exception as exc:
                self._log("debug", f"provider.task_started 失败: {exc}")

            if self.is_llm_running():
                self._log("info", "停止本地 LLM 释放显存")
                if not self._stop_llm():
                    self._log("warning", "LLM 停止超时，继续执行（可能 OOM）")
                else:
                    self._log("info", "LLM 已停，显存已释放")

            self._stop_aux()

            if kind == "tts" and self.is_image_running():
                self._log("warning", "TTS 准备时图像服务仍在运行，先等待队列并停止它")
                await asyncio.to_thread(self.system.drain_queue, self.config.service("image"))
                self._stop_image()
                self._image_active = False

            if kind == "tts" and self._image_active:
                self._log("warning", "图像任务进行中，拒绝 TTS 预切换以免三方抢显存")
                return False
            if kind == "image":
                self._image_active = True
                if (
                    bool(self.config.policy_value("free_tts_before_image", True))
                    and self.is_tts_running()
                ):
                    self._log("info", "生图前停止 TTS，释放驻留显存")
                    if self._stop_tts():
                        self._tts_stopped_for_image = True
                image = self.config.service("image")
                if self.config.service_enabled("image"):
                    image_up = (
                        self.system.service_running(image)
                        or self.system.service_process_running(image)
                    )
                    if not image_up:
                        self._log("info", "生图触发，启动图像服务")
                        self._start_image()
                    if not self.system.service_running(image):
                        ready = await asyncio.to_thread(self.system.wait_ready, image)
                        if ready:
                            self._log("info", "图像服务已就绪")
                        else:
                            self._log("warning", "图像服务在超时前未就绪，生图可能失败")
            else:
                if self.config.service_enabled("tts") and not self.is_tts_running():
                    if self._night_quiet():
                        self._log("info", "夜间静默：不启动 TTS 服务")
                        return False
                    self._log("info", "TTS 窗口：补拉语音服务")
                    self._start_tts(self.config.tts_version())
                if self.config.service_enabled("tts"):
                    self.touch_ready()
            return True

    async def ensure_tts(self) -> None:
        """Bring the configured TTS service online if it is not running."""
        if not self.config.enabled or not self.config.service_enabled("tts"):
            return
        if self._night_quiet():
            self._log("info", "夜间静默：不启动 TTS 服务")
            return
        acquired = await asyncio.to_thread(self._file_lock.acquire, self._lock_timeout)
        if not acquired:
            self._log("warning", "ensure_tts 等待协调锁超时")
            return
        try:
            if self.is_tts_running():
                return
            await asyncio.to_thread(self._start_tts, self.config.tts_version())
        finally:
            self._file_lock.release()

    def clear_llm_deferred(self) -> None:
        self._llm_deferred = False

    async def recover(self, reason: str = "", *, hold_lock: bool = False) -> None:
        """Drain/stop disposable services, then bring the local LLM back."""
        if not self.config.enabled or self._recovering:
            return
        if hold_lock:
            acquired = await asyncio.to_thread(self._file_lock.acquire, self._lock_timeout)
            if not acquired:
                self._log("warning", "恢复任务时等待协调锁超时")
                return
            await self._recover_locked(reason)
            return
        acquired = await asyncio.to_thread(self._file_lock.acquire, self._lock_timeout)
        if not acquired:
            self._log("warning", "恢复等待协调锁超时，稍后由下一次请求自愈")
            return
        try:
            await self._recover_locked(reason)
        finally:
            self._file_lock.release()

    async def _recover_locked(self, reason: str = "") -> None:
        async with self._lock:
            if self._recovering:
                return
            self._recovering = True
            try:
                version = self._tts_version_for_recovery()
                image = self.config.service("image")
                if self.config.service_enabled("image") and (
                    self.system.service_running(image)
                    or self.system.service_process_running(image)
                ):
                    self._log("info", "恢复：等待图像队列后停止图像服务")
                    await asyncio.to_thread(self.system.drain_queue, image)
                    self._stop_image()
                self._image_active = False

                keep_tts = bool(self.config.policy_value("keep_tts_resident", True))
                if self.config.service_enabled("tts") and not keep_tts:
                    self._log("info", "恢复：停止 TTS（keep_resident=false）")
                    self._stop_tts()
                    await asyncio.sleep(3)

                if (
                    self.config.service_enabled("tts")
                    and keep_tts
                    and not self.is_tts_running()
                ):
                    self._start_tts(version)

                if self.config.service_enabled("llm"):
                    if self._night_quiet():
                        self._log("info", "夜间静默：不启动本地 LLM")
                        self._llm_deferred = True
                        try:
                            await self.provider.llm_deferred()
                        except Exception:
                            pass
                    else:
                        self._log("info", "恢复：启动本地 LLM")
                        self._start_llm()
                        raw_wait = self.config.service("llm").get("recovery_wait_seconds")
                        wait = 45.0 if raw_wait is None else max(0.0, float(raw_wait))
                        if wait > 0:
                            await asyncio.sleep(wait)
                        if self.is_llm_running():
                            self._log("info", "本地 LLM 已恢复")
                            self._llm_deferred = False
                            try:
                                await self.provider.llm_ready()
                            except Exception:
                                pass
                        else:
                            self._log("warning", "本地 LLM 恢复失败，保持当前 provider（下次请求自愈）")
                            self._llm_deferred = True
                            try:
                                await self.provider.llm_deferred()
                            except Exception:
                                pass

                if (
                    self.config.service_enabled("tts")
                    and keep_tts
                    and not self.is_tts_running()
                ):
                    self._start_tts(version)
                self._tts_stopped_for_image = False
                self._log("info", f"恢复完成（原因 {reason or 'unspecified'}）")
            finally:
                self._recovering = False

    # ------------------------------------------------------------------
    # Automatic TTS watcher
    # ------------------------------------------------------------------
    async def tts_watch(self) -> None:
        interval = float(self.config.policy_value("tts_poll_seconds", 2))
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(0.5, interval))
                return
            except asyncio.TimeoutError:
                pass
            try:
                busy = self.tts_busy()
                if busy and not self._tts_engaged:
                    if self._recovering:
                        continue
                    if self._image_active:
                        self._log("debug", "图像任务进行中，暂不响应自动 TTS 信号")
                        continue
                    self._tts_engaged = True
                    self._log("info", "检测到自动 TTS 合成，准备释放显存")
                    await self.prepare("tts:auto")
                elif not busy and self._tts_engaged:
                    self._tts_engaged = False
                    self.clear_ready()
                    if not self._recovering:
                        self._log("info", "自动 TTS 合成完成，恢复本地 LLM")
                        await self.recover("tts:auto")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log("error", f"TTS 信号监控异常: {exc}")

    async def start(self) -> None:
        if self._tts_task and not self._tts_task.done():
            return
        if not self.config.enabled:
            return
        self._stop_event.clear()
        self._tts_task = asyncio.create_task(self.tts_watch())
        self._log("info", "TTS 信号监控已启动")

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._tts_task
        self._tts_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self.clear_ready()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "reason": self._last_reason,
            "llm_running": self.is_llm_running() if self.config.enabled else False,
            "tts_running": self.is_tts_running() if self.config.enabled else False,
            "image_running": self.is_image_running() if self.config.enabled else False,
            "tts_busy": self.tts_busy() if self.config.enabled else False,
            "tts_engaged": self._tts_engaged,
            "image_active": self._image_active,
            "recovering": self._recovering,
            "llm_deferred": self._llm_deferred,
        }
