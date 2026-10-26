"""Pure trigger matching, independent from AstrBot events."""

from __future__ import annotations

import re
from typing import Any

from .config import VramConfig


class TriggerMatcher:
    def __init__(self, config: VramConfig) -> None:
        self.config = config
        triggers = config.triggers
        self.tts_keywords = [
            str(item) for item in (triggers.get("tts_keywords") or []) if str(item)
        ]
        self.image_prefixes = [
            str(item).lower() for item in (triggers.get("image_prefixes") or []) if str(item)
        ]
        self.image_tool_names = {
            str(item) for item in (triggers.get("image_tool_names") or []) if str(item)
        }
        pattern = str(
            triggers.get("tts_reply_marker_pattern")
            or r"[\[【]\s*语音\s*[\]】]"
        )
        try:
            self.reply_marker = re.compile(pattern)
        except re.error:
            self.reply_marker = re.compile(r"[\[【]\s*语音\s*[\]】]")

    def match_text(self, text: str) -> str | None:
        value = str(text or "").strip()
        if not value:
            return None
        for keyword in self.tts_keywords:
            if keyword in value:
                return f"tts:{keyword}"
        first = value.lower().split(None, 1)[0] if value.split() else ""
        for prefix in self.image_prefixes:
            if first == prefix or first.startswith(prefix):
                return f"comfyui:{first}"
        return None

    def match_tool(self, name: str) -> str | None:
        value = str(name or "")
        return f"tool:{value}" if value in self.image_tool_names else None

    def has_reply_marker(self, text: str) -> bool:
        return bool(self.reply_marker.search(str(text or "")))
