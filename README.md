# VRAM Switcher

**[English](README.en.md)** · [Releases](https://github.com/xiaoxiao150/vram-switcher/releases) · AstrBot / DeepSeek Harness

**让一张 12 GB 显卡同时跑本地 LLM、TTS 和 ComfyUI —— 不 OOM、不 30 秒一步、不用手动杀进程。**

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

解法不是换更大的卡，而是确保关键时刻只有一个在占显存。

## 它做什么

- 任务前**停掉不需要的**、**拉起需要的**，结束后再放回去。
- **真的等就绪**：ComfyUI 必须 8188 在 listen，而不是进程刚起（早 1 秒连接曾让每次自拍都失败）；一批图会先等 `/queue` 跑空再停服务。
- **只动你列出的东西**：进程名 + 命令行过滤 + 可选端口白名单，没有“杀掉所有 python.exe”这条路。
- **跨进程串行**：AstrBot 插件和 DSH 插件共用 `coordinator.lock`，语音事件和生图任务不会同时切服务。
- **一套核心，两个宿主**：AstrBot（Python 插件）与 DeepSeek Harness（原生 Cordis 插件 + 小 Python daemon）。另带 MCP stdio 服务，可给 Claude Desktop / Codex 用。

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
- 生图队列：恢复前轮询 ComfyUI `/queue`，不打断一批多张图
- 夜间静默：默认 23:00–07:00 不重新拉起本地 LLM
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

## 已知限制

- Windows 优先；Linux 有基本的 `ps`/`kill` 支持，但进程名和启动器差异需要自行适配。
- 不读取 `nvidia-smi` 显存数值；显存是否释放由进程/端口/标志文件推断。这样更可预测，也避免驱动差异。
- TTS 必须实现 busy/ready 文件协议；其它 TTS 可以自己写一个薄适配器。
- ComfyUI 队列接口默认为 `/queue`；其它图像服务可以在 profile 里改 `queue.url`，或设为不可用。
- 默认不做 provider 切换；需要的话继承 `NullProviderHooks` 并在适配器里实现。

## 在市场里安装

- **AstrBot**：WebUI → 插件市场 搜 **VRAM Switcher**，或
  [cloud.astrbot.app](https://cloud.astrbot.app/) 搜仓库 `xiaoxiao150/vram-switcher`。
- **DSH**：插件市场搜 **vram-switcher**
  （[awesome-dsh-plugin](https://awesome-dsh-plugin.com/p/xiaoxiao150/vram-switcher/)）。
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

发布信息：[v2.0.2 Release](https://github.com/xiaoxiao150/vram-switcher/releases/tag/v2.0.2)。

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
