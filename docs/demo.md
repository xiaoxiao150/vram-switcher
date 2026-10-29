# 一次完整的显存交接（实测记录）

这不是概念图，是从生产日志里摘出来的时序。机器：12 GB 笔记本 N 卡，Windows；本地 LLM 常驻，IndexTTS 和 ComfyUI 按需启停。

## 语音：LLM → IndexTTS → LLM

```text
用户发一条带语音标记的消息
  │
  ├─ TTS 插件写 tts_busy.flag
  ├─ VRAMSwitcher 停本地 LLM（llama-server 退出，显存释放）
  ├─ 确保 IndexTTS webui 在线（没起就冷启动，等 7860 listen）
  ├─ 写 tts_ready.flag
  ├─ TTS 插件看到 ready + 端口在线，提交 /gen_single
  ├─ 合成结束，删 busy flag
  └─ VRAMSwitcher 恢复 LLM（夜间静默时停在“不拉起”）
```

日志里的关键行（服务名已保留，聊天内容已省略）：

```text
[VRAMSwitcher] 检测到自动 TTS 合成，准备释放显存
[VRAMSwitcher] 停止本地 LLM 释放显存
[VRAMSwitcher] LLM 已停，显存已释放
[VRAMSwitcher] TTS 窗口：补拉语音服务
[VRAMSwitcher] tts: 已启动 ...\IndexTTS2.5\.venv\Scripts\python.exe
[VRAMSwitcher] 自动 TTS 合成完成，恢复本地 LLM
[VRAMSwitcher] 本地 LLM 已恢复
```

## 生图：LLM → ComfyUI → LLM

```text
LLM 决定调用生图工具
  │
  ├─ VRAMSwitcher 停 LLM（可选停 TTS / PE）
  ├─ 启动 ComfyUI，等 8188 listen（不是等进程存在）
  ├─ 工具提交工作流
  ├─ 出图完成，等 ComfyUI /queue 跑空
  ├─ 停 ComfyUI
  └─ 恢复 LLM（如果配置了 keep_tts_resident，再补拉 TTS）
```

## 为什么必须等“端口/队列”，而不是“进程刚起”

在 12 GB 卡上，显存不够时 Windows 不会报 OOM，而是把显存换页到内存。实测代价：

| 场景 | 正常 | 换页时 |
|---|---|---|
| `s2mel` 扩散 | 0.12 s/步 | 30 s/步 |
| 一张图 | 几十秒 | 543 秒 |
| LLM 预填 | ~1300 tok/s | 0.2 tok/s |

进程一直活着，所以“看进程还在”不能证明服务可用；必须等端口 listen，出图后还要等队列跑空。

## 自己复现

1. `python configure.py --root D:/AI` 生成 profile（或在 `profile.example.json` 上手填）。
2. 指向 AstrBot/DSH，重启宿主。
3. 发一条带语音标记的消息，或让 Bot 调一次生图工具。
4. 看日志里的 `[VRAMSwitcher]` 前缀，以及 8080 / 7860 / 8188 的监听变化。

夜间（默认 23:00–07:00）只会停、不会拉起 TTS 和本地 LLM；这是刻意的静默规则，不是故障。

更完整的 IndexTTS 协议和排障见 [`indextts.md`](indextts.md)。
