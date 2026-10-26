# Changelog

## 2.0.3

- Reposition the project around local AI companions on AstrBot / DeepSeek
  Harness: persona chat (LLM) + IndexTTS voice + ComfyUI selfies on one GPU.
- Add `docs/indextts.md`: the busy/ready flag contract, a copy-paste adapter,
  AstrBot hook priorities, dual 2.0/2.5 versions and troubleshooting.
- Add `docs/comparison.md`: an honest comparison with llama-swap, berth and
  vhotseat, including when *not* to use this project.
- README/README.en: companion-focused intro, "who it is for", and a history
  section covering the August 2026 origin and the September production incident.
- npm package ships `docs/`; metadata and keywords now mention AstrBot, DSH,
  IndexTTS and AI companions.

## 2.0.2

- Add `configure.py`: detects a running llama-server / TTS webui / ComfyUI,
  falls back to scanning `--root` directories, and writes a valid
  `profile.local.json`. Nothing is started or stopped.
- Add `README.en.md` and a shorter, benefit-first top section to `README.md`.
- 7 new tests for `configure.py` (pure-JSON `--dry-run`, `--force` backup,
  refusal to overwrite, generated profile accepted by the real loader).
- npm package now ships `configure.py` and `README.en.md`, and contains no
  `__pycache__` files.

## 2.0.1

- Move the DSH bundle patch to the repository root (`cordis.patch.yml`), matching
  the documented DSH package layout.
- Correct prerelease ranges for `engines.dsh` and the `@deepseek-ai/cordis` peer.
- CI now validates `dsh-plugin.json` and the npm tarball contents.

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
