# IndexTTS 集成指南

这份文档写给两类人：

- 在 **AstrBot / DSH 上做本地 AI 陪伴**，用 IndexTTS 发语音，同时用 ComfyUI 自拍/改图；
- 想给别的 TTS（或别的“没有 unload API”的服务）接同一套显存调度协议。

核心结论只有一句：**IndexTTS webui 没有卸载模型的 HTTP API，所以只能停进程；而停进程的时机必须由 TTS 侧告诉调度器，不能由调度器猜。**

---

## 1. 为什么陪伴场景需要它

一次典型的陪伴回合可能同时发生：

```text
用户发消息
  ├─ 她回复文字（本地 LLM 占 4.9 GB）
  ├─ 回复带 [语音] → IndexTTS 要 2.3–5.3 GB
  └─ 她调用 generate_selfie → ComfyUI 要 6–7 GB
```

12 GB 卡上，LLM + IndexTTS + 桌面已经接近上限；再叠 ComfyUI 就会触发 Windows 显存换页。实测后果：

- `s2mel` 扩散从 **0.12 s/步** 变成 **30 s/步**；
- 一张图从几十秒变成 **543 秒**；
- 本地 LLM 预填从 ~1300 tok/s 掉到 **0.2 tok/s**。

进程不会崩，只会慢到不可用。所以调度器需要知道两件事：

1. **什么时候**可以把 LLM 停掉（TTS 真的要开始合成了）；
2. **什么时候**合成真的结束了（可以恢复 LLM）。

通用 GPU 租约系统通常只知道“HTTP 请求开始/结束”，但 IndexTTS 的合成是 Gradio 调用，冷启动和模型加载都在请求内部；HTTP 返回不代表显存已经释放。这个项目用两个文件把边界显式化。

---

## 2. 文件协议

| 文件 | 谁写 | 含义 |
|---|---|---|
| `tts_busy.flag` | TTS 插件 | 合成窗口打开。调度器看到后停 LLM、准备 TTS。 |
| `tts_ready.flag` | 调度器 | LLM 已停、TTS 端口已 listen，可以安全提交合成。 |

约定：

- TTS 插件在**提交合成之前**写 `busy`，并先删掉上一轮残留的 `ready`。
- 调度器轮询到 `busy` 后：停 LLM（和 `aux_services`）→ 确保 TTS 在线 → 写 `ready`。
- TTS 插件必须等 `ready` **且** webui 端口可连接，才提交 `/gen_single`。
- 合成结束（成功或失败）都要在 `finally` 里删 `busy`。
- `busy` 超过 `flag_stale_seconds`（默认 900 秒）没更新，调度器会当成崩溃残留清掉。
- 电话等长会话期间，每 1.5 秒 touch 一次 `busy`，避免被误判为残留。

> 两个 flag 只存时间戳，不包含文本、音频或任何隐私内容。

---

## 3. TTS 侧最小适配器

把下面这段接进你的 TTS 插件（AstrBot 插件、独立脚本都行）。路径必须和 `profile.local.json` 里的 `busy_flag` / `ready_flag` 完全一致。

```python
import asyncio
import os
import socket
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get(
    "VRAM_DATA_DIR",
    r"AstrBot/data/plugin_data/astrbot_plugin_vram_switcher",
))
BUSY = Path(os.environ.get("VRAM_TTS_BUSY_FLAG", DATA_DIR / "tts_busy.flag"))
READY = Path(os.environ.get("VRAM_TTS_READY_FLAG", DATA_DIR / "tts_ready.flag"))
TTS_PORT = int(os.environ.get("VRAM_TTS_PORT", "7860"))


def port_open(port: int = TTS_PORT, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def begin_synthesis() -> None:
    """在提交 /gen_single 之前调用。"""
    READY.unlink(missing_ok=True)          # 清掉上一轮的 ready
    BUSY.parent.mkdir(parents=True, exist_ok=True)
    BUSY.write_text(str(time.time()), encoding="utf-8")


def touch_busy() -> None:
    """长会话保活；每 1.5 秒调用一次。"""
    if BUSY.exists():
        os.utime(BUSY, None)


def end_synthesis() -> None:
    """放在 finally 里，失败也要删。"""
    BUSY.unlink(missing_ok=True)


async def wait_for_vram(timeout: float = 150.0) -> bool:
    """等调度器停完 LLM 并拉起 webui。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if READY.exists() and port_open():
            return True
        await asyncio.sleep(1.0)
    return False


async def synthesize(...):
    begin_synthesis()
    try:
        if not await wait_for_vram():
            raise TimeoutError("VRAM 未在超时前就绪")
        return await call_indextts(...)   # 你的 /gen_single 调用
    finally:
        end_synthesis()
```

电话/长时间通话场景：

```python
async def keep_busy_until(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        touch_busy()
        await asyncio.sleep(1.5)
```

常见错误：**先等 ready 再写 busy**。这样调度器根本不知道要停 LLM，会一直等到超时。

---

## 4. 调度器侧做了什么

`vram_switcher.Orchestrator.tts_watch()` 每 `tts_poll_seconds`（默认 2 秒）检查一次：

```text
busy 出现
  → 停 LLM
  → 停 aux_services（如提示词优化器）
  → 如果 TTS 没在跑：按 versions 启动，等端口 listen
  → 写 ready
  → 进入 engaged 状态

busy 消失
  → 删 ready
  → 如果图像服务在跑：先等 ComfyUI /queue 跑空，再停它
  → 按 keep_indextts_resident 决定 TTS 是保留还是停掉
  → 拉起 LLM，等 recovery_wait_seconds（默认 45 秒）
```

**夜间静默**（默认 23:00–07:00）：调度器不会启动 TTS。如果合成已经在跑，允许它结束；结束后看门狗会停掉 webui。这样 23:00 后自动 TTS 不会再把 IndexTTS 拉起来。

相关配置：

| 配置 | 作用 |
|---|---|
| `busy_flag` / `ready_flag` | 两个文件的绝对路径 |
| `flag_stale_seconds` | busy 残留清理阈值，默认 900 |
| `start_cooldown_seconds` | 防止反复重启 TTS，默认 150 |
| `version_selector` | `auto` / `2.5` / `2.0` |
| `default_version` | 停着时启动哪个版本 |
| `recovery_wait_seconds` | LLM 启动后等多久再判定恢复 |
| `keep_indextts_resident` | 语音结束后是否保留 TTS 常驻 |

---

## 5. Profile：IndexTTS 2.5 示例

```json
{
  "services": {
    "tts": {
      "enabled": true,
      "port": 7860,
      "host": "127.0.0.1",
      "process_names": ["python.exe"],
      "cmdline_contains": ["webui.py"],
      "cmdline_any": ["IndexTTS", "Index-TTS"],
      "stop_extra": [
        {"process_names": ["cmd.exe"], "cmdline_any": ["IndexTTS"]}
      ],
      "stop": {"timeout_seconds": 20, "tree": false},
      "busy_flag": "${DATA_DIR}/tts_busy.flag",
      "ready_flag": "${DATA_DIR}/tts_ready.flag",
      "flag_stale_seconds": 900,
      "start_cooldown_seconds": 150,
      "version_selector": "auto",
      "default_version": "2.5",
      "detect": {
        "2.5": ["IndexTTS2.5", "--version 2.5"],
        "2.0": ["Index-TTS"]
      },
      "versions": {
        "2.5": {
          "args": [
            "D:/AI/IndexTTS2.5/.venv/Scripts/python.exe",
            "D:/AI/IndexTTS2.5/webui.py",
            "--version", "2.5",
            "--fp16"
          ],
          "cwd": "D:/AI/IndexTTS2.5",
          "log_file": "${DATA_DIR}/tts.log"
        },
        "2.0": {
          "args": [
            "D:/AI/IndexTTS2/.venv/Scripts/python.exe",
            "D:/AI/IndexTTS2/webui.py",
            "--fp16"
          ],
          "cwd": "D:/AI/IndexTTS2",
          "log_file": "${DATA_DIR}/tts.log"
        }
      }
    }
  }
}
```

**进程匹配必须收窄**。只写 `process_names: ["python.exe"]` 会误杀其它 Python（提示词优化器、下载器、别的插件）。`cmdline_contains` + `cmdline_any` 至少给一个；更稳的做法是给 IndexTTS 单独的 venv/解释器副本。

---

## 6. AstrBot 钩子与优先级

AstrBot 适配层的触发点：

| 钩子 | priority | 动作 |
|---|---|---|
| `on_llm_request` | 999999 | 消息命中语音关键词 / 生图前缀 → 提前准备 |
| `on_decorating_result` | 999999 | 回复里出现 `[语音]` / `【语音】` → 提前准备 TTS |
| `on_using_llm_tool` | 100000 | LLM 要调用生图工具 → 停 LLM、起 ComfyUI |
| `on_llm_tool_respond` | 999999 | 生图工具返回 → 提前恢复，让配文继续由本地 LLM 生成 |
| `on_decorating_result` | -999999 | 整条消息处理完 → 统一恢复 |

优先级数字越大越先执行。生产环境里，提示词优化器用 `200000`，比生图停机的 `100000` 先跑，避免优化请求撞上“LLM 已停”。如果你也装了类似插件，给它比 `100000` 更高的优先级。

---

## 7. 2.0 / 2.5 双版本

- `version_selector: "auto"`：从正在运行的进程命令行里匹配 `detect` 表；匹配不到就用 `default_version`。
- 显式写 `"2.5"` / `"2.0"`：始终用该版本。
- 两个版本共用同一个端口时，停掉一个再起另一个；`start_cooldown_seconds` 防止 AstrBot 看门狗和调度器互相拉起。
- 如果两个 webui 同时监听不同端口，建议拆成两个服务或改端口，不要让 `stop_by_port` 误伤。

---

## 8. 排障

| 现象 | 先查 |
|---|---|
| TTS 一直等 ready，最后超时 | profile 的 `port` 是否是 webui 实际端口；TTS 是否 `enabled: false`；`ready` 路径是否和插件一致 |
| 合成还是 30 s/步 | `ready` 是否在 LLM 真停之前就写了；LLM 进程是否真的退出；ComfyUI 是否还驻留 |
| 语音结束后 LLM 没回来 | 夜间静默（23:00–07:00）是否生效；`recovery_wait_seconds`；LLM 启动失败日志 |
| busy flag 永远不消失 | TTS 进程崩溃；等 900 秒自动清理，或确认没有合成后手动删除 |
| 杀错 Python | `cmdline_contains` / `cmdline_any` 太宽；给 IndexTTS 独立解释器 |
| 生图和语音同时来 | 图像任务优先；语音信号会等图像结束，属于设计行为 |
| 电话中途 webui 被杀 | 通话期间是否每 1.5 秒 touch `busy`；`flag_stale_seconds` 是否设得太短 |

调试入口：

- AstrBot 日志里的 `[VRAMSwitcher]` 前缀；
- `plugin_data/astrbot_plugin_vram_switcher/tts_busy.flag` 和 `tts_ready.flag` 的修改时间；
- `netstat -ano | findstr :7860`（Windows）确认端口和 PID；
- DSH 里直接调 `vram_status` 看 `tts_busy` / `tts_engaged`。

---

## 9. 接入检查清单

- [ ] profile 里 `services.tts.enabled = true`，`port` 和 webui 一致
- [ ] 调度器与 TTS 插件使用**同一组** `busy_flag` / `ready_flag` 路径
- [ ] TTS 在提交合成**之前**写 busy，并先删旧 ready
- [ ] TTS 等 `ready && port_open()` 才提交
- [ ] 合成结束在 `finally` 里删 busy
- [ ] 冷启动等待时间 ≥ 150 秒
- [ ] 进程过滤同时有进程名 + 命令行关键词
- [ ] 电话场景有 1.5 秒保活
- [ ] 夜间静默是否符合预期

---

## 10. 边界

- 这个协议不关心 TTS 是 IndexTTS 2.0、2.5 还是别的 Gradio webui；只要它能写/删文件、能等端口，就能接。
- 它不修改 IndexTTS 本身，也不读取音频内容。
- 它不能替代 TTS 自己的错误处理；合成失败时仍要删 busy，否则要等 900 秒清理。
- 如果你的 TTS 有真正的 unload API，优先用 API（更快、无需重启进程）；文件协议是给“只能停进程”的服务的。
- Windows + AMD 显卡上，IndexTTS 的 PyTorch/ROCm 后端可能本身就是门槛；先单独跑通 webui，再谈调度。见 [`gpu-support.md`](gpu-support.md)。

## 11. 不用 AstrBot / DSH 也可以

核心包 `vram_switcher` 不依赖宿主。你自己的 AI 陪伴服务可以直接用 `VramService`：

```python
from vram_switcher import load_config
from vram_switcher.service import VramService

cfg = load_config(
    {"profile_path": r"D:/vram/profile.local.json"},
    plugin_dir=r"D:/vram",
    data_dir=r"D:/vram/data",
)
service = VramService(cfg)
await service.start()

await service.acquire("image", reason="selfie")   # 停 LLM、起 ComfyUI
# ... 出图 ...
await service.release("selfie")                   # 停 ComfyUI、恢复 LLM
```

语音不要用 `acquire("tts")` 包住整段合成再 `release`：`acquire` 只知道“我现在要卡”，不知道合成何时真正结束。TTS 仍用本文的 busy/ready 协议，调度器会在 `busy` 消失时自动恢复。

---

## 附：相关文件

- 调度侧：`vram_switcher/orchestrator.py`（`tts_watch` / `prepare` / `recover`）
- 配置：`vram_switcher/config.py`
- AstrBot 适配：`main.py`
- DSH 原生工具：`dsh-plugin/index.js` + `dsh-plugin/daemon.py`
- 对照：`docs/comparison.md`

