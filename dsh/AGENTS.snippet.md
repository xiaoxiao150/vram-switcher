## 本地 GPU 任务协调（VRAM Switcher 原生插件）

当你要在这台机器上运行会占用显存的本地任务（ComfyUI/生图、IndexTTS 或其它
TTS webui）时：

1. 先调用 `vram_status` 确认当前状态。
2. 调用 `vram_acquire`：
   - 生图：`mode: "image"`
   - 语音：`mode: "tts"`
   - 长任务可以传 `lease_seconds`（最多 7200）。
3. 运行本地任务。
4. 无论成功还是失败，最后都调用 `vram_release`。

注意：

- 如果当前这轮 DSH 正在使用 `local-8080` / `local-auto` 等本地 LLM，**不要**
  调用 `vram_acquire`：它会停掉正在给你生成回复的本地模型。
- 不要并行跑 AstrBot 和 DSH 的本地 GPU 任务；共享锁只能避免切换打架。
- 这些工具只做显存协调，不负责替你执行生图/语音命令。
