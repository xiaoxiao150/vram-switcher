import os
import tempfile
import time
import unittest
from pathlib import Path

from vram_switcher.config import VramConfig
from vram_switcher.orchestrator import Orchestrator


class FakeSystem:
    def __init__(self, state=None):
        self.state = {"llm": True, "tts": True, "image": False}
        if state:
            self.state.update(state)
        self.stopped = []
        self.started = []
        self.drained = []

    @staticmethod
    def _key(spec):
        port = spec.get("port")
        return {8080: "llm", 7860: "tts", 8188: "image"}.get(port)

    def service_running(self, spec):
        return self.state.get(self._key(spec), False)

    def service_process_running(self, spec):
        return self.state.get(self._key(spec), False)

    def stop_service(self, key, spec):
        self.stopped.append(key)
        if key in self.state:
            self.state[key] = False
        return True

    def start_service(self, key, spec, profile):
        self.started.append(key)
        if key in self.state:
            self.state[key] = True
        return True

    def wait_ready(self, spec, timeout=None):
        return True

    def drain_queue(self, spec, timeout=None):
        self.drained.append("image")
        return 0

    def detect_version(self, spec):
        return "2.5"


def make_config(data_dir: Path, *, keep_tts: bool = True, free_tts: bool = True) -> VramConfig:
    return VramConfig(
        {
            "enabled": True,
            "services": {
                "llm": {
                    "enabled": True,
                    "port": 8080,
                    "process_names": ["llama-server.exe"],
                    "start": {
                        "engine": "moe",
                        "profiles": {"moe": {"args": ["llama-server.exe"]}},
                    },
                    "recovery_wait_seconds": 0,
                },
                "tts": {
                    "enabled": True,
                    "port": 7860,
                    "process_names": ["python.exe"],
                    "cmdline_contains": ["webui.py"],
                    "busy_flag": str(data_dir / "tts_busy.flag"),
                    "ready_flag": str(data_dir / "tts_ready.flag"),
                    "version_selector": "2.5",
                    "default_version": "2.5",
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
            "aux_services": [
                {"key": "pe", "process_names": ["pe-server.exe"]},
            ],
            "policy": {
                "free_tts_before_image": free_tts,
                "keep_tts_resident": keep_tts,
                "tts_poll_seconds": 0.1,
            },
        },
        profile_path=Path("profile.local.json"),
        plugin_dir=data_dir,
        data_dir=data_dir,
    )


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.system = FakeSystem()
        self.config = make_config(self.data_dir)
        self.orch = Orchestrator(self.config, self.system)

    async def asyncTearDown(self):
        await self.orch.stop()
        self.tmp.cleanup()

    async def test_prepare_image_stops_tts_and_starts_image(self):
        ok = await self.orch.prepare("tool:generate_selfie")
        self.assertTrue(ok)
        self.assertFalse(self.system.state["llm"])
        self.assertFalse(self.system.state["tts"])
        self.assertTrue(self.system.state["image"])
        self.assertIn("llm", self.system.stopped)
        self.assertIn("tts", self.system.stopped)
        self.assertIn("pe", self.system.stopped)
        self.assertIn("image", self.system.started)
        self.assertTrue(self.orch.tts_stopped_for_image)

    async def test_prepare_tts_starts_tts_and_touches_ready_flag(self):
        self.system.state.update({"llm": True, "tts": False})
        ok = await self.orch.prepare("tts:语音")
        self.assertTrue(ok)
        self.assertFalse(self.system.state["llm"])
        self.assertTrue(self.system.state["tts"])
        self.assertTrue((self.data_dir / "tts_ready.flag").exists())

    async def test_recover_drains_image_and_restarts_llm(self):
        self.system.state.update({"llm": False, "tts": False, "image": True})
        await self.orch.recover("tool:generate_selfie")
        self.assertIn("image", self.system.drained)
        self.assertFalse(self.system.state["image"])
        self.assertTrue(self.system.state["tts"])
        self.assertTrue(self.system.state["llm"])
        self.assertFalse(self.orch.llm_deferred)

    async def test_night_quiet_does_not_start_llm(self):
        self.orch._night_quiet = lambda: True
        self.system.state.update({"llm": False, "tts": True, "image": True})
        await self.orch.recover("tool:generate_selfie")
        self.assertNotIn("llm", self.system.started)
        self.assertTrue(self.orch.llm_deferred)

    async def test_keep_tts_false_stops_tts(self):
        self.system = FakeSystem({"llm": False, "tts": True, "image": True})
        self.config = make_config(self.data_dir, keep_tts=False)
        self.orch = Orchestrator(self.config, self.system)
        await self.orch.recover("tool:generate_selfie")
        self.assertIn("tts", self.system.stopped)
        self.assertTrue(self.system.state["llm"])

    async def test_stale_busy_flag_is_cleaned(self):
        flag = self.data_dir / "tts_busy.flag"
        ready = self.data_dir / "tts_ready.flag"
        flag.write_text("1", encoding="ascii")
        ready.write_text("1", encoding="ascii")
        old = time.time() - 2000
        os.utime(flag, (old, old))
        self.assertFalse(self.orch.tts_busy())
        self.assertFalse(flag.exists())
        self.assertFalse(ready.exists())

    async def test_ensure_tts(self):
        self.system.state["tts"] = False
        await self.orch.ensure_tts()
        self.assertTrue(self.system.state["tts"])


if __name__ == "__main__":
    unittest.main()
