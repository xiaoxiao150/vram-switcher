#!/usr/bin/env python3
"""Generate profile.local.json by looking at what is already on this machine.

Nothing is started or stopped. The script only reads:

* running process command lines (via psutil, or the same fallback the plugin uses)
* listening ports
* a few well-known directories, plus anything passed with --root

Usage
-----
    python configure.py                    # detect, write profile.local.json
    python configure.py --dry-run          # print the profile, write nothing
    python configure.py --out D:/cfg/profile.local.json
    python configure.py --root D:/AI --root E:/ComfyUI
    python configure.py --force            # overwrite an existing profile (backed up)

Anything that could not be detected is written with ``enabled: false`` and
listed in the summary, so the generated file is always valid JSON and the
plugin never acts on a guess.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from vram_switcher.system import SystemAdapter
except Exception:  # pragma: no cover - setup must still run when the package is odd
    SystemAdapter = None  # type: ignore

DEFAULT_PORTS = {"llm": 8080, "tts": 7860, "image": 8188}
DEFAULT_DATA_SUBDIR = Path("data") / "plugin_data" / "astrbot_plugin_vram_switcher"

LLM_EXE_NAMES = {"llama-server.exe", "llama-kvmem-server.exe", "llama-server", "llama-kvmem-server"}
IMAGE_TOKENS = ("comfyui",)
TTS_TOKENS = ("indextts", "index-tts", "index_tts", "webui.py")


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------
def log(message: str) -> None:
    # Diagnostics go to stderr so `configure.py --dry-run > profile.json` works.
    print(message, file=sys.stderr, flush=True)


def split_cmdline(cmdline: str) -> list[str]:
    text = str(cmdline or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text, posix=False)
    except ValueError:
        return text.split()


def unquote(token: str) -> str:
    return str(token or "").strip().strip('"').strip("'").rstrip(",")


def existing(path: str | Path | None) -> Path | None:
    if not path:
        return None
    try:
        value = Path(unquote(str(path))).expanduser()
    except (OSError, ValueError):
        return None
    try:
        if value.is_file():
            return value.resolve()
        if value.is_dir():
            return value.resolve()
    except OSError:
        return None
    return None


def port_listening(port: int) -> bool:
    if SystemAdapter is not None:
        try:
            return SystemAdapter.is_port_listening(int(port))
        except Exception:
            pass
    import socket

    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.6):
            return True
    except OSError:
        return False


def arg_value(args: list[str], *names: str) -> str:
    for index, token in enumerate(args):
        clean = unquote(token)
        if clean in names and index + 1 < len(args):
            return unquote(args[index + 1])
    return ""


def parse_port(value: str, default: int) -> int:
    text = str(value or "").strip()
    try:
        port = int(text)
    except ValueError:
        return int(default)
    return port if 0 < port < 65536 else int(default)


def list_processes() -> list[Any]:
    if SystemAdapter is None:
        return []
    try:
        return SystemAdapter().list_processes()
    except Exception:
        return []


# ----------------------------------------------------------------------
# Detection: running processes first (most accurate), then the filesystem
# ----------------------------------------------------------------------
def detect_llm_from_process() -> dict[str, Any] | None:
    for process in list_processes():
        name = str(getattr(process, "name", "")).lower()
        if Path(name).name not in LLM_EXE_NAMES:
            continue
        args = split_cmdline(str(getattr(process, "cmdline", "")))
        if not args:
            continue
        exe = unquote(args[0])
        model = arg_value(args, "-m", "--model")
        port = arg_value(args, "--port")
        result: dict[str, Any] = {
            "source": "running process",
            "args": [unquote(token) for token in args],
            "cwd": str(Path(exe).parent) if existing(exe) else str(ROOT),
            "port": parse_port(port, DEFAULT_PORTS["llm"]),
            "exe": exe,
            "model": model,
        }
        return result
    return None


def detect_llm_from_filesystem(roots: list[Path]) -> dict[str, Any] | None:
    for root in roots:
        if not root or not root.exists():
            continue
        for depth in (1, 2, 3, 4):
            pattern = "/".join(["*"] * depth)
            for name in ("llama-server.exe", "llama-kvmem-server.exe"):
                try:
                    matches = list(root.glob(f"{pattern}/{name}"))
                except OSError:
                    matches = []
                for exe in matches:
                    if not exe.is_file():
                        continue
                    model = pick_model(exe)
                    args = [
                        str(exe),
                        "-ngl", "99",
                        "-c", "32768",
                        "--host", "127.0.0.1",
                        "--port", str(DEFAULT_PORTS["llm"]),
                    ]
                    if model:
                        args[1:1] = ["-m", str(model)]
                    return {
                        "source": f"filesystem ({exe})",
                        "args": args,
                        "cwd": str(exe.parent),
                        "port": DEFAULT_PORTS["llm"],
                        "exe": str(exe),
                        "model": str(model) if model else "",
                    }
    return None


def pick_model(exe: Path) -> Path | None:
    candidates: list[Path] = []
    for base in (exe.parent, exe.parent.parent, exe.parent.parent.parent):
        for folder in ("models", "model"):
            directory = base / folder
            try:
                candidates.extend(
                    item for item in directory.glob("*.gguf") if item.is_file()
                )
            except OSError:
                continue
    if not candidates:
        return None
    try:
        return max(candidates, key=lambda item: item.stat().st_size)
    except OSError:
        return candidates[0]


def detect_image() -> dict[str, Any] | None:
    for process in list_processes():
        cmdline = str(getattr(process, "cmdline", ""))
        lowered = cmdline.lower()
        if "python" not in str(getattr(process, "name", "")).lower():
            continue
        if not all(token in lowered for token in ("comfyui", "main.py")):
            continue
        args = [unquote(token) for token in split_cmdline(cmdline)]
        main_py = next(
            (token for token in args if token.lower().endswith("main.py") and "comfyui" in token.lower()),
            "",
        )
        python = next((token for token in args if token.lower().endswith("python.exe")), args[0])
        if not main_py:
            continue
        return {
            "source": "running process",
            "args": args,
            "cwd": str(Path(main_py).parent),
            "port": parse_port(arg_value(split_cmdline(cmdline), "--port"), DEFAULT_PORTS["image"]),
        }
    return None


def detect_image_from_filesystem(roots: list[Path]) -> dict[str, Any] | None:
    for root in roots:
        if not root or not root.exists():
            continue
        for depth in (1, 2, 3, 4):
            pattern = "/".join(["*"] * depth)
            try:
                matches = list(root.glob(f"{pattern}/ComfyUI/main.py"))
            except OSError:
                matches = []
            for main_py in matches:
                if not main_py.is_file():
                    continue
                comfy_root = main_py.parent.parent
                python = first_existing(
                    comfy_root / "python" / "python.exe",
                    comfy_root / "python_embeded" / "python.exe",
                    comfy_root.parent / "python" / "python.exe",
                )
                if python is None:
                    python = Path(shutil.which("python") or "python")
                return {
                    "source": f"filesystem ({main_py})",
                    "args": [
                        str(python), "-u", "-s", str(main_py),
                        "--windows-standalone-build",
                    ],
                    "cwd": str(main_py.parent),
                    "port": DEFAULT_PORTS["image"],
                }
    return None


def detect_tts() -> dict[str, Any] | None:
    for process in list_processes():
        cmdline = str(getattr(process, "cmdline", ""))
        lowered = cmdline.lower()
        if "python" not in str(getattr(process, "name", "")).lower():
            continue
        if "webui.py" not in lowered:
            continue
        if not any(token in lowered for token in ("indextts", "index-tts", "index_tts")):
            continue
        args = [unquote(token) for token in split_cmdline(cmdline)]
        webui = next((token for token in args if token.lower().endswith("webui.py")), "")
        if not webui:
            continue
        return {
            "source": "running process",
            "args": args,
            "cwd": str(Path(webui).parent),
            "port": DEFAULT_PORTS["tts"],
            "version": "2.0" if "index-tts" in str(webui).lower() else "2.5",
        }
    return None


def detect_tts_from_filesystem(roots: list[Path]) -> dict[str, Any] | None:
    for root in roots:
        if not root or not root.exists():
            continue
        for depth in (1, 2, 3, 4):
            pattern = "/".join(["*"] * depth)
            try:
                matches = list(root.glob(f"{pattern}/webui.py"))
            except OSError:
                matches = []
            for webui in matches:
                text = str(webui).lower()
                if "indextts" not in text and "index-tts" not in text:
                    continue
                tts_root = webui.parent
                python = first_existing(
                    tts_root / ".venv" / "Scripts" / "python.exe",
                    tts_root / "venv" / "Scripts" / "python.exe",
                    tts_root / ".venv" / "bin" / "python",
                )
                if python is None:
                    python = Path(shutil.which("python") or "python")
                version = "2.0" if "index-tts" in text else "2.5"
                args = [str(python), str(webui), "--fp16"]
                if version == "2.5":
                    args = [str(python), str(webui), "--version", "2.5", "--fp16"]
                return {
                    "source": f"filesystem ({webui})",
                    "args": args,
                    "cwd": str(tts_root),
                    "port": DEFAULT_PORTS["tts"],
                    "version": version,
                }
    return None


def first_existing(*paths: Path) -> Path | None:
    for path in paths:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


# ----------------------------------------------------------------------
# Profile assembly
# ----------------------------------------------------------------------
def build_profile(llm: dict | None, tts: dict | None, image: dict | None, data_dir: Path) -> dict[str, Any]:
    unique = str(time.time()).replace(".", "")
    services: dict[str, Any] = {}

    if llm:
        services["llm"] = {
            "enabled": True,
            "label": "Local LLM (llama.cpp)",
            "port": int(llm.get("port") or DEFAULT_PORTS["llm"]),
            "host": "127.0.0.1",
            "process_names": ["llama-server.exe", "llama-kvmem-server.exe"],
            "stop": {"timeout_seconds": 15, "tree": True},
            "start": {
                "engine": "detected",
                "profiles": {
                    "detected": {
                        "args": list(llm.get("args") or []),
                        "cwd": str(llm.get("cwd") or ROOT),
                        "log_file": str(data_dir / "llm.log"),
                        "env": {"VRAM_SETUP_GENERATED": unique},
                    }
                },
            },
            "ready": {"type": "process", "timeout_seconds": 50},
            "recovery_wait_seconds": 45,
            "night_quiet": {"enabled": True, "start": "23:00", "end": "07:00"},
        }
    else:
        services["llm"] = {"enabled": False, "label": "not detected — fill in start.args"}

    if tts:
        version = str(tts.get("version") or "2.5")
        services["tts"] = {
            "enabled": True,
            "label": "TTS webui",
            "port": int(tts.get("port") or DEFAULT_PORTS["tts"]),
            "host": "127.0.0.1",
            "process_names": ["python.exe"],
            "cmdline_contains": ["webui.py"],
            "cmdline_any": ["IndexTTS", "Index-TTS"],
            "stop": {"timeout_seconds": 20, "tree": False},
            "busy_flag": str(data_dir / "tts_busy.flag"),
            "ready_flag": str(data_dir / "tts_ready.flag"),
            "flag_stale_seconds": 900,
            "start_cooldown_seconds": 150,
            "version_selector": version,
            "default_version": version,
            "versions": {
                version: {
                    "args": list(tts.get("args") or []),
                    "cwd": str(tts.get("cwd") or ROOT),
                    "env": {"PYTHONUNBUFFERED": "1"},
                    "log_file": str(data_dir / "tts.log"),
                }
            },
        }
    else:
        services["tts"] = {"enabled": False, "label": "not detected — fill in versions.<v>.args"}

    if image:
        services["image"] = {
            "enabled": True,
            "label": "ComfyUI",
            "port": int(image.get("port") or DEFAULT_PORTS["image"]),
            "host": "127.0.0.1",
            "process_names": ["python.exe"],
            "cmdline_contains": ["ComfyUI"],
            "stop_by_port": True,
            "stop": {"timeout_seconds": 20, "tree": False},
            "start": {
                "args": list(image.get("args") or []),
                "cwd": str(image.get("cwd") or ROOT),
                "log_file": str(data_dir / "comfyui_console.log"),
            },
            "ready": {"type": "port", "timeout_seconds": 300},
            "queue": {"timeout_seconds": 420, "interval_seconds": 6},
        }
    else:
        services["image"] = {"enabled": False, "label": "not detected — fill in start.args"}

    return {
        "version": 1,
        "enabled": True,
        "lock_file": str(data_dir / "coordinator.lock"),
        "lock_timeout_seconds": 900,
        "services": services,
        "aux_services": [],
        "triggers": {
            "tts_keywords": ["语音"],
            "image_prefixes": ["aimg", "img2img", "改图", "/改图"],
            "image_tool_names": [
                "comfyui_txt2img", "generate_selfie", "generate_image", "edit_image",
            ],
            "tts_reply_marker_pattern": "[\\[【]\\s*语音\\s*[\\]】]",
        },
        "policy": {
            "free_tts_before_image": True,
            "keep_tts_resident": True,
            "tts_poll_seconds": 2,
        },
    }


def validate(profile: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for name in ("llm", "tts", "image"):
        service = (profile.get("services") or {}).get(name) or {}
        if not service.get("enabled"):
            problems.append(f"{name}: not detected")
            continue
        if name == "llm":
            args = service.get("start", {}).get("profiles", {}).get("detected", {}).get("args") or []
            if "-m" not in args:
                problems.append("llm: has no -m model argument")
        if name == "tts":
            versions = service.get("versions") or {}
            if not versions:
                problems.append("tts: has no versions")
        if name == "image":
            if not (service.get("start") or {}).get("args"):
                problems.append("image: has no start.args")
    return problems


def default_data_dir() -> Path:
    home = Path.home()
    return home / ".vram-switcher"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Detect local LLM/TTS/ComfyUI installs and write profile.local.json",
    )
    parser.add_argument("--out", default="profile.local.json")
    parser.add_argument("--data-dir", default=None, help="Where flag/lock/log files live")
    parser.add_argument("--root", action="append", default=[], help="Extra directory to scan (repeatable)")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing profile (keeps a .bak)")
    parser.add_argument("--dry-run", action="store_true", help="Print the profile instead of writing it")
    parser.add_argument("--no-process", action="store_true", help="Skip running-process detection")
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Exit 0 even when nothing was detected (the file is still written)",
    )
    args = parser.parse_args(argv)

    roots: list[Path] = []
    for item in [Path.cwd(), *[Path(value) for value in args.root], Path.home()]:
        try:
            roots.append(item.expanduser().resolve())
        except OSError:
            continue
    # Keep the list short: deep scans of a whole drive are slow and surprise people.
    unique_roots: list[Path] = []
    for root in roots:
        if root not in unique_roots and root.is_dir():
            unique_roots.append(root)

    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else default_data_dir()

    llm = None if args.no_process else detect_llm_from_process()
    if llm is None:
        llm = detect_llm_from_filesystem(unique_roots)
    tts = None if args.no_process else detect_tts()
    if tts is None:
        tts = detect_tts_from_filesystem(unique_roots)
    image = None if args.no_process else detect_image()
    if image is None:
        image = detect_image_from_filesystem(unique_roots)

    for name, found in (("llm", llm), ("tts", tts), ("image", image)):
        if found:
            log(f"[detected] {name}: {found.get('source')}")
        else:
            log(f"[missing ] {name}: fill this in by hand")

    for name, port in DEFAULT_PORTS.items():
        state = "listening" if port_listening(port) else "free"
        log(f"[port    ] {port} ({name}) {state}")

    profile = build_profile(llm, tts, image, data_dir)
    problems = validate(profile)
    text = json.dumps(profile, ensure_ascii=False, indent=2) + "\n"

    out_path = Path(args.out).expanduser()
    if not out_path.is_absolute():
        out_path = (Path.cwd() / out_path).resolve()

    if args.dry_run:
        sys.stdout.write(text)
        return 0

    data_dir.mkdir(parents=True, exist_ok=True)

    if out_path.exists() and not args.force:
        log(f"[refuse  ] {out_path} exists; pass --force to replace it (a .bak is kept)")
        return 2
    if out_path.exists():
        backup = out_path.with_name(out_path.name + f".bak_{time.strftime('%Y%m%d_%H%M%S')}")
        try:
            out_path.replace(backup)
            log(f"[backup  ] {backup}")
        except OSError as exc:
            log(f"[warn    ] could not back up: {exc}")
    out_path.write_text(text, encoding="utf-8")
    log(f"[written ] {out_path}")

    if problems:
        log("")
        log("Needs attention:")
        for item in problems:
            log(f"  - {item}")
        log("")
        log("Edit the file, then point the plugin's profile_path at it.")
        return 0 if args.allow_empty else 1

    log("")
    log("All three services were detected. Point the plugin's profile_path at:")
    log(f"  {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
