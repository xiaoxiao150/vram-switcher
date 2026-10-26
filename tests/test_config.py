import json
import tempfile
import unittest
from pathlib import Path

from vram_switcher.config import load_config


def minimal_profile() -> dict:
    return {
        "version": 1,
        "enabled": True,
        "services": {
            "llm": {
                "enabled": True,
                "port": 8080,
                "process_names": ["llama-server.exe"],
                "start": {"engine": "moe", "profiles": {"moe": {"args": ["llama-server.exe"]}}},
            },
            "tts": {
                "enabled": True,
                "port": 7860,
                "process_names": ["python.exe"],
                "cmdline_contains": ["webui.py"],
                "busy_flag": "${DATA_DIR}/tts_busy.flag",
                "ready_flag": "${DATA_DIR}/tts_ready.flag",
                "versions": {"2.5": {"args": ["python.exe", "webui.py"]}},
            },
            "image": {
                "enabled": True,
                "port": 8188,
                "process_names": ["python.exe"],
                "cmdline_contains": ["ComfyUI"],
                "start": {"args": ["python.exe", "main.py"]},
            },
        },
        "triggers": {"tts_keywords": ["语音"]},
    }


class ConfigTests(unittest.TestCase):
    def test_missing_profile_is_disabled_with_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config({}, plugin_dir=tmp, data_dir=tmp)
            self.assertFalse(cfg.enabled)
            self.assertTrue(cfg.errors)

    def test_profile_loads_and_resolves_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "profile.local.json").write_text(
                json.dumps(minimal_profile(), ensure_ascii=False), encoding="utf-8"
            )
            cfg = load_config(
                {"enabled": True, "profile_path": "profile.local.json"},
                plugin_dir=root,
                data_dir=root / "plugin_data",
            )
            self.assertTrue(cfg.enabled, cfg.errors)
            self.assertEqual(cfg.llm_engine, "moe")
            self.assertIn("plugin_data", cfg.service("tts")["busy_flag"])
            self.assertNotIn("${DATA_DIR}", cfg.service("tts")["busy_flag"])

    def test_plugin_overrides_engine_and_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "profile.local.json").write_text(
                json.dumps(minimal_profile(), ensure_ascii=False), encoding="utf-8"
            )
            cfg = load_config(
                {
                    "profile_path": "profile.local.json",
                    "local_llm_engine": "dense27b",
                    "keep_indextts_resident": False,
                    "index_tts_version": "2.0",
                    "extra_tts_keywords": ["开口说话"],
                },
                plugin_dir=root,
                data_dir=root,
            )
            self.assertTrue(cfg.enabled, cfg.errors)
            self.assertEqual(cfg.llm_engine, "dense27b")
            self.assertFalse(cfg.policy_value("keep_tts_resident"))
            self.assertEqual(cfg.tts_selector, "2.0")
            self.assertIn("开口说话", cfg.triggers["tts_keywords"])

    def test_checked_in_example_profile_is_valid(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_config(
                {"profile_path": "profile.example.json"},
                plugin_dir=root,
                data_dir=tmp,
            )
            self.assertTrue(cfg.enabled, cfg.errors)
            self.assertEqual(set(cfg.summary()["services"]), {"llm", "tts", "image"})

    def test_invalid_services_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = minimal_profile()
            profile["services"] = {}
            (root / "profile.local.json").write_text(json.dumps(profile), encoding="utf-8")
            cfg = load_config({"profile_path": "profile.local.json"}, plugin_dir=root, data_dir=root)
            self.assertFalse(cfg.enabled)
            self.assertTrue(any("services" in item for item in cfg.errors), cfg.errors)


if __name__ == "__main__":
    unittest.main()
