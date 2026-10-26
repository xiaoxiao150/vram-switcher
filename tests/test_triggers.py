import unittest
from pathlib import Path

from vram_switcher.config import VramConfig
from vram_switcher.triggers import TriggerMatcher


def make_config() -> VramConfig:
    return VramConfig(
        {
            "enabled": True,
            "triggers": {
                "tts_keywords": ["语音", "开口说话"],
                "image_prefixes": ["aimg", "改图", "/改图"],
                "image_tool_names": ["generate_selfie", "edit_image"],
                "tts_reply_marker_pattern": r"[\[【]\s*语音\s*[\]】]",
            },
        },
        profile_path=Path("profile.local.json"),
        plugin_dir=Path("."),
        data_dir=Path("."),
    )


class TriggerTests(unittest.TestCase):
    def setUp(self):
        self.matcher = TriggerMatcher(make_config())

    def test_tts_keyword(self):
        self.assertEqual(self.matcher.match_text("给我来一段语音"), "tts:语音")

    def test_image_prefix(self):
        self.assertEqual(self.matcher.match_text("aimg 一只猫"), "comfyui:aimg")
        self.assertEqual(self.matcher.match_text("/改图 把背景换掉"), "comfyui:/改图")

    def test_no_match(self):
        self.assertIsNone(self.matcher.match_text("今天吃什么"))

    def test_tool_name(self):
        self.assertEqual(self.matcher.match_tool("generate_selfie"), "tool:generate_selfie")
        self.assertIsNone(self.matcher.match_tool("search"))

    def test_reply_marker(self):
        self.assertTrue(self.matcher.has_reply_marker("晚安啦[语音]"))
        self.assertTrue(self.matcher.has_reply_marker("【语音】"))
        self.assertFalse(self.matcher.has_reply_marker("普通回复"))


if __name__ == "__main__":
    unittest.main()
