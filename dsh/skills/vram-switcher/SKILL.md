---
name: vram-switcher
description: Use when running a local GPU task on this machine (ComfyUI/image generation, IndexTTS or another TTS webui) while a local LLM is running. Coordinates the native vram-switcher DSH tools so services do not compete for VRAM.
---

# VRAM Switcher

This machine has a local GPU coordinator exposed by the native `vram-switcher` DSH plugin.

## When to use

Use this skill when a task will start a **local GPU service**, especially:

- ComfyUI or any image-generation workflow
- IndexTTS or another local TTS webui
- a shell command known to load a large model onto the GPU

Do **not** use it when the current turn is using the same local LLM provider
(for example `local-8080` or `local-auto`): `vram_acquire` stops that model.

## Workflow

1. Call `vram_status`.
2. If another lease is active, wait or ask the user; do not start a second GPU job.
3. Call `vram_acquire`:
   - image: `mode = "image"`
   - voice: `mode = "tts"`
   - long jobs: pass `lease_seconds` (up to 7200)
4. Run the local command/workflow.
5. Call `vram_release` in all outcomes, including failures.

## Rules

- Always release; a lease timeout is a safety net, not a substitute.
- Do not call `vram_acquire` just because you are thinking about an image.
  Only call it when you are about to start the local GPU work.
- Do not run AstrBot and DSH GPU jobs at the same instant. They share a lock
  file, but the coordinator is not a multi-tenant queue.
- If `vram_acquire` fails, do not start the GPU job anyway; report the error.
