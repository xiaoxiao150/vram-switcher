"""Small, testable process/port helpers.

Only processes that match an explicitly configured service spec are touched.
There is no "kill every python.exe" path in this module.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # psutil is optional; the plugin declares it, but tests can inject a fake.
    import psutil  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    psutil = None


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    name: str
    cmdline: str


def _norm_name(value: str) -> str:
    text = str(value or "").strip().lower()
    return text[:-4] if text.endswith(".exe") else text


class SystemAdapter:
    """Process/port operations with a strict allow-list model."""

    def __init__(self, logger: Any = None) -> None:
        self.logger = logger
        self._last_start: dict[str, float] = {}
        self._is_windows = os.name == "nt"

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    def _log(self, level: str, message: str) -> None:
        logger = self.logger
        if logger is None:
            return
        method = getattr(logger, level, None)
        if callable(method):
            method(f"[VRAMSwitcher] {message}")

    # ------------------------------------------------------------------
    # Process listing
    # ------------------------------------------------------------------
    def list_processes(self) -> list[ProcessInfo]:
        if psutil is not None:
            try:
                result: list[ProcessInfo] = []
                for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                    info = proc.info or {}
                    cmdline = info.get("cmdline") or []
                    result.append(
                        ProcessInfo(
                            pid=int(info.get("pid") or 0),
                            name=str(info.get("name") or ""),
                            cmdline=" ".join(str(part) for part in cmdline),
                        )
                    )
                return result
            except Exception as exc:
                self._log("debug", f"psutil 列进程失败，退回系统命令: {exc}")
        return self._list_processes_fallback()

    def _list_processes_fallback(self) -> list[ProcessInfo]:
        try:
            if self._is_windows:
                command = (
                    "$OutputEncoding = [Console]::OutputEncoding = "
                    "[System.Text.Encoding]::UTF8; "
                    "Get-CimInstance Win32_Process | "
                    "Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Compress"
                )
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", command],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=20,
                )
                raw = (proc.stdout or "").strip()
                if not raw:
                    return []
                data = json.loads(raw)
                if isinstance(data, dict):
                    data = [data]
                result = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    result.append(ProcessInfo(
                        pid=int(item.get("ProcessId") or 0),
                        name=str(item.get("Name") or ""),
                        cmdline=str(item.get("CommandLine") or ""),
                    ))
                return result
            proc = subprocess.run(
                ["ps", "-eo", "pid=,comm=,args="],
                capture_output=True, text=True, timeout=10,
            )
            result = []
            for line in (proc.stdout or "").splitlines():
                parts = line.strip().split(None, 2)
                if len(parts) < 2:
                    continue
                result.append(ProcessInfo(
                    pid=int(parts[0]),
                    name=parts[1],
                    cmdline=parts[2] if len(parts) > 2 else parts[1],
                ))
            return result
        except Exception as exc:
            self._log("debug", f"系统进程列表不可用: {exc}")
            return []

    @staticmethod
    def matches(process: ProcessInfo, spec: dict[str, Any]) -> bool:
        names = [str(v) for v in (spec.get("process_names") or []) if str(v).strip()]
        if names and not any(_norm_name(process.name) == _norm_name(name) for name in names):
            return False
        cmdline = process.cmdline.lower()
        contains = [str(v).lower() for v in (spec.get("cmdline_contains") or []) if str(v)]
        if contains and not all(token in cmdline for token in contains):
            return False
        any_of = [str(v).lower() for v in (spec.get("cmdline_any") or []) if str(v)]
        if any_of and not any(token in cmdline for token in any_of):
            return False
        return bool(names or contains or any_of)

    def find_pids(self, spec: dict[str, Any]) -> set[int]:
        exclude = {os.getpid(), os.getppid()}
        return {
            process.pid
            for process in self.list_processes()
            if process.pid not in exclude and self.matches(process, spec)
        }

    # ------------------------------------------------------------------
    # Ports
    # ------------------------------------------------------------------
    @staticmethod
    def is_port_listening(port: int | None, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
        if not port:
            return False
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except OSError:
            return False

    def pids_on_port(self, port: int | None, host: str | None = None) -> set[int]:
        if not port:
            return set()
        if psutil is not None:
            try:
                result: set[int] = set()
                for conn in psutil.net_connections(kind="inet"):
                    laddr = getattr(conn, "laddr", None)
                    if (
                        laddr
                        and int(getattr(laddr, "port", 0) or 0) == int(port)
                        and conn.status == psutil.CONN_LISTEN
                        and conn.pid
                    ):
                        result.add(int(conn.pid))
                return result
            except Exception as exc:
                self._log("debug", f"psutil 查端口占用失败，退回系统命令: {exc}")
        try:
            if self._is_windows:
                command = (
                    "$OutputEncoding = [Console]::OutputEncoding = "
                    "[System.Text.Encoding]::UTF8; "
                    f"$c = Get-NetTCPConnection -LocalPort {int(port)} -State Listen "
                    "-ErrorAction SilentlyContinue; "
                    "if ($c) { $c.OwningProcess }"
                )
                proc = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", command],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=10,
                )
                return {
                    int(line.strip()) for line in (proc.stdout or "").splitlines()
                    if line.strip().isdigit()
                }
            proc = subprocess.run(
                ["lsof", "-ti", f":{int(port)}"],
                capture_output=True, text=True, timeout=10,
            )
            return {
                int(line.strip()) for line in (proc.stdout or "").splitlines()
                if line.strip().isdigit()
            }
        except Exception:
            return set()

    # ------------------------------------------------------------------
    # Stopping
    # ------------------------------------------------------------------
    def _kill_pid(self, pid: int, *, tree: bool = False, timeout: float = 10.0) -> bool:
        try:
            if self._is_windows:
                args = ["taskkill", "/PID", str(pid), "/F"]
                if tree:
                    args.append("/T")
                subprocess.run(args, capture_output=True, timeout=timeout)
            else:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    return True
            return True
        except Exception as exc:
            self._log("warning", f"停止进程 {pid} 失败: {exc}")
            return False

    def _specs_for_stop(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        specs = [spec]
        extras = spec.get("stop_extra")
        if isinstance(extras, list):
            specs.extend(item for item in extras if isinstance(item, dict))
        return specs

    def stop_service(self, key: str, spec: dict[str, Any]) -> bool:
        if not isinstance(spec, dict):
            return True
        stop = spec.get("stop") if isinstance(spec.get("stop"), dict) else {}
        if stop.get("enabled", True) is False:
            return True
        timeout = float(stop.get("timeout_seconds") or 15)
        tree = bool(stop.get("tree", False))

        specs = self._specs_for_stop(spec)
        pids: set[int] = set()
        for item in specs:
            pids |= self.find_pids(item)
        if spec.get("stop_by_port") and spec.get("port"):
            pids |= self.pids_on_port(int(spec["port"]), spec.get("host"))
        pids.discard(os.getpid())
        pids.discard(os.getppid())

        if not pids:
            if spec.get("stop_by_port") and spec.get("port") and self.is_port_listening(
                spec.get("port"), spec.get("host", "127.0.0.1")
            ):
                self._log("warning", f"{key}: 端口仍被占用但找不到可安全停止的 PID")
                return False
            self._log("debug", f"{key}: 没有匹配的进程需要停止")
            return True

        self._log("info", f"{key}: 停止进程 {sorted(pids)}")
        for pid in sorted(pids):
            self._kill_pid(pid, tree=tree, timeout=timeout)

        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            remaining: set[int] = set()
            for item in specs:
                remaining |= self.find_pids(item)
            if spec.get("stop_by_port") and spec.get("port"):
                remaining |= self.pids_on_port(int(spec["port"]), spec.get("host"))
            remaining.discard(os.getpid())
            remaining.discard(os.getppid())
            if not remaining:
                return True
            time.sleep(0.5)

        self._log("warning", f"{key}: 停止超时，仍有进程 {sorted(remaining)}")
        return False

    # ------------------------------------------------------------------
    # Starting
    # ------------------------------------------------------------------
    def _open_log(self, log_file: str | None, rotate_mb: float = 5.0):
        if not log_file:
            return subprocess.DEVNULL, False
        path = Path(log_file)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and rotate_mb > 0 and path.stat().st_size > rotate_mb * 1024 * 1024:
                path.replace(path.with_name(path.name + ".old"))
            return open(path, "ab", buffering=0), True
        except Exception as exc:
            self._log("warning", f"日志文件打不开，丢弃子进程输出: {log_file} ({exc})")
            return subprocess.DEVNULL, False

    def start_service(
        self,
        key: str,
        spec: dict[str, Any],
        profile: dict[str, Any],
    ) -> bool:
        args_value = profile.get("args") if isinstance(profile, dict) else None
        if isinstance(args_value, str):
            args = shlex.split(args_value, posix=False)
        elif isinstance(args_value, list):
            args = [str(item) for item in args_value]
        else:
            self._log("warning", f"{key}: 启动配置没有 args")
            return False
        if not args:
            return False

        cooldown = float(
            profile.get("start_cooldown_seconds")
            or spec.get("start_cooldown_seconds")
            or 0
        )
        now = time.monotonic()
        last = self._last_start.get(key, 0.0)
        if cooldown > 0 and last and now - last < cooldown:
            remaining = int(cooldown - (now - last))
            self._log("info", f"{key}: 启动冷却中（剩余 {remaining}s），跳过重复启动")
            return False
        self._last_start[key] = now

        env = os.environ.copy()
        extra_env = profile.get("env") if isinstance(profile.get("env"), dict) else {}
        env.update({str(k): str(v) for k, v in extra_env.items()})
        cwd = profile.get("cwd") or None
        log_file = profile.get("log_file")
        rotate_mb = float(profile.get("log_rotate_mb") or 5.0)
        log_handle, _ = self._open_log(log_file, rotate_mb)
        creationflags = 0
        if self._is_windows and not profile.get("show_window", False):
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            subprocess.Popen(
                args,
                cwd=str(cwd) if cwd else None,
                env=env,
                shell=False,
                creationflags=creationflags,
                stdout=log_handle,
                stderr=subprocess.STDOUT if log_handle is not subprocess.DEVNULL else None,
            )
        except Exception as exc:
            self._log("error", f"{key}: 启动失败: {exc}")
            return False
        finally:
            if log_handle is not subprocess.DEVNULL:
                try:
                    log_handle.close()
                except Exception:
                    pass

        self._log(
            "info",
            f"{key}: 已启动 {args[0]}（日志: {log_file or '未配置'}）",
        )
        return True

    # ------------------------------------------------------------------
    # Health and queues
    # ------------------------------------------------------------------
    def wait_ready(self, spec: dict[str, Any], timeout: float | None = None) -> bool:
        ready = spec.get("ready") if isinstance(spec.get("ready"), dict) else {}
        kind = str(ready.get("type") or ("port" if spec.get("port") else "process"))
        if timeout is None:
            timeout = float(ready.get("timeout_seconds") or spec.get("ready_timeout_seconds") or 60)
        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            if kind == "port":
                if self.is_port_listening(spec.get("port"), spec.get("host", "127.0.0.1")):
                    return True
            elif self.service_process_running(spec):
                return True
            time.sleep(0.5)
        return False

    def service_process_running(self, spec: dict[str, Any]) -> bool:
        return bool(self.find_pids(spec))

    def service_running(self, spec: dict[str, Any]) -> bool:
        if spec.get("port"):
            return self.is_port_listening(spec.get("port"), spec.get("host", "127.0.0.1"))
        return self.service_process_running(spec)

    def http_get_json(self, url: str, timeout: float = 5.0) -> Any:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            return None

    def drain_queue(self, spec: dict[str, Any], timeout: float | None = None) -> int:
        queue = spec.get("queue") if isinstance(spec.get("queue"), dict) else {}
        if spec.get("port") is None:
            return 0
        if timeout is None:
            timeout = float(queue.get("timeout_seconds") or 420)
        interval = float(queue.get("interval_seconds") or 6)
        url = str(
            queue.get("url")
            or f"http://{spec.get('host', '127.0.0.1')}:{int(spec['port'])}/queue"
        )
        deadline = time.monotonic() + max(1.0, timeout)
        remaining = 0
        while time.monotonic() < deadline:
            payload = self.http_get_json(url)
            if not isinstance(payload, dict):
                return 0
            remaining = len(payload.get("queue_running") or []) + len(
                payload.get("queue_pending") or []
            )
            if remaining <= 0:
                return 0
            self._log("info", f"image: 队列还有 {remaining} 个任务，等待收工")
            time.sleep(interval)
        self._log("warning", "image: 等待队列超时，按恢复流程继续")
        return remaining

    def detect_version(self, spec: dict[str, Any]) -> str | None:
        patterns = spec.get("detect") if isinstance(spec.get("detect"), dict) else {}
        if not patterns:
            return None
        processes = [
            process for process in self.list_processes()
            if self.matches(process, spec)
        ]
        for version, needles in patterns.items():
            if isinstance(needles, str):
                needles = [needles]
            if not isinstance(needles, list):
                continue
            for process in processes:
                cmdline = process.cmdline
                if any(str(needle) in cmdline for needle in needles):
                    return str(version)
        return None

    def status(self) -> dict[str, Any]:
        return {
            "python": "psutil" if psutil is not None else "fallback",
            "platform": "windows" if self._is_windows else os.name,
            "processes": len(self.list_processes()),
        }
