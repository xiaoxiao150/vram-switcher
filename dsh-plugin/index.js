/**
 * Native DSH Cordis plugin for VRAM Switcher.
 *
 * This file is the DSH-facing surface: it registers native tools through
 * `ctx.tools.register`. The long-lived Python daemon beside it owns the actual
 * process/TTS/ComfyUI orchestration, so AstrBot, DSH and the MCP server all use
 * the same tested core.
 */

import { spawn } from 'node:child_process'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const PACKAGE_ROOT = resolve(HERE, '..')
const DAEMON = join(HERE, 'daemon.py')
const DEFAULT_TIMEOUT_MS = 15 * 60 * 1000

export const name = 'vram-switcher'
export const inject = ['tools']

function log(ctx, level, message) {
  const logger = ctx?.logger
  if (logger && typeof logger[level] === 'function') logger[level](message)
  else if (level === 'error') console.error(message)
  else if (level === 'warn') console.warn(message)
}

class DaemonClient {
  constructor({ ctx, profilePath, dataDir, pythonPath, leaseSeconds }) {
    this.ctx = ctx
    this.profilePath = profilePath
    this.dataDir = dataDir
    this.pythonPath = pythonPath
    this.leaseSeconds = leaseSeconds
    this.child = null
    this.starting = null
    this.nextId = 1
    this.pending = new Map()
    this.stdoutBuffer = ''
    this.stderrTail = ''
    this.stopped = false
  }

  async start() {
    if (this.child) return
    if (this.starting) return this.starting
    this.starting = new Promise((resolveStart, rejectStart) => {
      const args = [
        DAEMON,
        '--profile', this.profilePath,
        '--lease-seconds', String(this.leaseSeconds),
      ]
      if (this.dataDir) args.push('--data-dir', this.dataDir)
      let child
      try {
        child = spawn(this.pythonPath, args, {
          cwd: PACKAGE_ROOT,
          windowsHide: true,
          stdio: ['pipe', 'pipe', 'pipe'],
        })
      } catch (error) {
        rejectStart(error)
        return
      }
      this.child = child
      child.stdout.setEncoding('utf8')
      child.stderr.setEncoding('utf8')
      child.stdout.on('data', chunk => this._onStdout(chunk))
      child.stderr.on('data', chunk => {
        this.stderrTail = (this.stderrTail + chunk).slice(-4000)
      })
      child.once('spawn', () => {
        log(this.ctx, 'info', `vram-switcher daemon started (${this.pythonPath})`)
        resolveStart()
      })
      child.once('error', error => {
        this.child = null
        rejectStart(error)
      })
      child.once('close', code => {
        this.child = null
        this.starting = null
        const error = new Error(`vram-switcher daemon exited (code ${code}). ${this.stderrTail}`)
        for (const pending of this.pending.values()) pending.reject(error)
        this.pending.clear()
        if (!this.stopped) log(this.ctx, 'warn', error.message)
      })
    }).finally(() => {
      this.starting = null
    })
    return this.starting
  }

  _onStdout(chunk) {
    this.stdoutBuffer += chunk
    for (;;) {
      const newline = this.stdoutBuffer.indexOf('\n')
      if (newline < 0) break
      const line = this.stdoutBuffer.slice(0, newline).trim()
      this.stdoutBuffer = this.stdoutBuffer.slice(newline + 1)
      if (!line) continue
      let message
      try {
        message = JSON.parse(line)
      } catch {
        log(this.ctx, 'warn', `vram-switcher daemon emitted non-JSON: ${line.slice(0, 200)}`)
        continue
      }
      const pending = this.pending.get(message.id)
      if (!pending) continue
      this.pending.delete(message.id)
      if (message.ok) pending.resolve(message.data)
      else pending.reject(new Error(message.error || 'vram-switcher daemon error'))
    }
  }

  async request(op, args = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
    await this.start()
    if (!this.child) throw new Error('vram-switcher daemon is not running')
    const id = this.nextId++
    return new Promise((resolveRequest, rejectRequest) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        rejectRequest(new Error(`vram-switcher ${op} timed out after ${timeoutMs}ms`))
      }, timeoutMs)
      this.pending.set(id, {
        resolve: value => { clearTimeout(timer); resolveRequest(value) },
        reject: error => { clearTimeout(timer); rejectRequest(error) },
      })
      try {
        this.child.stdin.write(JSON.stringify({ id, op, ...args }) + '\n')
      } catch (error) {
        clearTimeout(timer)
        this.pending.delete(id)
        rejectRequest(error)
      }
    })
  }

  async stop() {
    this.stopped = true
    const child = this.child
    if (!child) return
    try {
      child.stdin.write(JSON.stringify({ id: this.nextId++, op: 'shutdown' }) + '\n')
    } catch {
      // ignore
    }
    await new Promise(resolveStop => {
      const timer = setTimeout(() => {
        try { child.kill() } catch { /* ignore */ }
        resolveStop()
      }, 2500)
      child.once('close', () => { clearTimeout(timer); resolveStop() })
    })
    this.child = null
  }
}

function textOutput(schema) {
  return {
    schema,
    render: (_args, value) => [{
      type: 'text',
      text: JSON.stringify(value ?? null, null, 2),
    }],
  }
}

const OUTPUT = textOutput({ type: 'object', additionalProperties: true })

function register(ctx, client, definition) {
  return ctx.tools.register({
    ...definition,
    output: OUTPUT,
  })
}

export function apply(ctx, settings = {}) {
  if (settings.enabled === false) {
    log(ctx, 'info', 'vram-switcher disabled by settings')
    return
  }
  const rawProfile = settings.profilePath
    || process.env.DSH_VRAM_PROFILE
    || join(PACKAGE_ROOT, 'profile.local.json')
  const profilePath = isAbsolute(rawProfile) ? rawProfile : resolve(PACKAGE_ROOT, rawProfile)
  const rawDataDir = settings.dataDir || process.env.DSH_VRAM_DATA_DIR || dirname(profilePath)
  const dataDir = isAbsolute(rawDataDir) ? rawDataDir : resolve(PACKAGE_ROOT, rawDataDir)
  const pythonPath = settings.pythonPath || process.env.DSH_VRAM_PYTHON || 'python'
  const leaseSeconds = Number(settings.leaseSeconds ?? 900)
  const client = new DaemonClient({ ctx, profilePath, dataDir, pythonPath, leaseSeconds })

  const disposers = [
    register(ctx, client, {
      name: 'vram_status',
      description:
        'Show the local GPU coordinator status for this machine. Use before deciding '
        + 'whether to acquire the GPU for a local ComfyUI/image or TTS task.',
      parameters: { type: 'object', properties: {}, additionalProperties: false },
      isConcurrencySafe: () => true,
      execute: () => client.request('status'),
    }),
    register(ctx, client, {
      name: 'vram_acquire',
      description:
        'Acquire the GPU coordinator lease before a local ComfyUI/image or TTS job. '
        + 'May stop the local LLM and start the requested service; cold ComfyUI can take '
        + 'minutes. The lease is held until vram_release or the timeout. Do not call while '
        + 'the current turn is using the same local LLM provider.',
      parameters: {
        type: 'object',
        properties: {
          mode: { type: 'string', enum: ['image', 'tts'] },
          reason: { type: 'string' },
          lease_seconds: { type: 'number' },
        },
        required: ['mode'],
        additionalProperties: false,
      },
      execute: args => client.request('acquire', {
        mode: String(args?.mode ?? ''),
        reason: String(args?.reason ?? ''),
        lease_seconds: args?.lease_seconds,
      }),
    }),
    register(ctx, client, {
      name: 'vram_release',
      description:
        'Release a vram_acquire lease, stop leftover image/TTS services as configured, '
        + 'and restore the local LLM. Call this after the GPU job, including on failure.',
      parameters: {
        type: 'object',
        properties: { reason: { type: 'string' } },
        additionalProperties: false,
      },
      execute: args => client.request('release', {
        reason: String(args?.reason ?? 'adapter_release'),
      }),
    }),
  ]

  const dispose = () => {
    for (const disposer of disposers) {
      try { disposer() } catch { /* ignore */ }
    }
    void client.stop()
  }
  if (typeof ctx.effect === 'function') ctx.effect(() => dispose, 'vram-switcher tools')
  else process.once('exit', dispose)
  log(ctx, 'info', `vram-switcher native plugin ready (profile: ${profilePath})`)
}
