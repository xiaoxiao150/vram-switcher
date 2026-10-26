# GPU 兼容性：NVIDIA / AMD / Intel

一句话结论：

> **调度器本身不挑显卡；挑卡的是它调度的三个服务。NVIDIA 是已验证的生产路径，AMD 的调度层可用，但 IndexTTS 在 Windows A 卡上是最容易卡住的一环。**

## 状态总表

图例：✅ 生产验证 / ⚠️ 可运行但有条件 / ❌ 不建议 / — 未验证

| 组件 | NVIDIA Windows | AMD Windows | AMD Linux | Intel |
|---|---|---|---|---|
| VRAM Switcher 调度器 | ✅ 作者生产使用 | ⚠️ 机制可用，未实测 | ⚠️ 基础支持 | ⚠️ 未验证 |
| llama.cpp | ✅ CUDA | ⚠️ Vulkan（社区常用）/ ROCm 构建 | ⚠️ ROCm / Vulkan | ⚠️ Vulkan / SYCL |
| ComfyUI | ✅ CUDA | ⚠️ DirectML 或 ZLUDA | ⚠️ ROCm（较成熟） | ⚠️ 未验证 |
| IndexTTS | ✅ CUDA | ⚠️ 取决于显卡/驱动/PyTorch 轮子 | ⚠️ ROCm 可行但需实测 | ⚠️ 未验证 |

“调度器可用”不等于“三个服务都能跑”。先分别把服务跑通，再接 profile。

---

## 1. 为什么调度器不挑卡

`vram_switcher` 只做四件事：

- 按进程名 + 命令行 + 可选端口找到服务；
- 停掉/启动 profile 里的命令；
- 等端口 listen 或进程存在；
- 读写 `tts_busy.flag` / `tts_ready.flag`。

代码里没有 CUDA、ROCm、`nvidia-smi`、厂商 SDK 调用。Windows 用 psutil 或 PowerShell 列进程，Linux 用 `ps` / `kill`。所以：

- A 卡、I 卡、甚至多台机器，只要服务能被进程/端口描述，调度层就能工作；
- 它不会读取显存数值，也不会因为“不是 NVIDIA”而拒绝启动；
- 代价是它无法确认“显存真的释放了”，只能靠进程退出 + 端口/flag 推断。A 卡驱动有时会比进程晚一点回收显存，见下面的调参。

## 2. NVIDIA（已验证路径）

作者的生产环境：12 GB 笔记本 N 卡，Windows，llama.cpp CUDA + IndexTTS + ComfyUI。

- **llama.cpp**：CUDA 后端，最省心；`llama-server.exe`。
- **ComfyUI**：CUDA，自定义节点和加速算子兼容性最好。
- **IndexTTS**：PyTorch CUDA，官方路径。
- **就绪判断**：profile 里给 `port` 就会按端口判断；这是推荐做法。
- **显存回收**：进程退出后通常很快释放；仍建议 `recovery_wait_seconds` 给 30–60 秒，别设 0。

N 卡不需要额外配置；按 README 的 Profile 参考填即可。

## 3. AMD + Windows（调度可用，服务有条件）

### 3.1 llama.cpp

推荐 **Vulkan 后端**的 `llama-server.exe`：

- 不需要 ROCm，装好显卡驱动即可；
- 进程名通常仍是 `llama-server.exe`，profile 不用大改；
- 性能通常低于同档 CUDA，但对 LLM 够用；
- 也可以用 ROCm/HIP 构建，但 Windows 上对具体显卡和驱动的支持范围要看构建方说明。

### 3.2 ComfyUI

Windows A 卡有三条路：

1. **DirectML 分支**：安装简单，兼容性取决于节点；比 CUDA 慢。
2. **ZLUDA**：社区方案，可以跑 CUDA 构建；稳定性和节点兼容性因版本而异。
3. **Linux + ROCm**：最成熟的 A 卡路径，但不再是 Windows。

注意：

- 依赖 CUDA 的自定义节点、`flash-attn`、`bitsandbytes`、部分采样器/加速算子可能直接报错；
- 12 GB A 卡跑 SDXL/Flux 通常要量化、降分辨率或减少 batch；
- 先单独生成一张图，确认工作流能跑，再接调度器。

### 3.3 IndexTTS（最大的门槛）

IndexTTS 是 PyTorch 应用，不是 CUDA-only，但 Windows + A 卡的组合最容易出问题：

- Windows 上 PyTorch 的 ROCm 轮子覆盖范围不如 Linux，具体看显卡型号和驱动；
- DirectML 不保证 IndexTTS 用到的所有算子；
- 可行路线通常是：**Linux + ROCm**、**WSL2 + ROCm**（先查显卡是否在 ROCm 支持列表），或者 CPU（能跑，但很慢，基本失去意义）。

如果 IndexTTS 在这张 A 卡上起不来：

- 调度器仍然可以让 **LLM + ComfyUI** 轮流用卡；
- 语音链路需要换 TTS，或把 TTS 放到另一台机器；
- 不要指望“调度器能修好一个跑不起来的 webui”。

## 4. AMD + Linux（更现实，但项目支持是基础的）

- llama.cpp：ROCm 或 Vulkan，通常没问题。
- ComfyUI：ROCm 路径比 Windows 成熟，但仍有节点兼容性问题。
- IndexTTS：ROCm PyTorch 可行，具体看显卡。
- 本项目：Linux 只有基础 `ps` / `kill` 支持；进程名通常是 `llama-server`（没有 `.exe`），`python` 而不是 `python.exe`。需要自己改 `process_names` / `cmdline_any`。`configure.py` 的文件探测仍可用，但生成后要人工核对。

## 5. Intel

同一套逻辑：调度层可用，服务取决于后端（Vulkan / SYCL / oneAPI / DirectML）。本项目没有在 Intel 上验证过，按“未验证”对待。

---

## 6. A 卡上的 profile 调整建议

```json
{
  "services": {
    "llm": {
      "port": 8080,
      "process_names": ["llama-server.exe"],
      "ready": {"type": "port", "timeout_seconds": 90},
      "recovery_wait_seconds": 60,
      "stop": {"timeout_seconds": 20, "tree": false}
    },
    "tts": {
      "port": 7860,
      "process_names": ["python.exe"],
      "cmdline_contains": ["webui.py"],
      "cmdline_any": ["IndexTTS", "Index-TTS"],
      "ready": {"type": "port", "timeout_seconds": 180},
      "stop": {"timeout_seconds": 30, "tree": false}
    },
    "image": {
      "port": 8188,
      "process_names": ["python.exe"],
      "cmdline_contains": ["ComfyUI"],
      "ready": {"type": "port", "timeout_seconds": 300},
      "queue": {"timeout_seconds": 420, "interval_seconds": 6},
      "stop_by_port": true,
      "stop": {"timeout_seconds": 20, "tree": false}
    }
  }
}
```

要点：

- **`ready.type` 用 `port`**，不要只靠进程存在；A 卡上进程起来不等于服务可用。
- **超时给足**：Vulkan/ROCm 首次编译/加载可能比 CUDA 慢，`ready.timeout_seconds` 至少 90–300 秒。
- **`recovery_wait_seconds` 加大**：给驱动回收显存留时间；N 卡 45 秒，A 卡可以先 60 秒。
- **`process_names` / `cmdline_any` 收窄**：A 卡构建的进程名可能不同；不要只写 `python.exe`。`cmdline_any` 只有在启动命令行确实包含该词时才有效，不确定就留空，靠 `process_names` + `port`。
- **`stop_by_port` 只给确认独占端口的服务**；不要给 LLM 用，避免误杀别的监听进程。
- **TTS 侧多等 1–2 秒**：如果实测 ready 后仍有换页，在 TTS 插件里等 `tts_ready` 后再 `sleep(1~2)` 提交；调度器只能确认进程退出，不能确认驱动已回收显存。

## 7. 接入前检查清单

- [ ] 单独启动 llama.cpp，能正常对话；记录启动耗时和显存
- [ ] 单独启动 ComfyUI，能生成一张图；记录工作流是否依赖 CUDA-only 节点
- [ ] 单独启动 IndexTTS webui，能合成一段音频
- [ ] 三个都跑通后，再运行 `python configure.py --root ...` 生成 profile
- [ ] 核对 `process_names` / `cmdline_any` / 端口，尤其是 A 卡构建
- [ ] 用 `--dry-run` 看 profile，再 `vram_status` 看状态
- [ ] 先用一条语音、一张图做真实切换测试，观察是否还有换页
- [ ] 用任务管理器（Windows）或 `rocm-smi` / `nvtop`（Linux）观察显存；调度器本身不读显存

## 8. 状态说明

- **NVIDIA Windows 12 GB**：作者生产路径，已验证。
- **AMD**：调度层没有厂商依赖，机制上可用；但作者没有 A 卡硬件，**没有做过端到端实测**。IndexTTS/ComfyUI 的 A 卡支持取决于你的显卡、驱动和安装方式。
- **Linux**：基础支持，需要自行适配进程名和启动器。
- 欢迎在 issue 里附上显卡型号、系统、三个服务各自能否跑通，以及日志；这些信息可以直接补进这张表。

---

相关文档：

- [IndexTTS 集成](indextts.md)
- [和 llama-swap / berth 的区别](comparison.md)
- [README 的 Profile 参考](../README.md#profile-参考)
