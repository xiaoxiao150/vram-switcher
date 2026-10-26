"""VRAM Switcher - AstrBot adapter.

The plugin is intentionally thin: event matching and AstrBot compatibility live
here; process/port orchestration and configuration live in ``vram_switcher/``.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.message_components import Plain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register

try:
    from .vram_switcher import (
        NullProviderHooks,
        Orchestrator,
        SystemAdapter,
        TriggerMatcher,
        load_config,
    )
except ImportError:  # supports loading main.py as a top-level module in local tests
    from vram_switcher import (  # type: ignore
        NullProviderHooks,
        Orchestrator,
        SystemAdapter,
        TriggerMatcher,
        load_config,
    )


_FLAG_KEY = "_vram_switcher_active"
_FLAG_REASON = "_vram_switcher_reason"
_FLAG_INDEXTTS_KILLED = "_vram_switcher_indextts_killed"

# Set by VRAMSwitcherPlugin.__init__. The 2.0 refactor kept the instance
# methods but dropped the module-level helpers that qq_voice_adapter imports
# by module path; these thin wrappers restore that contract without bringing
# back the old hard-coded paths.
_PLUGIN_INSTANCE: Any = None


def _is_indextts_running() -> bool:
    """Compatibility: is the configured TTS service listening?"""
    inst = _PLUGIN_INSTANCE
    if inst is None:
        return False
    try:
        return bool(inst._orchestrator.is_tts_running())
    except Exception as exc:
        logger.debug(f"[VRAMSwitcher] compat _is_indextts_running failed: {exc}")
        return False


def _is_tts_busy() -> bool:
    """Compatibility: is a TTS synthesis window open? Stale flags are cleared."""
    inst = _PLUGIN_INSTANCE
    if inst is None:
        return False
    try:
        return bool(inst._orchestrator.tts_busy())
    except Exception as exc:
        logger.debug(f"[VRAMSwitcher] compat _is_tts_busy failed: {exc}")
        return False


def _is_comfyui_running() -> bool:
    """Compatibility: is ComfyUI's port listening?"""
    inst = _PLUGIN_INSTANCE
    if inst is None:
        return False
    try:
        return bool(inst._orchestrator.is_image_running())
    except Exception as exc:
        logger.debug(f"[VRAMSwitcher] compat _is_comfyui_running failed: {exc}")
        return False


def _is_comfyui_process_running() -> bool:
    """Compatibility: has the ComfyUI process started (port may not be bound yet)?"""
    inst = _PLUGIN_INSTANCE
    if inst is None:
        return False
    try:
        return bool(
            inst._orchestrator.system.service_process_running(
                inst._cfg.service("image")
            )
        )
    except Exception as exc:
        logger.debug(f"[VRAMSwitcher] compat _is_comfyui_process_running failed: {exc}")
        return False


def _kill_indextts_webui() -> bool:
    """Compatibility: stop the TTS webui, but never mid-synthesis.

    qq_voice_adapter calls this every 60 s during the 23:00-07:00 quiet
    window. If a synthesis is in flight, skip it and let the next tick
    retry; the busy flag goes stale after ``flag_stale_seconds`` anyway.
    """
    inst = _PLUGIN_INSTANCE
    if inst is None:
        return False
    try:
        if inst._orchestrator.tts_busy():
            logger.info("[VRAMSwitcher] TTS 合成中，跳过停止 IndexTTS")
            return False
        if not inst._cfg.service_enabled("tts"):
            return False
        return bool(
            inst._orchestrator.system.stop_service(
                "tts", inst._cfg.service("tts")
            )
        )
    except Exception as exc:
        logger.error(f"[VRAMSwitcher] compat _kill_indextts_webui failed: {exc}")
        return False


def _start_indextts_webui(version: str = "2.5") -> bool:
    """Compatibility: start the configured TTS service outside quiet hours."""
    inst = _PLUGIN_INSTANCE
    if inst is None:
        return False
    try:
        if inst._orchestrator.is_tts_running():
            return True
        if inst._orchestrator._night_quiet():
            logger.info("[VRAMSwitcher] 夜间静默：不启动 IndexTTS")
            return False
        return bool(inst._orchestrator._start_tts(version))
    except Exception as exc:
        logger.error(f"[VRAMSwitcher] compat _start_indextts_webui failed: {exc}")
        return False


class _AstrBotProviderHooks(NullProviderHooks):
    """Reserved for users who want provider switching.

    AstrBot's provider selection is deliberately left untouched by default:
    many deployments (including the original one) use a cloud fallback chain
    and a plugin writing ``default_provider_id`` fights that chain.  Override
    this class in a fork if you need the old behavior.
    """

    def __init__(self, plugin: "VRAMSwitcherPlugin") -> None:
        self.plugin = plugin

    async def task_started(self, reason: str) -> None:
        logger.debug(f"[VRAMSwitcher] provider hook: task_started {reason}")

    async def llm_ready(self) -> None:
        logger.debug("[VRAMSwitcher] provider hook: llm_ready")

    async def llm_deferred(self) -> None:
        logger.debug("[VRAMSwitcher] provider hook: llm_deferred")


@register(
    "astrbot_plugin_vram_switcher",
    "VRAM Switcher",
    "本地 GPU 任务协调器：LLM / TTS / 生图服务按需让出显存，完成后恢复。",
    "2.0.4",
)
class VRAMSwitcherPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        global _PLUGIN_INSTANCE
        super().__init__(context)
        self.config = config or {}
        self.plugin_dir = Path(__file__).resolve().parent
        # Derive from the plugin location instead of the process CWD: the
        # same absolute data dir is used by the DSH MCP server and the
        # cross-process coordinator lock.
        self.data_dir = (
            self.plugin_dir.parents[1]
            / "plugin_data"
            / "astrbot_plugin_vram_switcher"
        ).resolve()

        self._cfg = load_config(
            self.config,
            plugin_dir=self.plugin_dir,
            data_dir=self.data_dir,
        )
        self._system = SystemAdapter(logger)
        self._matcher = TriggerMatcher(self._cfg)
        self._orchestrator = Orchestrator(
            self._cfg,
            self._system,
            logger=logger,
            provider=_AstrBotProviderHooks(self),
        )
        self._active_umos: set[str] = set()
        self._self_heal_pending = False
        self._tts_task: asyncio.Task | None = None

        summary = self._cfg.summary()
        logger.info(f"[VRAMSwitcher] profile: {summary['profile']}")
        for warning in summary["warnings"]:
            logger.warning(f"[VRAMSwitcher] {warning}")
        if summary["errors"]:
            for error in summary["errors"]:
                logger.error(f"[VRAMSwitcher] {error}")
            logger.error("[VRAMSwitcher] profile 未就绪，插件保持禁用状态")
            return
        if not self._cfg.enabled:
            logger.info("[VRAMSwitcher] 已在 profile/插件配置中禁用")
            return

        logger.info(
            "[VRAMSwitcher] 已加载: "
            f"services={summary['services']} engine={summary['llm_engine']} "
            f"tts_selector={summary['tts_selector']}"
        )
        _PLUGIN_INSTANCE = self
        try:
            self._tts_task = asyncio.create_task(self._orchestrator.start())
        except RuntimeError:
            logger.debug("[VRAMSwitcher] 当前没有事件循环，TTS 监控延后")
        try:
            status = self._orchestrator.status()
            logger.info(
                "[VRAMSwitcher] 初始状态: "
                f"llm={status['llm_running']} tts={status['tts_running']} "
                f"image={status['image_running']} tts_busy={status['tts_busy']}"
            )
        except Exception as exc:
            logger.debug(f"[VRAMSwitcher] 初始状态读取失败: {exc}")

    # ------------------------------------------------------------------
    # Compatibility helpers used by other plugins
    # ------------------------------------------------------------------
    @property
    def _qwen_restarting(self) -> bool:
        """旧插件读取的属性，映射到新的恢复锁。"""
        return self._orchestrator.recovering

    async def _switch_provider(self, provider_id: str, umos: list[str] | None = None) -> bool:
        """兼容旧接口；provider 切换默认停用。"""
        logger.debug(f"[VRAMSwitcher] provider 切换已停用（忽略 {provider_id}）")
        return True

    async def _recover_after_tts(self) -> None:
        """兼容旧接口：统一恢复流程。"""
        await self._orchestrator.recover("compat")

    async def _wait_qwen_ready(self, timeout: float = 50.0) -> bool:
        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            if self._orchestrator.is_llm_running():
                return True
            await asyncio.sleep(1)
        return False

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _message_text(event: AstrMessageEvent) -> str:
        try:
            return event.message_obj.message_str.strip()
        except Exception:
            return ""

    @staticmethod
    def _chain_text(event: AstrMessageEvent) -> str:
        try:
            result = event.get_result()
            chain = getattr(result, "chain", []) or []
            return "".join(
                str(getattr(component, "text", "") or "")
                for component in chain
                if isinstance(component, Plain)
            )
        except Exception:
            return ""

    async def _prepare_switch(self, event: AstrMessageEvent, reason: str) -> None:
        """兼容旧接口：停本地 LLM，准备 TTS/图像服务。"""
        if not self._cfg.enabled or event is None:
            return
        if event.get_extra(_FLAG_KEY):
            return
        ok = await self._orchestrator.prepare(reason)
        if not ok:
            return
        if reason.startswith(("comfyui:", "tool:")) and self._orchestrator.tts_stopped_for_image:
            event.set_extra(_FLAG_INDEXTTS_KILLED, True)
        event.set_extra(_FLAG_KEY, True)
        event.set_extra(_FLAG_REASON, reason)

    # ------------------------------------------------------------------
    # AstrBot hooks
    # ------------------------------------------------------------------
    @filter.on_llm_request(priority=999999)
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        if not self._cfg.enabled:
            return
        umo = event.unified_msg_origin
        if umo:
            self._active_umos.add(umo)

        # 自愈：上次恢复失败/夜间静默时保持云端 provider；发现本地 LLM 已起来后
        # 调用 provider hook（默认 no-op，不影响普通 fallback 链）。
        if self._orchestrator.llm_deferred and self._orchestrator.is_llm_running():
            logger.info("[VRAMSwitcher] 自愈：本地 LLM 已就绪")
            self._orchestrator.clear_llm_deferred()
            self._self_heal_pending = False
            try:
                await self._orchestrator.provider.llm_ready()
            except Exception:
                pass

        text = self._message_text(event)
        reason = self._matcher.match_text(text)
        if reason:
            await self._prepare_switch(event, reason)

    @filter.on_decorating_result(priority=999999)
    async def on_decorating_result_pre_tts(self, event: AstrMessageEvent) -> None:
        """LLM 回复里带 [语音]/【语音】时，提前停 LLM 给 TTS 腾显存。"""
        if not self._cfg.enabled or event.get_extra(_FLAG_KEY):
            return
        text = self._chain_text(event)
        if not self._matcher.has_reply_marker(text):
            return
        logger.info("[VRAMSwitcher] 检测到回复语音标记，提前准备 TTS 显存")
        await self._prepare_switch(event, "tts:reply_marker")

    @filter.on_using_llm_tool(priority=100000)
    async def on_using_llm_tool(self, event: AstrMessageEvent, tool: Any, tool_args: dict | None) -> None:
        if not self._cfg.enabled:
            return
        name = getattr(tool, "name", "")
        reason = self._matcher.match_tool(name)
        if reason:
            await self._prepare_switch(event, reason)

    @filter.on_llm_tool_respond(priority=999999)
    async def on_llm_tool_respond(
        self,
        event: AstrMessageEvent,
        tool: Any,
        tool_args: dict | None,
        tool_result: Any,
    ) -> None:
        """生图工具返回后提前恢复，让后续配文继续由本地她本人生成。"""
        if not self._cfg.enabled:
            return
        reason = str(event.get_extra(_FLAG_REASON) or "")
        if reason.startswith("tool:") and event.get_extra(_FLAG_KEY):
            if self._orchestrator.recovering:
                logger.info("[VRAMSwitcher] 恢复流程已在跑，跳过提前恢复")
                return
            logger.info(f"[VRAMSwitcher] 生图完成（{reason}），提前恢复")
            event.set_extra(_FLAG_INDEXTTS_KILLED, False)
            await self._orchestrator.recover(reason)
            event.set_extra(_FLAG_KEY, False)
            return

        if event.get_extra(_FLAG_INDEXTTS_KILLED):
            event.set_extra(_FLAG_INDEXTTS_KILLED, False)
            logger.info("[VRAMSwitcher] 生图完成，拉回 TTS 服务待命")
            await self._orchestrator.ensure_tts()

    @filter.on_decorating_result(priority=-999999)
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        """消息处理完（TTS/生图后）：统一恢复流程。"""
        if not self._cfg.enabled or not event.get_extra(_FLAG_KEY):
            return
        reason = event.get_extra(_FLAG_REASON, "unknown")
        if self._orchestrator.recovering:
            logger.info("[VRAMSwitcher] 恢复流程进行中，跳过")
            event.set_extra(_FLAG_KEY, False)
            return
        logger.info(f"[VRAMSwitcher] 消息处理完（原因 {reason}），开始恢复")
        await self._orchestrator.recover(str(reason))
        event.set_extra(_FLAG_KEY, False)

    async def terminate(self) -> None:
        global _PLUGIN_INSTANCE
        if _PLUGIN_INSTANCE is self:
            _PLUGIN_INSTANCE = None
        task = self._tts_task
        self._tts_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await self._orchestrator.stop()
        except Exception:
            pass
        logger.info("[VRAMSwitcher] 插件卸载")
