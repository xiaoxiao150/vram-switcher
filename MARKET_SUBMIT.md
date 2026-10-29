# 市场上架清单

仓库已推送到 <https://github.com/xiaoxiao150/vram-switcher>，v2.0.5 Release 已含预构建 tarball。
下列两步需要仓库作者的 GitHub 账号在网页上完成，命令行 token 权限不足。

## 1. DSH 插件市场（awesome-dsh-plugin）—— 已收录

条目已于 2026-10-27 被 maintainer 直接合并到 `main`，原 PR #9555 已关闭，不用再推。
线上页面：<https://awesome-dsh-plugin.com/p/xiaoxiao150/vram-switcher/>

目录里的 tarball 当前仍指向 **v2.0.4**。要升到 v2.0.5，需要另开一个新 PR（不要重开已关闭的 #9555）或请 maintainer 更新；单行改动见
[`.market/xiaoxiao150__vram-switcher.yml`](.market/xiaoxiao150__vram-switcher.yml)。

> YAML 坑：英文描述里出现 ``: ``（冒号+空格）时整行必须加引号，否则目录解析失败。中文全角冒号不受影响。

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
