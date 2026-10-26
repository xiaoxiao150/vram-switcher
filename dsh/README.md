# DSH native plugin

This repository is also a native DSH Cordis plugin package:

```text
dsh-vram-switcher (npm package)
  package.json                 dsh.bundle.patch -> dsh-plugin/cordis.patch.yml
  dsh-plugin.json              DSH manifest v0.15
  dsh-plugin/
    index.js                   Cordis host entry; ctx.tools.register(...)
    cordis.patch.yml           bundle patch (inserts ./index.js)
    daemon.py                  long-lived Python coordinator
  vram_switcher/               shared core (also used by AstrBot/MCP)
```

DSH loads the native plugin; the plugin starts a long-lived Python daemon. The
daemon owns the same `Orchestrator`, profile parser, process matching and
cross-process lock as the AstrBot plugin. No second state machine in JS.

## Tools

| Tool | Purpose |
|---|---|
| `vram_status` | Show LLM/TTS/image state, profile, lease and task status |
| `vram_acquire` | Acquire a GPU lease; `mode=image` or `mode=tts` |
| `vram_release` | Release the lease, stop disposable services, restore LLM |

`vram_acquire` may stop the local LLM and start ComfyUI/TTS; it can take minutes
on a cold ComfyUI start. The lease is held until `vram_release` or the timeout
(default 900s; pass `lease_seconds` for a longer job).

## Install from the DSH plugin market

The package is shaped for the official market flow:

- `package.json` has `dsh.bundle.patch`
- `dsh-plugin.json` is valid DSH manifest v0.15
- `keywords` include `dsh-plugin` / `deepseek-harness`
- `files` contains only the native plugin, Python core, README, profile example
  and license

Publishing requires your npm/GitHub account:

```bash
npm publish --access public
```

The DSH market is curated from [awesome-dsh-plugin](https://awesome-dsh-plugin.com).
After the npm package exists, submit its repository/package entry there; once
listed, DSH's market can install it like any other `dsh-plugin`.

## Local install (development)

Without publishing, install the package as a local dependency in the DSH profile:

```jsonc
// ~/.dsh/profiles/<profile>/package.json
{
  "dependencies": {
    "dsh-vram-switcher": "file:B:/path/to/astrbot_plugin_vram_switcher"
  },
  "dsh": {
    "profile": {
      "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless", "dsh-vram-switcher"]
    }
  }
}
```

Then:

```bash
pnpm install
```

The package's `dsh.bundle.patch` inserts `vram-switcher`. Add a profile override
to point it at the shared profile and Python:

```yaml
# ~/.dsh/profiles/<profile>/cordis.patch.yml
- id: vram-switcher
  config:
    profilePath: 'B:/.../plugin_data/astrbot_plugin_vram_switcher/profile.local.json'
    dataDir: 'B:/.../plugin_data/astrbot_plugin_vram_switcher'
    pythonPath: 'C:/.../dsh-runtimes/dsh-primary-runtime/dependencies/python/python.exe'
    leaseSeconds: 900
```

`pythonPath` may be any Python 3.10+ with `psutil`, or DSH's bundled Python
(which has no psutil; the core falls back to tasklist/PowerShell on Windows).

## Why a Python daemon?

- AstrBot, DSH and MCP all share one tested orchestration core.
- The daemon keeps the lease and file lock across multiple tool calls.
- The native plugin only owns Cordis tool registration, argument validation and
  RPC to the daemon.
- The daemon is launched once per DSH plugin lifetime and stopped on dispose.

## Shared lock

AstrBot and DSH must use the same `profile.local.json` and `dataDir`. The
`coordinator.lock` file serializes service transitions; the DSH native plugin
holds the lease for the whole acquire/release window.

Do not run an AstrBot GPU task and a DSH GPU task at the same instant. The lock
is a safety net, not a multi-tenant queue.

## Important: local LLM

If the current DSH turn is using the same local LLM provider that the profile's
`llm` service points to, do not call `vram_acquire`: it stops that model. Use a
remote provider for the coordination turn, or let AstrBot own the GPU task.

## MCP fallback

`mcp_server.py` remains available for hosts that do not install native Cordis
plugins (Claude Desktop, Codex, older DSH profiles). The MCP entry is optional;
see [`cordis.patch.mcp.example.yml`](cordis.patch.mcp.example.yml). Do not load
the MCP entry and the native plugin at the same time: both would manage the same
services and expose duplicate tools.
