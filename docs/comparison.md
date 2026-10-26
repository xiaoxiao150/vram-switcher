# 和 llama-swap / berth / vhotseat 的区别

显存协调已经不缺项目。这份对比不是要证明“只有我们是对的”，而是帮你判断：**你的场景到底该用哪一个。**

数据截至 2026-10-26。

| | 本项目 | [llama-swap](https://github.com/mostlygeek/llama-swap) | [berth](https://github.com/T-Crypt/berth) | [vhotseat](https://github.com/wandermyz/vhotseat) |
|---|---|---|---|---|
| 形态 | AstrBot 插件 + DSH 原生 Cordis 插件，共用 Python 核心 | Go 单文件代理 | Go daemon + CLI + gateway | Python gate + ComfyUI 节点 |
| 主要解决 | **宿主事件边界上的进程停/起** | 按请求切换受管后端 | 通用 GPU 租约、优先级、排队 | LLM ↔ ComfyUI 轮流 |
| 客户端要改地址吗 | **不用** | 要指向它的代理端口 | 要走 gateway 或 `berth run` | 要装 gate / node |
| 知道 TTS 合成何时结束 | **有 busy/ready 文件协议** | 不知道 | 不知道 | 不知道 |
| 停进程 | 是（严格白名单） | 是（受管后端） | 是（drain 后 TERM/KILL） | 是 |
| 多卡 / 游戏 / VM / 优先级 | 无 | 无 | 有（部分实验性） | 无 |
| 宿主集成 | **AstrBot hooks + DSH 原生工具** | 通用 OpenAI/Anthropic | 通用 + agent skill | Linux + Strata/ComfyUI |
| 状态 | 2.0.x，MIT | 6.1k★，成熟 | alpha 0.3.6，Apache-2.0 | 9★，MIT，Linux |

---

## 什么时候选 llama-swap

- 你的所有客户端（OpenAI SDK、WebUI、脚本）都能改 `base_url`；
- 你主要是 LLM 换模型，偶尔加图像/音频后端；
- 你想要一个成熟、单文件、社区大的方案。

llama-swap 已经覆盖 llama.cpp、vLLM、stable-diffusion.cpp、audio.cpp、ComfyUI 等后端，6k+ star。**如果你只是换模型，不需要犹豫，用它。**

它不适合的地方：它按请求切换自己管理的后端；对 AstrBot 插件内部的事件（回复里的 `[语音]` 标记、工具调用前后）、对 IndexTTS 合成是否真正结束，它没有感知。

## 什么时候选 berth

- 你想要的不是“停两个进程”，而是“整张卡谁先谁后”；
- 需要优先级、排队、游戏/VM/多卡；
- 能接受 alpha 软件，愿意用 `berth run` 包一层或走 gateway。

berth 是这个方向里范围最大的项目：租约、优先级、ComfyUI job 边界、park 到内存、游戏检测、VM 直通。它甚至能识别 Kokoro 这类语音引擎。

它不适合的地方：公开仓库 2026-10-11 才建，alpha；Windows 标注实验性；没有 AstrBot/DSH 原生插件，也没有 TTS 的 busy/ready 协议；要让现有 AstrBot 插件不改地址，需要额外适配。

## 什么时候选 vhotseat

- 你在 Linux + NVIDIA 上；
- 你用的是 Strata + ComfyUI；
- 你只需要这两者轮流，且愿意装它的 gate / ComfyUI 节点。

它做得更聚焦，Windows 还在计划中。

## 什么时候选本项目

- 你在 **AstrBot** 或 **DeepSeek Harness** 上跑本地 AI 陪伴；
- 你有 **IndexTTS**（或别的没有 unload API 的 TTS），需要“合成真正结束才恢复 LLM”；
- 你不想改客户端地址、不想加代理端口、不想装第二个 daemon；
- 你能接受它只做“宿主事件 + 进程停/起”这一层。

它多做的是：AstrBot 的五个事件钩子、DSH 的三个原生工具、IndexTTS 文件协议、真实进程停启、跨进程协调锁。
它少做的是：多卡、游戏/VM、优先级队列、网关、`nvidia-smi` 轮询、provider 切换、模型下载管理。

---

## 为什么不直接给 llama-swap / berth 提 PR？

因为缺的那块不是“调度算法”，而是**宿主语义**：

- AstrBot 的“这条回复带 `[语音]`”发生在文本生成之后、TTS 调用之前；
- “生图工具返回”发生在 ComfyUI 出图之后、配文生成之前；
- IndexTTS 的“合成结束”发生在 Gradio 调用返回之后，而不是 HTTP 连接建立/关闭时。

这些边界只有宿主插件知道。把它们做成 AstrBot/DSH 适配层，比让通用代理去猜更可靠。反过来说，如果你的场景不需要这些宿主语义，通用项目更合适——这也是上面表格存在的意义。

如果 berth 或 llama-swap 以后补上 AstrBot/DSH 适配，这个项目会直接在 README 里指过去；重复造轮子对谁都没好处。

---

## 已知边界（不藏着）

- 不做多卡；一张卡一个 profile。
- 不读 `nvidia-smi`；显存是否释放靠进程/端口/flag 推断。
- 不做 provider 自动切换；交给 AstrBot 的 fallback 链。
- TTS 必须实现 busy/ready 协议；没有适配器的 TTS 只能手动或换方案。
- Windows 优先；Linux 只有基础 `ps`/`kill` 支持。
- 不是“装完就自动识别一切”；`configure.py` 只负责生成初稿，路径和端口仍要人确认。
