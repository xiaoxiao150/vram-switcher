#!/usr/bin/env python3
"""Deploy the runtime subset of this dev repo into a live AstrBot plugin dir.

Edit here, never in the live plugin directory. The live directory is a
deployment target and deliberately has no .git.

Usage:
    python scripts/deploy.py --dry-run
    python scripts/deploy.py
    python scripts/deploy.py --target "D:/AstrBot/data/plugins/astrbot_plugin_vram_switcher"
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

DEV_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = (
    DEV_ROOT.parent
    / "AstrBotLauncher-0.2.0"
    / "AstrBot"
    / "data"
    / "plugins"
    / "astrbot_plugin_vram_switcher"
)

FILES = [
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "profile.example.json",
    "LICENSE",
    "README.md",
]
DIRS = ["vram_switcher"]


def copy_file(src: Path, dst: Path, dry_run: bool) -> None:
    print(f"  file  {src.relative_to(DEV_ROOT)} -> {dst}")
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def copy_dir(src: Path, dst: Path, dry_run: bool) -> None:
    for item in sorted(src.rglob("*.py")):
        if "__pycache__" in item.parts:
            continue
        rel = item.relative_to(src)
        copy_file(item, dst / rel, dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=str(DEFAULT_TARGET))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target = Path(args.target).expanduser().resolve()
    if not target.is_dir():
        print(f"target does not exist: {target}")
        return 2
    if (target / ".git").exists():
        print(
            "refusing: target still contains .git; remove/move it first so the "
            "live plugin can never be committed to GitHub by accident"
        )
        return 2

    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = DEV_ROOT.parent / "_deploy_backups" / stamp
    print(f"source: {DEV_ROOT}")
    print(f"target: {target}")
    print(f"backup: {backup}")
    if not args.dry_run:
        backup.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            current = target / name
            if current.is_file():
                shutil.copy2(current, backup / name)
        current_dir = target / "vram_switcher"
        if current_dir.is_dir():
            shutil.copytree(
                current_dir,
                backup / "vram_switcher",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

    for name in FILES:
        src = DEV_ROOT / name
        if not src.is_file():
            print(f"missing source: {src}")
            return 1
        copy_file(src, target / name, args.dry_run)
    for name in DIRS:
        src = DEV_ROOT / name
        if not src.is_dir():
            print(f"missing source: {src}")
            return 1
        copy_dir(src, target / name, args.dry_run)

    print("done. restart AstrBot (single PID, no /T tree kill) to load it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
