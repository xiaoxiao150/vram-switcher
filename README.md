# VRAM Switcher

A configurable local-GPU task coordinator for AstrBot.

当一张显卡同时要跑本地 LLM、TTS 和 ComfyUI 时，显存往往不够。VRAM Switcher 在任务开始前停掉当前不需要的服务，在任务结束后按配置把本地 LLM 拉回来，并可选地保留 TTS 服务待命。

It is deliberately **not** tied to a particular model or vendor. Processes, ports, start commands and flag files all live in a profile.

## 它解决什么问题

典型场景：12GB 显卡上同时存在：

- 本地 LLM（llama.cpp / llama-server）
- IndexTTS 或其它 TTS webui
- ComfyUI + SD/Flux 模型

三者同时驻留会触发 Windows 显存换页，表现为预填从几百 tok/s 掉到个位数、生图一张几分钟、甚至整台机器卡死。VRAM Switcher 用一个很小的状态机做互斥：

```text
LLM 常驻
  ├─ 收到 TTS 请求 → 停 LLM（可停辅助服务）→ 确保 TTS 在线 → 完成后恢复 LLM
  └─ 收到生图请求 → 停 LLM（可停 TTS/辅助服务）→ 启动图像服务 → 等就绪
       → 生图完成后先等队列跑空 → 停图像服务 → 恢复 LLM
```

它只操作**配置里明确列出的服务**，不会按“所有 python.exe”或“所有 llama 进程”无差别杀进程。

## 特性

- 配置驱动：路径、端口、进程名、启动命令、日志、超时全部在 `profile.local.json`
- 三类服务：`llm` / `tts` / `image`，以及任意数量的 `aux_services`
- LLM 引擎 profile：例如 `moe`、`dense27b`，由 `local_llm_engine` 选择
- TTS 版本 profile：例如 `2.5` / `2.0`，支持 `auto` 跟随当前运行版本
- TTS 文件协议：`tts_busy.flag` 表示合成中，`tts_ready.flag` 表示显存已就绪
- 生图队列：恢复前轮询 ComfyUI `/queue`，避免打断一批多张图
- 夜间静默：23:00–07:00 默认不重新拉起本地 LLM（可配置）
- 安全边界：进程名 + 命令行子串 + 端口白名单；排除自身与父进程
- 兼容层：保留 `_prepare_switch`、`_recover_after_tts`、事件标记和 flag 路径，原 AstrBot 生态插件无需改动
- 默认不切 provider：provider 选择交给 AstrBot 的 fallback 链，避免与 `default_provider_id` 打架
- 原生 DSH Cordis 插件：`ctx.tools.register` 暴露 `vram_status` / `vram_acquire` / `vram_release`
- MCP 备用入口：`mcp_server.py` 供 Claude Desktop、Codex 等宿主使用
- 跨进程协调锁：AstrBot 和 DSH 共用 `coordinator.lock`，避免同时切换同一组服务

## 安装

1. 把本目录放到：

   ```text
   AstrBot/data/plugins/astrbot_plugin_vram_switcher
   ```

2. 复制 profile：

   ```text
   profile.example.json -> profile.local.json
   ```

3. 编辑 `profile.local.json`，至少填好：

   - `services.llm.start.profiles.<engine>.args/cwd`
   - `services.tts.versions.<version>.args/cwd`（如果不用 TTS 可 `enabled: false`）
   - `services.image.start.args/cwd`（如果不用生图可 `enabled: false`）
   - 各服务的 `port`、`process_names`

   建议把 `profile.local.json` 放在 `AstrBot/data/plugin_data/astrbot_plugin_vram_switcher/`，再把插件配置里的 `profile_path` 填成绝对路径；这样源码目录里不会残留本机路径。

4. 重启 AstrBot。

5. 在 AstrBot 插件配置里确认：

   - `profile_path` = `profile.local.json`
   - `local_llm_engine` = profile 里的引擎键，例如 `moe`
   - `index_tts_version` = `auto` 或 `2.5` / `2.0`

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

测试使用 FakeSystem，不会启动/停止真实进程，也不需要 GPU。

CI 见 [`.github/workflows/ci.yml`](.github/workflows/ci.yml)，在 Python 3.10 / 3.11 / 3.12 上跑单元测试。

## 安全说明

- 只操作 profile 中明确配置的进程名/命令行/端口；没有“按进程名通配杀所有”的默认行为。
- 自动排除当前 Python 进程及其父进程。
- `stop_by_port` 和 `tree` 默认关闭；只有明确配置时才启用。
- `start_service` 只执行 profile 里的参数数组，不经过 shell 字符串拼接。
- 不要把 API key、Cookie、个人绝对路径提交到公开仓库；`profile.local.json` 已加入 `.gitignore`。

## 已知限制

- Windows 优先；Linux 有基本的 `ps`/`kill` 支持，但进程名和启动器差异需要自行适配。
- 不读取 `nvidia-smi` 显存数值；显存是否释放由进程/端口/标志文件推断。这样更可预测，也避免驱动差异。
- TTS 必须实现 busy/ready 文件协议；其它 TTS 可以自己写一个薄适配器。
- ComfyUI 队列接口默认为 `/queue`；其它图像服务可以在 profile 里改 `queue.url`，或设为不可用。
- 默认不做 provider 切换；需要的话继承 `NullProviderHooks` 并在适配器里实现。

## 在市场里安装

- **AstrBot**：在 AstrBot WebUI → 插件市场 搜索 **VRAM Switcher**，或
  [cloud.astrbot.app](https://cloud.astrbot.app/) 搜索仓库 `xiaoxiao150/vram-switcher`。
- **DSH**：在 DSH 插件市场搜索 **vram-switcher**（来自
  [awesome-dsh-plugin](https://awesome-dsh-plugin.com/p/xiaoxiao150/vram-switcher/)）。
- 也可以直接从源码安装：

  ```bash
  # AstrBot
  git clone https://github.com/xiaoxiao150/vram-switcher     AstrBot/data/plugins/astrbot_plugin_vram_switcher

  # DSH（源码安装；包名 dsh-vram-switcher）
  dsh plugin --profile web add github:xiaoxiao150/vram-switcher
  ```

> 仓库名是 `vram-switcher`，但 AstrBot 会把插件安装到
> `data/plugins/astrbot_plugin_vram_switcher/`（取自 `metadata.yaml` 的 `name`），
> 两者不一致是正常的。

发布信息：[v2.0.0 Release](https://github.com/xiaoxiao150/vram-switcher/releases/tag/v2.0.0)。

## License

MIT. See [LICENSE](LICENSE).
