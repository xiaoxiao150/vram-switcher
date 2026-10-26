# VRAM Switcher

**Run a local LLM, a TTS webui and ComfyUI on one 12 GB GPU — without OOM, without 30-second diffusion steps, without juggling processes by hand.**

[中文](README.md) · [Releases](https://github.com/xiaoxiao150/vram-switcher/releases) · [AstrBot plugin](#astrbot) · [Native DSH plugin](#deepseek-harness-dsh)

```text
LLM resident, 4.9 GB / 12 GB used
        │
        ├── "/aimg a cat" ──► stop LLM ──► start ComfyUI ──► generate ──► drain queue
        │                                    (frees ~6 GB)                stop ComfyUI
        │                                                                 restart LLM
        └── TTS request ────► stop LLM ──► ensure TTS webui ──► synthesize ──► restart LLM
```

VRAM Switcher is a small coordinator that owns the *ordering* of those
start/stop transitions. It is not a model, not a router, and not a launcher you
have to rewrite: everything machine-specific lives in one JSON profile.

## Why

A 12 GB card holds roughly:

| Service | VRAM when resident |
|---|---|
| `llama-server` (Qwen3.6-35B-A3B MoE, 256K ctx) | ~4.9 GB |
| IndexTTS webui | ~2.3–5.3 GB |
| ComfyUI + SD/Flux | ~6–7 GB |
| Desktop + host app | ~2 GB |

Any two of the first three plus the desktop overflows. Windows then starts
paging VRAM to system RAM, which does not fail loudly — it just makes everything
100× slower. Measured on the machine this was written for: `s2mel` diffusion
went from **0.12 s/step to 30 s/step**, a single image took **543 s**, and prompt
prefill dropped from ~1300 tok/s to **0.2 tok/s**. The process was alive the
whole time.

The fix is not a bigger GPU. It is making sure only one of them is resident when
it matters.

## What it does

- **Stops what is not needed** before a task, **starts what is** — then puts it back.
- **Waits properly**: ComfyUI's 8188 must be *listening*, not merely spawned
  (a 1-second-early connection used to kill every selfie); a batch of images is
  drained via `/queue` before the service is stopped.
- **Only touches what you listed**: process name + cmdline filter + optional
  port allow-list. There is no "kill all python.exe" path.
- **Serializes across processes**: the AstrBot plugin and the DSH plugin share
  `coordinator.lock`, so a TTS event and a GPU task never swap services at the
  same instant.
- **Works for two hosts from one core**: AstrBot (Python plugin) and DeepSeek
  Harness (native Cordis plugin + a small Python daemon). An MCP stdio server is
  included for Claude Desktop / Codex and friends.

## Quick start

```bash
git clone https://github.com/xiaoxiao150/vram-switcher
cd vram-switcher

# Detect a running llama-server / TTS webui / ComfyUI and write a profile.
# Nothing is started or stopped by this step.
python configure.py --root D:/AI        # add --dry-run to just look
```

`configure.py` writes `profile.local.json` and prints what it found:

```text
[detected] llm: running process
[detected] tts: running process
[missing ] image: fill this in by hand
[port    ] 8080 (llm) listening
[port    ] 7860 (tts) listening
[port    ] 8188 (image) free
```

Point the host at that file and restart it. Services it could not detect are
written with `enabled: false` — the plugin stays valid and simply does less.

### AstrBot

```bash
git clone https://github.com/xiaoxiao150/vram-switcher \
  AstrBot/data/plugins/astrbot_plugin_vram_switcher
```

Or install from the plugin market: search **VRAM Switcher**
([cloud.astrbot.app](https://cloud.astrbot.app/)). Then set `profile_path` in the
plugin settings.

Compatibility hooks used by other AstrBot companion plugins
(`_prepare_switch`, `_recover_after_tts`, `on_decorating_result`,
`_vram_switcher_*` event extras, `tts_busy.flag` / `tts_ready.flag`) are
preserved, so existing setups keep working.

### DeepSeek Harness (DSH)

The repository is also a native Cordis plugin (`dsh-vram-switcher`): root
`cordis.patch.yml`, `dsh.bundle.patch` in `package.json`, manifest v0.15. Tools:

| Tool | Purpose |
|---|---|
| `vram_status` | current state of each service + active lease |
| `vram_acquire` | take the GPU for `image` or `tts` (may stop the LLM) |
| `vram_release` | give it back: drain, stop, restore the LLM |

Also listed in the [awesome-dsh-plugin](https://awesome-dsh-plugin.com) catalog.
See [`dsh/README.md`](dsh/README.md) for the profile override and the caveat:
**do not acquire while the same turn is using the local LLM** — it would stop
the model mid-answer.

## Configuration

One file, `profile.local.json` (git-ignored). Strings understand
`${PLUGIN_DIR}`, `${DATA_DIR}` and `${ENV_NAME}`.

```jsonc
{
  "services": {
    "llm": {
      "enabled": true,
      "port": 8080,
      "process_names": ["llama-server.exe"],
      "start": {
        "engine": "moe",
        "profiles": {
          "moe": { "args": ["D:/AI/llama/llama-server.exe", "-m", "D:/AI/models/m.gguf",
                            "-c", "262144", "--port", "8080"], "cwd": "D:/AI/llama" }
        }
      },
      "ready": { "type": "process", "timeout_seconds": 50 },
      "recovery_wait_seconds": 45,
      "night_quiet": { "enabled": true, "start": "23:00", "end": "07:00" }
    },
    "tts": { "enabled": true, "port": 7860, "cmdline_contains": ["webui.py"],
             "busy_flag": "${DATA_DIR}/tts_busy.flag",
             "ready_flag": "${DATA_DIR}/tts_ready.flag",
             "versions": { "2.5": { "args": ["python.exe", "webui.py", "--fp16"] } } },
    "image": { "enabled": true, "port": 8188, "cmdline_contains": ["ComfyUI"],
               "stop_by_port": true,
               "start": { "args": ["python.exe", "-u", "-s", "main.py"] },
               "ready": { "type": "port", "timeout_seconds": 300 },
               "queue": { "timeout_seconds": 420 } }
  },
  "aux_services": [{ "key": "optimizer", "process_names": ["pe-server.exe"] }]
}
```

- `aux_services` are stopped but never started — right for a prompt optimizer
  that re-launches itself on demand.
- `night_quiet` leaves the local LLM down overnight; set `enabled: false` if you
  want it back immediately.
- `stop_by_port` only when the port is exclusively that service.
- Full reference and every field: [Chinese README](README.md#profile-参考) /
  [`profile.example.json`](profile.example.json).

## Safety model

| Risk | Mitigation |
|---|---|
| Killing the wrong process | name **and** cmdline filters; optional port allow-list; never a bare name match |
| Killing itself | own PID and parent PID excluded |
| Shell injection | `start` takes an argv array, executed with `shell=False` |
| Two hosts fighting | `coordinator.lock` held across the whole acquire → release window |
| Bad config | profile validated on load; invalid profile disables the plugin with a logged reason instead of guessing |
| Leaked paths | `profile.local.json` is git-ignored; the shipped example has no real paths |

## Tests

```bash
python -m unittest discover -s tests -v     # 36 tests, no GPU, no real processes
```

CI runs on Python 3.10–3.12, validates the DSH manifest against the official
schema, and checks that the published npm tarball contains no caches or tests.

## Limits

- Windows first. Linux has basic `ps`/`kill` support but process names differ.
- No `nvidia-smi` polling: readiness comes from processes, ports and flag files,
  which is predictable and driver-independent.
- TTS needs the busy/ready flag protocol; other engines need a thin adapter.
- Provider switching is intentionally off by default — it fights AstrBot's
  fallback chain. Subclass `NullProviderHooks` if you want it.

## License

MIT.
