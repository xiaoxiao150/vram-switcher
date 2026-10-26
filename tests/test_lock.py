import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from vram_switcher.lock import FileLock


class FileLockTests(unittest.TestCase):
    def test_cross_process_lock(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            lock_path = tmp_path / "coordinator.lock"
            holder_script = tmp_path / "holder.py"
            holder_script.write_text(
                "import sys, time\n"
                f"sys.path.insert(0, {str(root)!r})\n"
                "from vram_switcher.lock import FileLock\n"
                f"lock = FileLock({str(lock_path)!r})\n"
                "print(lock.acquire(5), flush=True)\n"
                "time.sleep(5)\n",
                encoding="utf-8",
            )
            holder = subprocess.Popen(
                [sys.executable, str(holder_script)],
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(holder.stdout.readline().strip(), "True")
                holder.stdout.close()
                lock = FileLock(lock_path)
                self.assertFalse(lock.acquire(0.5))
                lock.release()
            finally:
                holder.terminate()
                holder.wait(timeout=5)
            lock = FileLock(lock_path)
            self.assertTrue(lock.acquire(2.0))
            lock.release()


if __name__ == "__main__":
    unittest.main()
