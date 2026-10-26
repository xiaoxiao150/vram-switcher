# 市场上架清单

仓库已推送到 <https://github.com/xiaoxiao150/vram-switcher>，v2.0.4 Release 已含预构建 tarball。
下列两步需要仓库作者的 GitHub 账号在网页上完成，命令行 token 权限不足。

## 1. DSH 插件市场（awesome-dsh-plugin）

1. 给仓库加 topic `dsh-plugin`：仓库 → About 右侧齿轮 → Topics。
2. 提交 PR，新增一个文件 `data/plugins/xiaoxiao150__vram-switcher.yml`：

直接点这个预填链接即可（内容已填好）：

<https://github.com/awesome-dsh-plugin/awesome-dsh-plugin/new/main?filename=data%2Fplugins%2Fxiaoxiao150__vram-switcher.yml>

文件内容见 [`.market/xiaoxiao150__vram-switcher.yml`](.market/xiaoxiao150__vram-switcher.yml)：

```yaml
url: https://github.com/xiaoxiao150/vram-switcher
name: xiaoxiao150/vram-switcher
category: tools
description:
  en: AstrBot/DSH-native GPU coordination for local AI companions: LLM, IndexTTS and ComfyUI take turns, no proxy.
  zh: 面向本地 AI 陪伴的 AstrBot/DSH 原生显存协调：LLM、IndexTTS、ComfyUI 轮流用卡，不加代理。
tarball: https://github.com/xiaoxiao150/vram-switcher/releases/download/v2.0.4/dsh-vram-switcher-2.0.4.tgz
```

合并后站点与 DSH 市场通常一天内自动收录。

> 注意：目录要求仓库创建满 1 天（本仓库创建于 2026-10-26），如果 CI 因仓库年龄拦下，隔天再提交一次即可。

## 2. AstrBot 插件市场（AstrBot Cloud）

1. 打开 <https://cloud.astrbot.app/publish>
2. 用 GitHub 账号登录并授权 AstrBot Cloud 读取你的仓库
3. 选择 `xiaoxiao150/vram-switcher`，它会自动读取根目录的 `metadata.yaml`
4. 确认信息后发布

`metadata.yaml` 已包含 `name / display_name / desc / version / author / repo`，
仓库体积约 230 KB，远低于 16 MB 上限。

## 备选：npm

发布到 npm 后可让 DSH 市场显示下载量（收录本身不依赖 npm）：

```bash
npm publish --access public
```

需要先把 `package.json` 的 `dsh-vram-switcher` 改成你的 npm scope（若名称被占用）。
