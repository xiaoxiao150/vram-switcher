# VRAM Switcher

**[English](README.en.md)** · [Releases](https://github.com/xiaoxiao150/vram-switcher/releases) · [IndexTTS 集成](docs/indextts.md) · [GPU 兼容性](docs/gpu-support.md) · [和 llama-swap / berth 的区别](docs/comparison.md)

**为本地 AI 情感陪伴（AstrBot / DeepSeek Harness）原生接入的 GPU 调度器 —— 让人设对话、IndexTTS 语音和自拍/改图在同一张 12 GB 卡上轮流用卡。**

它不是通用 GPU 租约系统：不换客户端、不加代理、不读 `nvidia-smi`，只在宿主的事件边界上停/起你明确列出的进程。

在 12 GB 卡上，它把本地 LLM、IndexTTS 和 ComfyUI 从「互相换页」变成「轮流独占」——实测 `s2mel` 从 **30 s/步回到 0.12 s/步**，预填从 **0.2 tok/s 回到 ~1300 tok/s**。

```text
LLM 常驻，显存 4.9 GB / 12 GB
        │
        ├── "/aimg 一只猫" ──► 停 LLM ──► 起 ComfyUI ──► 出图 ──► 等队列跑空
        │                              (腾出 ~6 GB)             停 ComfyUI
        │                                                        拉起 LLM
        └── 语音请求 ──────► 停 LLM ──► 确保 TTS 在线 ──► 合成 ──► 拉起 LLM
```

它只负责这些「停/起」的**顺序**。不是模型、不是路由、也不是要你重写的启动器：所有机器相关的参数都在一个 JSON profile 里。

## 为什么需要它

12 GB 卡上通常是这样：

| 服务 | 常驻显存 |
|---|---|
| `llama-server`（Qwen3.6-35B-A3B MoE，256K 上下文） | ~4.9 GB |
| IndexTTS webui | ~2.3–5.3 GB |
| ComfyUI + SD/Flux | ~6–7 GB |
| 桌面 + 宿主程序 | ~2 GB |

前三个里任意两个加桌面就会溢出。Windows 不会当场报错，而是开始把显存换页到内存——**这不会崩，只会慢 100 倍**。在写下它的那台机器上实测：`s2mel` 扩散从 **0.12 s/步 变成 30 s/步**，一张图跑了 **543 秒**，预填从 ~1300 tok/s 掉到 **0.2 tok/s**。进程一直是活的。

这套东西最初就是给一个本地 AI 陪伴机器人写的：她要有人设连续性（本地 LLM）、要能发语音（IndexTTS）、要能自拍和改图（ComfyUI），而且这些经常发生在同一个回合里。通用 GPU 调度器不知道一段语音什么时候真正合成完；对陪伴场景，那个边界恰恰是最重要的。

解法不是换更大的卡，而是确保关键时刻只有一个在占显存。

## 它做什么

- 任务前**停掉不需要的**、**拉起需要的**，结束后再放回去。
- **真的等就绪**：ComfyUI 必须 8188 在 listen，而不是进程刚起（早 1 秒连接曾让每次自拍都失败）；一批图会先等 `/queue` 跑空再停服务。
- **只动你列出的东西**：进程名 + 命令行过滤 + 可选端口白名单，没有“杀掉所有 python.exe”这条路。
- **跨进程串行**：AstrBot 插件和 DSH 插件共用 `coordinator.lock`，语音事件和生图任务不会同时切服务。
- **一套核心，两个宿主**：AstrBot（Python 插件）与 DeepSeek Harness（原生 Cordis 插件 + 小 Python daemon）。另带 MCP stdio 服务，可给 Claude Desktop / Codex 用。
- **可复现的实测记录**：[docs/demo.md](docs/demo.md) 有语音和生图两条交接时序，以及换页前后的数字。

## 适合谁

显存协调已经不缺项目：[llama-swap](https://github.com/mostlygeek/llama-swap) 6.1k★，[berth](https://github.com/T-Crypt/berth) 2026-10-11 才公开、已 200★，vhotseat、OneGPU4All 也在做类似的事。这个项目不假装是通用调度器，它只解决一个具体问题：

> **你在 AstrBot 或 DSH 上跑一个本地 AI 陪伴：人设对话（本地 LLM）+ IndexTTS 语音 + 自拍/改图（ComfyUI），全在同一张 12 GB 卡上。**

一次回合里可能同时发生：

```text
用户消息
  ├─ 她回复文字（LLM 占卡）
  ├─ 回复带 [语音] → 停 LLM → IndexTTS 合成 → 恢复 LLM
  └─ 调 generate_selfie → 停 LLM/TTS → ComfyUI 出图 → 恢复 LLM
```

| 你的情况 | 更合适的选择 |
|---|---|
| 只是 llama.cpp / vLLM 换模型，所有客户端都能改 base URL | [llama-swap](https://github.com/mostlygeek/llama-swap) |
| 要通用 GPU 租约、优先级、游戏/VM/多卡仲裁 | [berth](https://github.com/T-Crypt/berth)（alpha，Windows 实验性） |
| Linux 上 LLM ↔ ComfyUI 轮流 | [vhotseat](https://github.com/wandermyz/vhotseat) |
| **在 AstrBot / DSH 上跑本地 AI 陪伴，不想改端口、不加代理，语音合成结束才切** | **本项目** |

相对通用调度器，它**多做**这些：

- AstrBot 的 5 个事件钩子：LLM 请求、回复装饰前后、工具调用前、工具返回后
- DSH 原生 Cordis 工具：`vram_status` / `vram_acquire` / `vram_release`
- IndexTTS 的 `tts_busy.flag` / `tts_ready.flag` 协议（合成边界，不是 HTTP 边界）
- 真实进程停启：TTS 没有 unload API 也能让出显存

也**少做**这些：多卡、游戏/VM、优先级队列、网关、`nvidia-smi` 轮询、provider 自动切换。需要这些就选上面的通用项目。

不跑 AstrBot / DSH 也可以：核心包 `vram_switcher` 不依赖宿主，`VramService` 提供 `start / acquire / release / status`，可以直接在你的 AI 陪伴服务里用：

```python
from vram_switcher import load_config
from vram_switcher.service import VramService

cfg = load_config({"profile_path": "profile.local.json"}, plugin_dir=".", data_dir="./data")
service = VramService(cfg)
await service.start()
await service.acquire("image", reason="selfie")   # 停 LLM、起 ComfyUI
# ... 出图 ...
await service.release("selfie")                   # 停 ComfyUI、恢复 LLM
```

语音侧仍建议走 `tts_busy.flag` / `tts_ready.flag`，因为只有 TTS 插件知道合成什么时候真正结束。

## GPU 兼容性（NVIDIA / AMD）

调度器本身不挑卡：没有 CUDA / ROCm / `nvidia-smi` 依赖，只看进程、命令行、端口和 flag。挑卡的是它调度的三个服务。

| 组件 | N 卡 Windows | A 卡 Windows | A 卡 Linux |
|---|---|---|---|
| 调度器 | ✅ 生产验证 | ⚠️ 机制可用，未实测 | ⚠️ 基础支持 |
| llama.cpp | ✅ CUDA | ⚠️ Vulkan（社区常用） | ⚠️ ROCm / Vulkan |
| ComfyUI | ✅ CUDA | ⚠️ DirectML / ZLUDA | ⚠️ ROCm |
| IndexTTS | ✅ CUDA | ⚠️ 最易卡住 | ⚠️ ROCm 需实测 |

一句话：**N 卡是已验证路径；A 卡的调度层可用，但 IndexTTS 在 Windows A 卡上最容易卡住**（PyTorch 的 ROCm 轮子覆盖有限）。llama.cpp 用 Vulkan 基本没问题；ComfyUI 走 DirectML/ZLUDA 能跑，但有节点兼容性和性能代价。

A 卡 profile 的关键调整：`ready.type` 用 `port`、超时给足、`recovery_wait_seconds` 加大、`process_names` / `cmdline_any` 按实际构建改。完整矩阵、逐组件说明和检查清单见 [`docs/gpu-support.md`](docs/gpu-support.md)。

## 快速开始

```bash
git clone https://github.com/xiaoxiao150/vram-switcher
cd vram-switcher

# 自动探测正在运行的 llama-server / TTS webui / ComfyUI，生成 profile。
# 这一步不会启动或停止任何进程。
python configure.py --root D:/AI        # 只看不写加 --dry-run
```

`configure.py` 会写出 `profile.local.json`，并打印它找到了什么：

```text
[detected] llm: running process
[detected] tts: running process
[missing ] image: fill this in by hand
[port    ] 8080 (llm) listening
[port    ] 7860 (tts) listening
[port    ] 8188 (image) free
```

把宿主指向这个文件并重启即可。没探测到的服务会写成 `enabled: false`，插件仍然合法，只是少做一件事。

## 状态机

```text
LLM 常驻
  ├─ TTS 请求 → 停 LLM（可停辅助服务）→ 确保 TTS 在线 → 完成后恢复 LLM
  └─ 生图请求 → 停 LLM（可停 TTS/辅助服务）→ 启动图像服务 → 等就绪
       → 出图完成 → 等队列跑空 → 停图像服务 → 恢复 LLM
```

## 特性

- 配置驱动：路径、端口、进程名、启动命令、日志、超时全部在 `profile.local.json`
- 三类服务：`llm` / `tts` / `image`，外加任意数量的只停不启 `aux_services`
- 引擎 / 版本 profile：多个 LLM 引擎（如 `moe`、`dense27b`）、多个 TTS 版本（`2.5` / `2.0`，支持 `auto`）
- TTS 文件协议：`tts_busy.flag` 表示合成中，`tts_ready.flag` 表示显存已就绪
- 为 AI 陪伴回合设计：文字回复 → 语音合成 → 自拍/改图可以在同一回合内安全切换
- 生图队列：恢复前轮询 ComfyUI `/queue`，不打断一批多张图
- 夜间静默：默认 23:00–07:00 不重新拉起本地 LLM，也不启动 TTS/IndexTTS（已在跑的合成可以结束，结束后看门狗再停 webui）
- 兼容层：`_prepare_switch`、`_recover_after_tts`、事件标记、flag 路径全部保留
- 默认不切 provider：交给 AstrBot 的 fallback 链，避免与 `default_provider_id` 打架
- 原生 DSH Cordis 插件 + MCP 备用入口 + 跨进程协调锁

## AstrBot 安装

```bash
git clone https://github.com/xiaoxiao150/vram-switcher   AstrBot/data/plugins/astrbot_plugin_vram_switcher
```

也可以直接从插件市场安装：搜 **VRAM Switcher**（[cloud.astrbot.app](https://cloud.astrbot.app/)）。

然后在插件配置里确认：

- `profile_path` = 你生成的 `profile.local.json`（建议放
  `AstrBot/data/plugin_data/astrbot_plugin_vram_switcher/`，填绝对路径）
- `local_llm_engine` = profile 里的引擎键（用 `configure.py` 生成时是 `detected`）
- `index_tts_version` = `auto` 或 `2.5` / `2.0`

没跑过 `configure.py` 的话，也可以从 `profile.example.json` 手动复制一份改。
如果 profile 找不到或 JSON 非法，插件会记录错误并保持禁用，不会崩溃或乱杀进程。

## IndexTTS 集成：为什么必须停进程

IndexTTS webui 没有“卸载模型”的 HTTP API。它常驻时占 2.3–5.3 GB；本地 LLM 占 4.9 GB；ComfyUI 出图要 6–7 GB。12 GB 卡上三者不能共存，只能让 TTS 在合成前把卡让出来，合成后再还回去。

这里用的是**进程级互斥 + 两个文件**：

```text
AstrBot 决定发语音
  │
  ├─ TTS 插件写 tts_busy.flag（并删掉上一轮的 tts_ready.flag）
  │
  ▼
VRAM Switcher（每 2 s 轮询）
  ├─ 停本地 LLM（以及 profile 里的 aux_services）
  ├─ 确保 IndexTTS webui 在线（没起就按 versions 启动，等端口 listen）
  └─ 写 tts_ready.flag
  │
  ▼
TTS 插件等到 ready + webui 在线，才提交 /gen_single
  │
  ▼
合成结束，TTS 插件删掉 tts_busy.flag
  │
  ▼
VRAM Switcher 恢复：等 ComfyUI 队列跑空 → 停图像服务 → 拉起本地 LLM
```

关键点：

- `tts_ready.flag` 必须在**真正停完 LLM 之后**写。TTS 抢跑会让 `s2mel` 权重被 Windows 换页：实测 `0.12 s/步 → 30 s/步`，25 步要 731 秒。
- 电话场景下 busy flag 每 1.5 秒续一次，避免被看门狗当成崩溃残留。
- 900 秒没更新的 busy flag 会被清理，防止进程崩了以后永远卡在“合成中”。
- `version_selector: auto` 跟随正在运行的 2.5 / 2.0；两个都没起时用 `default_version`。
- IndexTTS 冷启动约 60 秒，TTS 侧等待 ready 的超时要给够（生产用 150 秒）。

最小 profile：

```json
{
  "services": {
    "tts": {
      "enabled": true,
      "port": 7860,
      "process_names": ["python.exe"],
      "cmdline_contains": ["webui.py"],
      "cmdline_any": ["IndexTTS", "Index-TTS"],
      "stop": {"timeout_seconds": 20, "tree": false},
      "busy_flag": "${DATA_DIR}/tts_busy.flag",
      "ready_flag": "${DATA_DIR}/tts_ready.flag",
      "flag_stale_seconds": 900,
      "start_cooldown_seconds": 150,
      "version_selector": "auto",
      "default_version": "2.5",
      "versions": {
        "2.5": {
          "args": ["D:/AI/IndexTTS2.5/.venv/Scripts/python.exe",
                   "D:/AI/IndexTTS2.5/webui.py", "--version", "2.5", "--fp16"],
          "cwd": "D:/AI/IndexTTS2.5"
        }
      }
    }
  }
}
```

完整的适配器代码、AstrBot 钩子优先级和失败排查见 [`docs/indextts.md`](docs/indextts.md)。**注意**：这个仓库只提供调度侧；你的 TTS 插件必须按上面的协议写/删 flag，否则联动不会生效。

## Profile 参考

完整示例见 [`profile.example.json`](profile.example.json)。

profile 字符串里支持两个内置变量，避免把绝对路径写死：

- `${PLUGIN_DIR}`：插件源码目录
- `${DATA_DIR}`：AstrBot 的 `plugin_data/astrbot_plugin_vram_switcher` 目录
- `${ENV_NAME}`：读取同名环境变量

### LLM

```json
{
  "services": {
    "llm": {
      "enabled": true,
      "port": 8080,
      "process_names": ["llama-server.exe"],
      "stop": {"timeout_seconds": 15, "tree": true},
      "start": {
        "engine": "moe",
        "profiles": {
          "moe": {
            "args": ["D:/AI/llama/llama-server.exe", "-m", "D:/AI/models/model.gguf", "-c", "32768"],
            "cwd": "D:/AI/llama",
            "log_file": "${DATA_DIR}/llm.log"
          }
        }
      },
      "ready": {"type": "process", "timeout_seconds": 50},
      "recovery_wait_seconds": 45,
      "night_quiet": {"enabled": true, "start": "23:00", "end": "07:00"}
    }
  }
}
```

- `process_names` 是停止/检查用的进程名白名单。
- `start.profiles` 可以有多个引擎；AstrBot 配置里的 `local_llm_engine` 选其中一个。
- `ready.type = process` 表示“看到进程存在”即算恢复；如果你的启动器会先起一个壳进程，建议改成 `port`。
- `recovery_wait_seconds` 是启动后等待时间；0 表示立即检查（适合测试）。

### TTS

```json
{
  "services": {
    "tts": {
      "enabled": true,
      "port": 7860,
      "process_names": ["python.exe"],
      "cmdline_contains": ["webui.py"],
      "cmdline_any": ["IndexTTS", "Index-TTS"],
      "stop_extra": [{"process_names": ["cmd.exe"], "cmdline_any": ["IndexTTS"]}],
      "stop": {"timeout_seconds": 20, "tree": false},
      "busy_flag": "${DATA_DIR}/tts_busy.flag",
      "ready_flag": "${DATA_DIR}/tts_ready.flag",
      "flag_stale_seconds": 900,
      "start_cooldown_seconds": 150,
      "version_selector": "auto",
      "default_version": "2.5",
      "versions": {
        "2.5": {"args": ["python.exe", "webui.py", "--version", "2.5", "--fp16"], "cwd": "D:/AI/IndexTTS2.5"}
      }
    }
  }
}
```

TTS 插件需要配合两个文件：

- 合成开始时创建 `busy_flag`，结束时删除；
- VRAM Switcher 在 LLM 停掉、TTS 在线后写 `ready_flag`，TTS 插件应在提交合成前等它出现。

`flag_stale_seconds` 用于清理崩溃后残留的 busy 文件，避免永远卡在“合成中”。

### Image

```json
{
  "services": {
    "image": {
      "enabled": true,
      "port": 8188,
      "process_names": ["python.exe"],
      "cmdline_contains": ["ComfyUI"],
      "stop_by_port": true,
      "start": {
        "args": ["D:/AI/ComfyUI/python/python.exe", "-u", "-s", "D:/AI/ComfyUI/ComfyUI/main.py", "--windows-standalone-build"],
        "cwd": "D:/AI/ComfyUI/ComfyUI"
      },
      "ready": {"type": "port", "timeout_seconds": 300},
      "queue": {"timeout_seconds": 420, "interval_seconds": 6}
    }
  }
}
```

`stop_by_port: true` 会额外杀掉监听该端口的进程。只在你确认端口属于该服务时使用。

### 辅助服务

```json
{
  "aux_services": [
    {
      "key": "prompt_optimizer",
      "process_names": ["pe-server.exe"],
      "stop": {"timeout_seconds": 10, "tree": true}
    }
  ]
}
```

辅助服务只停不启。适合提示词优化器、临时缓存服务等“下次使用时会自己拉起”的进程。

## 触发规则

```json
{
  "triggers": {
    "tts_keywords": ["语音"],
    "image_prefixes": ["aimg", "img2img", "改图", "/改图"],
    "image_tool_names": ["generate_selfie", "generate_image", "comfyui_txt2img"],
    "tts_reply_marker_pattern": "[\\[【]\\s*语音\\s*[\\]】]"
  }
}
```

- `tts_keywords`：用户消息包含关键词 → TTS 预切换。
- `image_prefixes`：消息首词匹配 → 生图预切换。
- `image_tool_names`：LLM 决定调用这些工具前 → 生图预切换。
- `tts_reply_marker_pattern`：LLM 回复里出现语音标记 → TTS 预切换。

AstrBot 插件配置里的 `extra_tts_keywords` / `extra_comfyui_prefixes` 会追加到 profile 对应列表。

## 与其它 AstrBot 插件兼容

为了兼容已有生态，插件保留以下接口：

- 事件 extra：`_vram_switcher_active`、`_vram_switcher_reason`、`_vram_switcher_indextts_killed`
- `await plugin._prepare_switch(event, "tool:...")`
- `await plugin._recover_after_tts()`
- `on_decorating_result(event)`（也会把最终恢复跑完）
- 模块级兼容函数（`qq_voice_adapter` 的看门狗使用）：
  `_is_indextts_running`、`_kill_indextts_webui`、`_start_indextts_webui`、
  `_is_comfyui_running`、`_is_comfyui_process_running`、`_is_tts_busy`
- 夜间静默时，`_kill_indextts_webui()` 在 `tts_busy.flag` 有效期间会跳过，
  避免把正在合成的语音拦腰杀掉；合成结束后下一轮看门狗再停
- flag 路径可通过 profile 配置；默认仍建议放在 `plugin_data/astrbot_plugin_vram_switcher/`

如果其它插件硬编码了 flag 路径，保持 `busy_flag` / `ready_flag` 指向同一位置即可。

## DSH 原生 Cordis 插件

这个仓库同时就是一个原生 DSH 插件包（`dsh-vram-switcher`）：

```text
DSH Cordis -> dsh-plugin/index.js -> ctx.tools.register(...)
                              └─ Python daemon.py -> vram_switcher.Orchestrator
```

- `package.json` 的 `dsh.bundle.patch` 指向仓库根的 `cordis.patch.yml`
- `dsh-plugin.json` 是 DSH manifest v0.15
- 原生工具：`vram_status` / `vram_acquire` / `vram_release`
- DSH 加载时启动一个长驻 Python daemon，复用 AstrBot 的同一套核心和锁

安装与市场发布说明见 [`dsh/README.md`](dsh/README.md)，配置覆盖模板见
[`dsh/cordis.patch.example.yml`](dsh/cordis.patch.example.yml)。DSH 和 AstrBot
必须使用同一个 `profile.local.json` 与 `dataDir`，否则跨进程锁不会生效。

如果当前 DSH 轮次正在使用本地 LLM，不要调用 `vram_acquire`，否则它会停掉正在
生成回复的模型。

MCP 仍作为备用入口保留：`mcp_server.py` 可给 Claude Desktop、Codex 等没有原生
Cordis 插件的宿主使用；不要和原生插件同时加载，避免重复管理同一组服务。

## 开发与测试

核心包 `vram_switcher/` 不依赖 AstrBot，可以直接测试：

```bash
python -m unittest discover -s tests -v
```

测试使用 FakeSystem，不会启动/停止真实进程，也不需要 GPU；`tests/test_configure.py` 会验证 `configure.py --dry-run` 只输出 JSON、拒绝覆盖已有文件，并确认生成的 profile 能被真实 loader 接受。

CI 见 [`.github/workflows/ci.yml`](.github/workflows/ci.yml)，在 Python 3.10 / 3.11 / 3.12 上跑单元测试。

### 开发目录与部署（重要）

**本仓库是开发副本。** 不要在 AstrBot 的
`data/plugins/astrbot_plugin_vram_switcher/` 里直接编辑或提交；生产目录只由
`scripts/deploy.py` 覆盖，并且不再包含 `.git`。改完、跑完测试再部署：

```bash
python scripts/deploy.py --dry-run
python scripts/deploy.py
```

部署后按单个 PID 重启 AstrBot（不要 `/T` 树杀）。这样开发中的重构不会直接改坏正在运行的陪伴链路。

## 起源

- **2026-08**：第一版写在一个本地 AI 陪伴项目里。当时她在 AstrBot 里用本地 LLM 聊天、用 IndexTTS 发语音、用 ComfyUI 自拍，三件事只能手动杀进程轮换，经常把正在生成的回复卡死。
- **2026-09-28**：一次请求在 ComfyUI 冷启动窗口（当时只等 120 秒）误落到云端节点；此后把就绪等待改成 300 秒，并把“停/起”做成显式状态机。
- **2026-10**：重构成 profile 驱动的 2.0，同一套核心接上 AstrBot 插件与 DSH 原生 Cordis 插件，开源。
- **2026-10-26**：公开仓库。代码在公开前已经在一台 12 GB 机器上跑了两个多月。

公开时间不等于开始时间。这段历史写在这里，是因为在“谁先”这个问题上，最早的生产日志比仓库创建日期更有说服力；也是因为这个项目的第一需求从来不是“调度 GPU”，而是“让她在只有一张 12 GB 卡的情况下，仍然能同时聊天、发语音和自拍”。

## 已知限制

- Windows 优先；Linux 有基本的 `ps`/`kill` 支持，但进程名和启动器差异需要自行适配。
- NVIDIA 是生产验证路径；A 卡/Intel 的调度层可用，但三个服务的后端支持差异很大，见 [GPU 兼容性](docs/gpu-support.md)。
- 不读取 `nvidia-smi` 显存数值；显存是否释放由进程/端口/标志文件推断。这样更可预测，也避免驱动差异。
- TTS 必须实现 busy/ready 文件协议；其它 TTS 可以自己写一个薄适配器。
- ComfyUI 队列接口默认为 `/queue`；其它图像服务可以在 profile 里改 `queue.url`，或设为不可用。
- 默认不做 provider 切换；需要的话继承 `NullProviderHooks` 并在适配器里实现。

## 在市场里安装

- **AstrBot**：WebUI → 插件市场 搜 **VRAM Switcher**，或
  [cloud.astrbot.app](https://cloud.astrbot.app/) 搜仓库 `xiaoxiao150/vram-switcher`。
- **DSH**：已提交 [awesome-dsh-plugin 收录](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin/pull/9555)（PR #9555），合并后即可在 DSH 插件市场搜到 **vram-switcher**。
- 源码安装：

  ```bash
  # AstrBot
  git clone https://github.com/xiaoxiao150/vram-switcher     AstrBot/data/plugins/astrbot_plugin_vram_switcher

  # DSH（包名 dsh-vram-switcher）
  dsh plugin --profile web add github:xiaoxiao150/vram-switcher
  ```

> 仓库名是 `vram-switcher`，但 AstrBot 会把插件安装到
> `data/plugins/astrbot_plugin_vram_switcher/`（取自 `metadata.yaml` 的 `name`），
> 两者不一致是正常的。

发布信息：[v2.0.5 Release](https://github.com/xiaoxiao150/vram-switcher/releases/tag/v2.0.5)。

## 安全模型

| 风险 | 处理 |
|---|---|
| 杀错进程 | 进程名 **且** 命令行过滤，可选端口白名单；没有裸名字匹配 |
| 杀到自己 | 排除当前 PID 与父 PID |
| Shell 注入 | `start` 是 argv 数组，`shell=False` 执行 |
| 两个宿主打架 | `coordinator.lock` 覆盖整个 acquire → release 窗口 |
| 配置写错 | 加载时校验；非法 profile 只禁用插件并记录原因，不猜 |
| 路径泄漏 | `profile.local.json` 已 git-ignore；示例里没有真实路径 |

## License

MIT. See [LICENSE](LICENSE).
