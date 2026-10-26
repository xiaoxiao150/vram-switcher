import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AstrBotCompatContractTests(unittest.TestCase):
    """The AstrBot adapter's module-level helpers are imported by name by
    qq_voice_adapter's watchdog. The 2.0 refactor dropped them once; this
    test keeps that contract from disappearing silently again."""

    def setUp(self):
        self.source = (ROOT / "main.py").read_text(encoding="utf-8")

    def test_module_level_helpers_exist(self):
        for name in (
            "_is_indextts_running",
            "_kill_indextts_webui",
            "_start_indextts_webui",
            "_is_comfyui_running",
            "_is_comfyui_process_running",
            "_is_tts_busy",
        ):
            self.assertIn(f"def {name}(", self.source, name)

    def test_kill_is_guarded_by_busy_flag(self):
        self.assertIn("TTS 合成中，跳过停止 IndexTTS", self.source)
        self.assertIn("tts_busy()", self.source)

    def test_instance_is_registered_and_cleared(self):
        self.assertIn("_PLUGIN_INSTANCE = self", self.source)
        self.assertIn("_PLUGIN_INSTANCE = None", self.source)


if __name__ == "__main__":
    unittest.main()
