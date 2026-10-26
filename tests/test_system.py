import unittest

from vram_switcher.system import ProcessInfo, SystemAdapter


class FakeStopSystem(SystemAdapter):
    def __init__(self, pids):
        super().__init__()
        self.remaining = set(pids)
        self.killed = []
        self.port_pids = set()

    def find_pids(self, spec):
        return set(self.remaining)

    def pids_on_port(self, port, host=None):
        return set(self.port_pids)

    def _kill_pid(self, pid, *, tree=False, timeout=10.0):
        self.killed.append(pid)
        self.remaining.discard(pid)
        self.port_pids.discard(pid)
        return True


class ProcessMatchTests(unittest.TestCase):
    def test_name_match_is_exact_after_exe_normalization(self):
        spec = {"process_names": ["llama-server.exe"]}
        self.assertTrue(SystemAdapter.matches(
            ProcessInfo(1, "llama-server.exe", "llama-server.exe -m x"), spec
        ))
        self.assertFalse(SystemAdapter.matches(
            ProcessInfo(2, "not-llama-server.exe", "not-llama-server.exe"), spec
        ))

    def test_cmdline_contains_all_tokens(self):
        spec = {
            "process_names": ["python.exe"],
            "cmdline_contains": ["webui.py"],
            "cmdline_any": ["IndexTTS", "Index-TTS"],
        }
        self.assertTrue(SystemAdapter.matches(
            ProcessInfo(1, "python.exe", r"C:\AI\IndexTTS2.5\webui.py --fp16"), spec
        ))
        self.assertFalse(SystemAdapter.matches(
            ProcessInfo(2, "python.exe", r"C:\Other\webui.py"), spec
        ))

    def test_no_empty_match(self):
        self.assertFalse(SystemAdapter.matches(
            ProcessInfo(1, "python.exe", "python.exe"), {}
        ))

    def test_stop_service_kills_only_matched_pids(self):
        system = FakeStopSystem({10, 11})
        ok = system.stop_service("llm", {
            "process_names": ["llama-server.exe"],
            "stop": {"timeout_seconds": 1},
        })
        self.assertTrue(ok)
        self.assertEqual(sorted(system.killed), [10, 11])

    def test_stop_by_port_merges_pids(self):
        system = FakeStopSystem({10})
        system.port_pids = {20}
        ok = system.stop_service("image", {
            "process_names": ["python.exe"],
            "port": 8188,
            "stop_by_port": True,
            "stop": {"timeout_seconds": 1},
        })
        self.assertTrue(ok)
        self.assertEqual(sorted(system.killed), [10, 20])


if __name__ == "__main__":
    unittest.main()
