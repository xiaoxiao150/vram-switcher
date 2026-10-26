"""Configuration loading and validation.

The public profile is intentionally data-only: process names, ports, start
commands and flag paths all live in a JSON file.  The AstrBot plugin config
only carries user-facing switches and a path to that profile.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

DEFAULT_PROFILE_NAME = "profile.local.json"

DEFAULT_TRIGGERS: dict[str, Any] = {
    "tts_keywords": ["语音"],
    "image_prefixes": [
        "aimg",
        "img2img",
        "comfyuioutput",
        "tomatodecrypt",
        "teeee",
        "addserver",
        "encrypt",
        "t2l",
        "workflow",
        "改图",
        "/改图",
    ],
    "image_tool_names": [
        "comfyui_txt2img",
        "generate_selfie",
        "generate_image",
        "generate_video",
        "pc_generate_photo",
        "edit_image",
    ],
    "tts_reply_marker_pattern": r"[\[【]\s*语音\s*[\]】]",
}

DEFAULT_POLICY: dict[str, Any] = {
    "free_tts_before_image": True,
    "keep_tts_resident": True,
    "tts_poll_seconds": 2,
}


class ConfigError(ValueError):
    """Raised when the profile cannot be loaded or is structurally invalid."""


def _plugin_value(config: Any, key: str, default: Any = None) -> Any:
    try:
        return config.get(key, default)
    except Exception:
        return default


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_vars(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: _resolve_vars(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_vars(v, variables) for v in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables:
            return variables[key]
        return os.environ.get(key, match.group(0))

    return _VAR_PATTERN.sub(replace, value)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"无法读取 profile: {path} ({exc})") from exc
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"profile 不是合法 JSON: {path} ({exc})") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"profile 顶层必须是 JSON 对象: {path}")
    return value


class VramConfig:
    """Resolved profile plus AstrBot overrides."""

    def __init__(
        self,
        raw: dict[str, Any],
        *,
        profile_path: Path,
        plugin_dir: Path,
        data_dir: Path,
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
    ) -> None:
        self.raw = raw
        self.profile_path = profile_path
        self.plugin_dir = plugin_dir
        self.data_dir = data_dir
        self.warnings = warnings or []
        self.errors = errors or []

    @property
    def enabled(self) -> bool:
        return bool(self.raw.get("enabled", True)) and not self.errors

    def service(self, name: str) -> dict[str, Any]:
        services = self.raw.get("services")
        value = services.get(name) if isinstance(services, dict) else None
        return value if isinstance(value, dict) else {}

    def service_enabled(self, name: str) -> bool:
        return bool(self.service(name).get("enabled", False))

    def aux_services(self) -> list[dict[str, Any]]:
        values = self.raw.get("aux_services")
        if not isinstance(values, list):
            return []
        return [
            value for value in values
            if isinstance(value, dict) and value.get("enabled", True)
        ]

    @property
    def triggers(self) -> dict[str, Any]:
        value = self.raw.get("triggers")
        return value if isinstance(value, dict) else {}

    @property
    def policy(self) -> dict[str, Any]:
        value = self.raw.get("policy")
        return value if isinstance(value, dict) else {}

    def policy_value(self, key: str, default: Any = None) -> Any:
        return self.policy.get(key, default)

    @property
    def llm_engine(self) -> str:
        start = self.service("llm").get("start")
        if isinstance(start, dict):
            return str(start.get("engine") or "moe")
        return "moe"

    @property
    def tts_selector(self) -> str:
        service = self.service("tts")
        return str(service.get("version_selector") or service.get("default_version") or "2.5")

    def tts_version(self, detected: str | None = None) -> str:
        selector = self.tts_selector
        if selector == "auto":
            return detected or str(self.service("tts").get("default_version") or "2.5")
        return selector

    def summary(self) -> dict[str, Any]:
        services = {
            name: self.service_enabled(name)
            for name in ("llm", "tts", "image")
        }
        return {
            "enabled": self.enabled,
            "profile": str(self.profile_path),
            "services": services,
            "llm_engine": self.llm_engine,
            "tts_selector": self.tts_selector,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def _merge_plugin_overrides(profile: dict[str, Any], plugin_config: Any) -> dict[str, Any]:
    """Apply the small AstrBot-facing switches on top of the profile."""

    def as_bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "on"}

    merged = copy.deepcopy(profile)

    triggers = merged.setdefault("triggers", {})
    if not isinstance(triggers, dict):
        triggers = {}
        merged["triggers"] = triggers
    for key, source in (
        ("tts_keywords", "extra_tts_keywords"),
        ("image_prefixes", "extra_comfyui_prefixes"),
    ):
        extra = _plugin_value(plugin_config, source, [])
        if isinstance(extra, list):
            existing = triggers.setdefault(key, [])
            if not isinstance(existing, list):
                existing = []
                triggers[key] = existing
            for item in extra:
                text = str(item).strip()
                if text and text not in existing:
                    existing.append(text)

    policy = merged.setdefault("policy", {})
    if not isinstance(policy, dict):
        policy = {}
        merged["policy"] = policy
    free_tts = _plugin_value(plugin_config, "free_indextts_before_gen", None)
    if free_tts is not None:
        policy["free_tts_before_image"] = as_bool(
            free_tts, bool(policy.get("free_tts_before_image", True))
        )
    keep_tts = _plugin_value(plugin_config, "keep_indextts_resident", None)
    if keep_tts is not None:
        policy["keep_tts_resident"] = as_bool(
            keep_tts, bool(policy.get("keep_tts_resident", True))
        )

    services = merged.setdefault("services", {})
    if not isinstance(services, dict):
        services = {}
        merged["services"] = services

    engine = _plugin_value(plugin_config, "local_llm_engine", None)
    if engine:
        llm = services.setdefault("llm", {})
        if isinstance(llm, dict):
            start = llm.setdefault("start", {})
            if isinstance(start, dict):
                start["engine"] = str(engine).strip().lower()

    version = _plugin_value(plugin_config, "index_tts_version", None)
    if version:
        tts = services.setdefault("tts", {})
        if isinstance(tts, dict):
            tts["version_selector"] = str(version).strip().lower()

    enabled = _plugin_value(plugin_config, "enabled", None)
    if enabled is not None:
        merged["enabled"] = as_bool(enabled, True)
    return merged


def _validate(config: VramConfig) -> None:
    if not config.enabled:
        return
    services = config.raw.get("services")
    if not isinstance(services, dict) or not services:
        raise ConfigError("profile 缺少 services")

    found = False
    for name in ("llm", "tts", "image"):
        service = config.service(name)
        if not config.service_enabled(name):
            continue
        found = True
        if not isinstance(service, dict):
            raise ConfigError(f"services.{name} 必须是对象")
        if not service.get("process_names") and not service.get("cmdline_contains"):
            raise ConfigError(f"services.{name} 缺少 process_names/cmdline_contains，拒绝做无边界杀进程")
        if name == "tts":
            versions = service.get("versions")
            if not isinstance(versions, dict) or not versions:
                config.warnings.append("services.tts 没有 versions，TTS 只能停不能启动")
            if not service.get("busy_flag"):
                config.warnings.append("services.tts 没有 busy_flag，自动 TTS 联动不会生效")
        if name == "llm":
            start = service.get("start")
            if not isinstance(start, dict):
                config.warnings.append("services.llm 没有 start，任务结束后无法自动恢复本地 LLM")
            elif not isinstance(start.get("profiles"), dict) and not start.get("args"):
                config.warnings.append("services.llm.start 没有 profiles/args")
        if name == "image":
            if not isinstance(service.get("start"), dict):
                config.warnings.append("services.image 没有 start，生图任务不会自动拉起 ComfyUI")
    if not found:
        raise ConfigError("services.llm/tts/image 全部被禁用，插件无事可做")


def load_config(
    plugin_config: Any,
    *,
    plugin_dir: Path | str,
    data_dir: Path | str,
) -> VramConfig:
    """Load profile.local.json, resolve variables and apply plugin switches."""
    plugin_dir = Path(plugin_dir).resolve()
    data_dir = Path(data_dir).resolve()
    variables = {
        "PLUGIN_DIR": str(plugin_dir),
        "DATA_DIR": str(data_dir),
    }

    raw_profile_name = _plugin_value(plugin_config, "profile_path", DEFAULT_PROFILE_NAME)
    profile_name = _resolve_vars(str(raw_profile_name or DEFAULT_PROFILE_NAME), variables)
    profile_path = Path(profile_name).expanduser()
    if not profile_path.is_absolute():
        profile_path = plugin_dir / profile_path

    if not profile_path.is_file():
        return VramConfig(
            {},
            profile_path=profile_path,
            plugin_dir=plugin_dir,
            data_dir=data_dir,
            errors=[
                f"找不到 profile: {profile_path}。请复制 profile.example.json 为 "
                "profile.local.json 并填写路径/端口/启动命令。"
            ],
        )

    try:
        profile = _read_json(profile_path)
    except ConfigError as exc:
        return VramConfig(
            {},
            profile_path=profile_path,
            plugin_dir=plugin_dir,
            data_dir=data_dir,
            errors=[str(exc)],
        )

    profile = _resolve_vars(profile, variables)
    merged = _merge_plugin_overrides(profile, plugin_config)
    triggers = merged.get("triggers")
    if not isinstance(triggers, dict):
        triggers = {}
    merged["triggers"] = _deep_merge(DEFAULT_TRIGGERS, triggers)
    policy = merged.get("policy")
    if not isinstance(policy, dict):
        policy = {}
    merged["policy"] = _deep_merge(DEFAULT_POLICY, policy)

    config = VramConfig(
        merged,
        profile_path=profile_path,
        plugin_dir=plugin_dir,
        data_dir=data_dir,
    )
    try:
        _validate(config)
    except ConfigError as exc:
        config.errors.append(str(exc))
    return config
