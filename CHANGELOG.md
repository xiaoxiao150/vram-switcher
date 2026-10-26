# Changelog

## 2.0.0

- Refactored the script into a configurable coordinator:
  - `vram_switcher/config.py`
  - `vram_switcher/system.py`
  - `vram_switcher/orchestrator.py`
  - `vram_switcher/triggers.py`
- Removed machine-specific paths, ports and model names from source code.
- Added `profile.local.json` (ignored) and `profile.example.json`.
- Added strict process targeting: process names + cmdline filters + optional port allow-list.
- Added TTS busy/ready flag handling, stale flag cleanup, ComfyUI queue draining and night quiet.
- Preserved compatibility hooks used by other AstrBot plugins:
  - `_prepare_switch`
  - `_recover_after_tts`
  - `on_decorating_result`
  - `_vram_switcher_*` event extras
- Added a native DSH Cordis plugin (`dsh-plugin/index.js` + `dsh-plugin.json`)
  with `vram_status`, `vram_acquire`, `vram_release`, backed by a Python daemon.
- Added an optional MCP fallback (`mcp_server.py`) for non-Cordis hosts.
- Added a cross-process `coordinator.lock` shared by AstrBot and DSH.
- Added unit tests, GitHub Actions CI, MIT license and README.
