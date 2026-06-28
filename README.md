# Afdian / Ifdian File Downloader

<div align="center">

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![Dependency](https://img.shields.io/badge/Dependency-requests-green.svg)
![Platform](https://img.shields.io/badge/Platform-Qinglong%20%7C%20Server%20%7C%20Local-orange.svg)
![License](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-lightgrey.svg)
![Last Commit](https://img.shields.io/github/last-commit/qwe1187292926/afdian-file-downloader.svg)
![Issues](https://img.shields.io/github/issues/qwe1187292926/afdian-file-downloader.svg)
![Stars](https://img.shields.io/github/stars/qwe1187292926/afdian-file-downloader.svg?style=social)

**爱发电 / Ifdian 赞助内容下载工具**

[![中文](https://img.shields.io/badge/README-中文-red.svg)](README.md)
[![English](https://img.shields.io/badge/README-English-blue.svg)](README.en.md)
[![日本語](https://img.shields.io/badge/README-日本語-green.svg)](README.ja.md)

</div>

---

### 如果这个项目对你有帮助，欢迎点一个 Star。

---

## 项目简介

`afdian-file-downloader` 是一个面向爱发电 / Ifdian 用户的文件下载工具。它使用你自己账号的 Cookie 访问创作者投稿和单帖详情，下载当前账号有权限访问的视频、音频和附件。

它适合这些场景：

- 定期下载某个创作者在指定日期之后发布的所有可访问文件。
- 对单个帖子进行查漏补缺下载。
- 在服务器、青龙面板或计划任务中无浏览器运行。
- 使用 Bark 在新增下载完成后推送通知。

## 能力边界

本工具不会绕过付费墙、验证码、权限校验或 DRM。它只会下载当前登录账号在网页端已经有权限访问到的文件链接。若账号没有赞助权限、Cookie 失效、平台接口变化，下载会失败或跳过。

## 功能特性

- Cookie 配置运行，服务器端不需要 Playwright。
- 支持按创作者主页批量下载投稿。
- 支持按单帖 URL 下载。
- 支持 `since` / `until` 日期边界。
- 使用帖子标题命名文件和目录。
- 使用 `download_state.json` 去重，重复运行不会反复下载。
- 状态文件缺失时也会检查目标目录中的既有文件，尽量避免重复生成 `-1` 文件。
- 下载完成后输出平均速度。
- 支持 Bark 通知，并在通知正文中包含本次下载标题。

## 快速开始

### 1. 安装依赖

Windows PowerShell:

```powershell
cd C:\Users\Hoyoung\IdeaProjects\afdian-file-downloader
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Linux / macOS:

```bash
cd afdian-file-downloader
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

服务器下载只需要 `requirements.txt`。如果只使用 `download_creators.py` 和 `download_post.py`，不需要安装 Playwright。

### 2. 创建配置文件

```powershell
Copy-Item .\config.example.json .\config.json
```

Linux / macOS:

```bash
cp config.example.json config.json
```

### 3. 准备 Cookie

推荐从浏览器开发者工具复制 Cookie 请求头：

1. 在浏览器登录 [ifdian.net](https://ifdian.net/)。
2. 打开开发者工具，进入 Network。
3. 刷新创作者主页或帖子页。
4. 找到发往 `ifdian.net` 的请求。
5. 复制请求头里的 `Cookie` 值。
6. 保存到 `cookies.txt`。

格式示例：

```text
cookie_a=value_a; cookie_b=value_b; cookie_c=value_c
```

也可以直接填入 `config.json` 的 `cookie` 字段。为了避免泄露，推荐使用 `cookie_file`，并保持 `cookies.txt` 不提交。

## 配置说明

`config.json` 示例：

```json
{
  "cookie": "",
  "cookie_file": "cookies.txt",
  "download_dir": "downloads",
  "state_file": "download_state.json",
  "include_images": false,
  "overwrite": false,
  "skip_existing": true,
  "dry_run": false,
  "timeout": 60,
  "per_page": 10,
  "max_posts": 0,
  "since": "",
  "until": "",
  "stop_post_id": "",
  "creators": [
    {
      "url": "https://ifdian.net/a/creator?tab=feed",
      "since": "2026-06-26",
      "until": "",
      "max_posts": 0,
      "stop_post_id": ""
    }
  ],
  "single_posts": [
    "https://www.ifdian.net/p/post_id"
  ],
  "bark": {
    "enabled": false,
    "server": "https://api.day.app",
    "device_key": "",
    "group": "Ifdian Downloader",
    "sound": "",
    "icon_creator_avatar": true
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `cookie` | 直接填写浏览器请求头里的 Cookie。 |
| `cookie_file` | Cookie 文件路径，推荐保存为 `cookies.txt`。 |
| `download_dir` | 下载输出目录。 |
| `state_file` | 下载状态文件。只写文件名时会放在 `download_dir` 下，例如 `downloads/download_state.json`。 |
| `include_images` | 是否下载图片资源。默认 `false`，避免头像、封面等噪音。 |
| `overwrite` | 是否覆盖已存在文件。默认 `false`。 |
| `skip_existing` | 是否跳过已下载文件。默认 `true`。 |
| `dry_run` | 预览模式，只检查可下载项，不写文件。 |
| `since` / `until` | 全局日期边界，支持 `YYYY-MM-DD` 或 `YYYYMMDD`。 |
| `creators` | 创作者主页列表，用于批量下载。 |
| `single_posts` | 单帖 URL 列表，用于查漏补缺。 |
| `bark` | Bark 推送配置。 |

## 下载创作者投稿

配置好 `creators` 后运行：

```powershell
python .\download_creators.py
```

Linux / macOS:

```bash
python download_creators.py
```

启动时会打印关键参数：

```text
[config] config_dir=...
[creators] configured=1
[creator-config] #1 url=https://ifdian.net/a/creator?tab=feed
[config] download_dir=...
[config] state_file=...
[config] manifest=...
[config] options={...}
```

输出目录示例：

```text
downloads/
  创作者名称/
    2026-06-27-帖子标题/
      帖子标题.mp4
  download_state.json
  manifest.jsonl
```

## 下载单个帖子

推荐直接通过启动参数传入帖子 URL：

```powershell
python .\download_post.py "https://www.ifdian.net/p/post_id"
```

也可以把 URL 写进 `config.json` 的 `single_posts` 后运行：

```powershell
python .\download_post.py
```

## 去重机制

`download_state.json` 用于记录已下载文件。脚本启动时会创建该文件，并在每个文件下载成功或被识别为本地已存在后立即保存。

正常去重使用：

```text
post_id + 下载 URL + 文件名提示
```

如果状态文件丢失，脚本会用目标目录中的文件兜底判断。对于同一个帖子里的多个同名文件，会按候选顺序对应：

```text
第 1 个文件 -> 标题.mp4
第 2 个文件 -> 标题-1.mp4
第 3 个文件 -> 标题-2.mp4
```

## Bark 通知

打开 `config.json` 中的 Bark：

```json
{
  "bark": {
    "enabled": true,
    "server": "https://api.day.app",
    "device_key": "你的 Bark Device Key",
    "group": "Ifdian Downloader",
    "sound": "",
    "icon_creator_avatar": true
  }
}
```

当本次运行有新增下载时，脚本会发送通知。通知正文会包含本次下载的视频或附件标题；标题过多时会自动截断。

## 运行产物

| 文件 | 说明 |
| --- | --- |
| `download_state.json` | 去重状态文件。 |
| `manifest.jsonl` | 每个成功、跳过、失败记录的明细日志。 |
| `*.part` | 下载中的临时文件，正常完成后会被替换成最终文件。 |

## 可选浏览器模式

项目仍保留 `afdian_downloader.py login` 和网页解析兜底模式，但服务器日常下载不需要它们。

只有需要浏览器登录或旧 CLI 网页解析时，才安装：

```powershell
pip install -r requirements-browser.txt
python -m playwright install chromium
```

## 注意事项

- 不要提交 `config.json`、`cookies.txt`、`download_state.json`、`manifest.jsonl` 或下载文件。
- Cookie 失效后需要重新复制。
- 不设置 `since`、`until`、`max_posts` 或 `stop_post_id` 时，会尝试遍历账号可见的全部投稿。
- 平台接口变化时，可能需要更新脚本。

## 作者

Hoyoung

## 许可

本项目源代码公开，但仅允许非商业用途使用、复制、修改和分发。商业使用需要另行获得作者授权。

许可证：PolyForm Noncommercial License 1.0.0，详见 [LICENSE](LICENSE)。

---

<div align="center">

**如果这个项目对你有帮助，欢迎点一个 Star。**

Made by Hoyoung

</div>
