# VRAM Switcher

**Native GPU coordination for local AI companions on AstrBot / DeepSeek Harness — persona chat, IndexTTS voice and ComfyUI selfies taking turns on one 12 GB card.**

[中文](README.md) · [Releases](https://github.com/xiaoxiao150/vram-switcher/releases) · [IndexTTS integration](docs/indextts.md) (Chinese) · [vs. llama-swap / berth](docs/comparison.md) (Chinese) · [AstrBot plugin](#astrbot) · [Native DSH plugin](#deepseek-harness-dsh)

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

This was written for a local AI companion: she needs persona continuity (local
LLM), voice replies (IndexTTS), and selfies/edits (ComfyUI), often in the same
turn. A generic GPU broker does not know when a voice clip has actually finished
synthesizing; for a companion that boundary is the whole point.

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

## Who it is for

GPU arbitration is already crowded: [llama-swap](https://github.com/mostlygeek/llama-swap)
has 6.1k stars, [berth](https://github.com/T-Crypt/berth) reached 200 stars after
its October 11, 2026 debut, and vhotseat / OneGPU4All cover nearby cases. This
project does not pretend to be a general scheduler. It solves one specific
setup:

> **You run a local AI companion on AstrBot or DSH: persona chat (local LLM) +
> IndexTTS voice + ComfyUI selfies, all on one 12 GB card.**

| Your situation | Better fit |
|---|---|
| Only swapping llama.cpp / vLLM models, clients can change `base_url` | [llama-swap](https://github.com/mostlygeek/llama-swap) |
| General GPU leases, priorities, games/VMs/multi-GPU | [berth](https://github.com/T-Crypt/berth) (alpha, Windows experimental) |
| Linux LLM ↔ ComfyUI turn-taking | [vhotseat](https://github.com/wandermyz/vhotseat) |
| **AstrBot / DSH companion stack, no proxy, switch only when TTS really finishes** | **this project** |

What it adds over a generic broker: the five AstrBot event hooks, three native
DSH tools, the IndexTTS `tts_busy.flag` / `tts_ready.flag` protocol, real
process stop/start, and a cross-process lock. What it deliberately does not do:
multi-GPU, games/VMs, priority queues, a gateway, `nvidia-smi` polling, or
automatic provider switching. See the
[detailed comparison](docs/comparison.md) (Chinese).

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

### IndexTTS: why this stops the process

IndexTTS webui has no "unload model" endpoint. Resident it takes 2.3–5.3 GB; the
local LLM takes 4.9 GB; ComfyUI takes 6–7 GB. On a 12 GB card they cannot
coexist, so the TTS side has to give the card back before synthesis and take it
again afterwards.

The handoff uses two files:

1. The TTS plugin writes `tts_busy.flag` (and removes a stale `tts_ready.flag`)
   **before** submitting a job.
2. VRAM Switcher stops the LLM, brings the TTS webui online if needed, waits for
   the port to listen, then writes `tts_ready.flag`.
3. The TTS plugin waits for `ready` **and** an open port before submitting.
4. On success or failure the TTS plugin removes `busy` in `finally`; VRAM
   Switcher restores the LLM.

Do not write `ready` early: if synthesis starts while the LLM still holds VRAM,
Windows pages the `s2mel` weights and a step goes from **0.12 s to 30 s**.
IndexTTS cold start is about 60 s, so allow at least 150 s. A busy flag older
than 900 s is treated as a crash leftover.

The full adapter, profile, AstrBot hook priorities and troubleshooting are in
[`docs/indextts.md`](docs/indextts.md) (Chinese, with copy-paste code). The
repository provides the scheduler side; your TTS plugin must implement the flag
contract.

### DeepSeek Harness (DSH)

The repository is also a native Cordis plugin (`dsh-vram-switcher`): root
`cordis.patch.yml`, `dsh.bundle.patch` in `package.json`, manifest v0.15. Tools:

| Tool | Purpose |
|---|---|
| `vram_status` | current state of each service + active lease |
| `vram_acquire` | take the GPU for `image` or `tts` (may stop the LLM) |
| `vram_release` | give it back: drain, stop, restore the LLM |

Submitted to the [awesome-dsh-plugin](https://awesome-dsh-plugin.com) catalog
([PR #9555](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin/pull/9555));
once merged it also appears in the DSH plugin market.
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

## History

- **2026-08**: the first version was written inside a local AI companion
  project. She chatted through a local LLM, spoke through IndexTTS, and sent
  selfies through ComfyUI; the only way to switch was to kill processes by hand,
  which regularly killed the reply that was being generated.
- **2026-09-28**: a selfie request hit a ComfyUI cold start (the wait window was
  120 s at the time), the plugin assumed it was not ready, and the request fell
  through to a cloud node — a privacy incident. The readiness window became
  300 s, and start/stop became an explicit state machine.
- **2026-10**: rewritten as the profile-driven 2.0 core, with native AstrBot and
  DSH adapters, then open-sourced.
- **2026-10-26**: public repository. The code had already been running on one
  12 GB machine for more than two months.

Public date is not start date. This history is here because, for the "who was
first" question, the earliest production log is more convincing than the
repository creation date — and because the first requirement was never "schedule
a GPU"; it was "let her chat, speak and send a selfie on a single 12 GB card".

## Limits

- Windows first. Linux has basic `ps`/`kill` support but process names differ.
- No `nvidia-smi` polling: readiness comes from processes, ports and flag files,
  which is predictable and driver-independent.
- TTS needs the busy/ready flag protocol; other engines need a thin adapter.
- Provider switching is intentionally off by default — it fights AstrBot's
  fallback chain. Subclass `NullProviderHooks` if you want it.

## License

MIT.
