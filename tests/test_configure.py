import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import configure  # noqa: E402
from vram_switcher.config import load_config  # noqa: E402


class ConfigureHelpersTests(unittest.TestCase):
    def test_split_cmdline_keeps_windows_paths(self):
        parts = configure.split_cmdline(
            r'"C:\Program Files\AI\llama-server.exe" -m "D:\models\my model.gguf" --port 8080'
        )
        self.assertEqual(configure.unquote(parts[0]), r"C:\Program Files\AI\llama-server.exe")
        self.assertEqual(configure.unquote(parts[2]), r"D:\models\my model.gguf")
        self.assertEqual(configure.unquote(parts[3]), "--port")

    def test_parse_port_falls_back_on_garbage(self):
        self.assertEqual(configure.parse_port("9090", 8080), 9090)
        self.assertEqual(configure.parse_port("", 8080), 8080)
        self.assertEqual(configure.parse_port("abc", 8080), 8080)
        self.assertEqual(configure.parse_port("70000", 8080), 8080)

    def test_build_profile_is_accepted_by_the_loader(self):
        profile = configure.build_profile(
            {
                "args": ["D:/AI/llama-server.exe", "-m", "D:/AI/model.gguf", "--port", "8080"],
                "cwd": "D:/AI",
                "port": 8080,
            },
            {
                "args": ["python.exe", "webui.py", "--version", "2.5", "--fp16"],
                "cwd": "D:/TTS",
                "port": 7860,
                "version": "2.5",
            },
            {
                "args": ["python.exe", "-u", "-s", "D:/ComfyUI/main.py"],
                "cwd": "D:/ComfyUI",
                "port": 8188,
            },
            Path("D:/tmp/vram-test"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.local.json"
            path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
            cfg = load_config({"profile_path": str(path)}, plugin_dir=ROOT, data_dir=tmp)
            self.assertTrue(cfg.enabled, cfg.errors)
            self.assertEqual(cfg.summary()["services"], {"llm": True, "tts": True, "image": True})
            self.assertEqual(cfg.llm_engine, "detected")
            self.assertEqual(cfg.tts_selector, "2.5")

    def test_missing_services_are_disabled_not_invented(self):
        profile = configure.build_profile(None, None, None, Path("D:/tmp"))
        self.assertEqual(
            [name for name, svc in profile["services"].items() if svc.get("enabled")],
            [],
        )
        self.assertEqual(len(configure.validate(profile)), 3)

    def test_validate_flags_llm_without_model(self):
        profile = configure.build_profile(
            {"args": ["llama-server.exe", "--port", "8080"], "cwd": "D:/AI", "port": 8080},
            None,
            None,
            Path("D:/tmp"),
        )
        self.assertIn("llm: has no -m model argument", configure.validate(profile))


class ConfigureCliTests(unittest.TestCase):
    def run_configure(self, tmp, *args):
        # Point HOME at the empty temp dir so detection cannot pick up the
        # developer's own ComfyUI/IndexTTS installs and make tests flaky.
        env = dict(os.environ, USERPROFILE=tmp, HOME=tmp)
        return subprocess.run(
            [sys.executable, str(ROOT / "configure.py"), *args],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=tmp,
            env=env,
        )

    def test_dry_run_writes_nothing_and_prints_only_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "profile.local.json"
            result = self.run_configure(
                tmp, "--dry-run", "--no-process", "--root", tmp,
                "--out", str(out), "--data-dir", str(Path(tmp) / "data"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(out.exists())
            self.assertFalse((Path(tmp) / "data").exists())
            profile = json.loads(result.stdout)
            self.assertIn("services", profile)

    def test_refuses_to_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "profile.local.json"
            out.write_text("{}", encoding="utf-8")
            result = self.run_configure(
                tmp, "--no-process", "--root", tmp,
                "--out", str(out), "--data-dir", str(Path(tmp) / "data"),
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(out.read_text(encoding="utf-8"), "{}")

    def test_force_keeps_a_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "profile.local.json"
            out.write_text("{}", encoding="utf-8")
            result = self.run_configure(
                tmp, "--no-process", "--allow-empty", "--force", "--root", tmp,
                "--out", str(out), "--data-dir", str(Path(tmp) / "data"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            backups = list(Path(tmp).glob("profile.local.json.bak_*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "{}")
            self.assertIn("services", json.loads(out.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
