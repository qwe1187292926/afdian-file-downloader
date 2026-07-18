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
- 默认对创作者投稿做增量扫描，并定期执行全量补扫。
- 使用“创作者名称 / 发布日期-帖子标题”的可读目录；仅在已确认帖子冲突或既有非空目录无法安全认领时，才追加短帖子标识。
- 使用帖子标题或附件名称生成可读文件名，并通过 `.afdian-post.json` 记录帖子和文件映射。
- 使用 `download_state.json` v2 稳定资源标识去重，并兼容迁移旧版状态。
- 状态文件缺失时也会通过帖子 sidecar 和预期文件名检查既有文件，尽量避免重复生成 `-1` 文件。
- 下载完成后输出平均速度。
- 支持 Bark 通知，并在通知正文中包含本次下载标题。
- 创作者有新投稿但当前账号无权限查看时，会发送一次无法下载提醒，并记录状态避免重复提醒。

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
  "incremental_scan": true,
  "incremental_lookback_posts": 20,
  "incremental_full_scan_days": 30,
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
| `dry_run` | 预览模式，不下载内容，也不推进增量检查点或清除无权限状态。运行时状态文件和 manifest 仍可能被创建或记录。 |
| `timeout` | 单个下载请求的超时秒数，默认 `60`。 |
| `per_page` | 创作者投稿接口每页请求数量，默认 `10`。 |
| `max_posts` | 单次最多处理的投稿数；`0` 表示不限。非零值属于手动扫描边界，会停用自动增量检查点。 |
| `since` / `until` | 全局日期边界，支持 `YYYY-MM-DD` 或 `YYYYMMDD`。 |
| `stop_post_id` | 扫描遇到此帖子 ID 时停止；空字符串表示不设置。非空值会停用自动增量检查点。 |
| `incremental_scan` | 是否启用创作者投稿自动增量扫描，必须是 JSON 布尔值，默认 `true`。 |
| `incremental_lookback_posts` | 增量扫描遇到已知检查点后，继续回看的非置顶投稿数，默认 `20`，负数按 `0` 处理。 |
| `incremental_full_scan_days` | 距离上次全量扫描多少天后再做一次全量补扫，默认 `30`；设为 `0` 可关闭周期全量补扫，但首次运行仍会全量扫描。 |
| `creators` | 创作者主页列表，用于批量下载。 |
| `single_posts` | 单帖 URL 列表，用于查漏补缺。 |
| `bark` | Bark 推送配置。 |

`creators` 中的字段会覆盖同名全局字段，因此可以为不同创作者分别设置日期边界或扫描策略。

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
[config] options={..., "incremental_scan": true, "incremental_lookback_posts": 20, "incremental_full_scan_days": 30}
[incremental] mode=full|incremental, lookback_posts=20, full_scan_days=30
```

输出目录示例：

```text
downloads/
  创作者名称/
    2026-07-18-帖子标题/
      .afdian-post.json
      帖子标题.mp4
  download_state.json
  manifest.jsonl
```

普通目录保持修改前的可读格式：创作者目录使用创作者名称，帖子目录使用发布日期和帖子标题；创作者批量入口沿用 feed 发布时间，单帖入口沿用 detail 返回的发布时间。稳定 post/asset 身份保存在 `download_state.json` 与 `.afdian-post.json` 内部，不再暴露为日常目录名。若已有 sidecar 明确证明同名目录属于另一篇帖子，或既有非空目录无法安全认领，当前帖子才会使用 `日期-标题--短帖子标识`。每个新认领的帖子目录中的 sidecar 保存完整 ID、当前标题、链接、发布时间和资源到相对文件名的映射。

本版本不会自动移动短暂使用 ID-only 布局的 `creator-.../post-...` 目录。确认其中是重复文件后可直接删除；对应 state 路径失效时，脚本会回退检查修改前的可读目录，并把 v2 state 刷新到已存在的旧文件，不会再次下载。

## 增量扫描

`incremental_scan` 默认为 `true`。没有可用检查点时（通常是第一次运行），脚本会扫描该创作者的全部可见投稿并保存最近的非置顶帖子 ID；后续运行从最新一页开始，在遇到任一已知检查点后，再回看 `incremental_lookback_posts` 条非置顶投稿，然后停止。置顶投稿会正常处理，但不会建立检查点，也不会消耗回看数量。

默认每 `incremental_full_scan_days=30` 天执行一次全量补扫。将该值设为 `0` 只会关闭周期补扫；首次扫描仍是全量。切换 `include_images` 或检测到旧版资源标识检查点时，也会自动做一次全量扫描。

以下任一情况会停用自动增量检查点，并按当前配置执行普通扫描：

- 设置了 `since`、`until`、非零 `max_posts` 或 `stop_post_id`；
- `skip_existing=false`；
- `overwrite=true`；
- `incremental_scan=false`。

保存在 `inaccessible_posts` 中的无权限帖子会在增量窗口之外单独重试，因此旧帖子在账号权限恢复后仍能被发现。只有确认帖子无可下载文件，或所有候选均已下载/已存在后，才会清除该记录；失败、跳过或预览运行都会保留它。

检查点只会在扫描安全结束、manifest 写入成功且本次 feed 投稿没有详情失败、下载失败或跳过后推进。`dry_run` 可以读取现有检查点来缩小扫描范围，但不会推进检查点。

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

`download_state.json` 用于记录已下载文件、创作者增量扫描检查点和已提醒过的无权限投稿。脚本启动时会创建该文件，并在每个文件下载成功、被识别为本地已存在，或发现无权限投稿后立即保存。

当前 v2 资源键会进行 SHA-256 哈希，其原始标识由以下稳定信息组成：

```text
post_id + （可用时的资源定位符） + 规范化下载 URL
```

帖子标题和可变的文件名提示不参与资源标识。URL 规范化会移除 fragment，并且只在识别到一组完整签名参数时移除对应的 AWS、Google Cloud、腾讯 COS、阿里 OSS 或 CloudFront 签名字段。对于爱发电视频域名 `vod.afdiancdn.com`，只有 `sign`、`t`、`us` 三个临时鉴权参数完整同时出现时才会一起移除；其他域名、缺少任一参数的组合以及所有额外功能参数仍会保留，避免把实际不同的资源错误合并。

旧版 v1/v0 状态会在再次命中时惰性迁移。为了保证修改前已经下载的内容不被重新下载，当前候选中唯一且精确命中的旧 v1/v0 key 会优先复用原可读目录文件，即使当前 URL 含有旧算法无法区分的 query；两个候选共享同一旧 key 时仍拒绝复用。只有没有精确旧 key 时，URL-only 模糊迁移才要求规范化 URL 不含遗留功能性 query。记录指向的文件必须仍然存在，且同一物理路径不能被不同帖子或资源定位符重复认领。已经由旧版动态 `sign/t/us` 生成的错误 v2 key 也会按“同一帖子、同一非空资源定位符、按新规则规范化后 URL 完全一致”迁移到稳定 key；存在多个重复文件记录时优先复用严格预期的基础文件，不自动移动或删除任何文件。

如果状态文件丢失，脚本会先使用可读帖子目录中的 `.afdian-post.json` 查找精确相对文件名；旧目录没有 sidecar 时，则只检查目录直下的严格预期文件名，并在命中后补写 v2 state 和 sidecar，不移动或重命名旧文件。对于同一个帖子里的多个同名文件，会按候选顺序对应：

```text
第 1 个文件 -> 标题.mp4
第 2 个文件 -> 标题-1.mp4
第 3 个文件 -> 标题-2.mp4
```

同时兼容修改前可能生成的 `archive.zip.zip`、`archive.zip-1.zip` 等重复扩展名。sidecar 会校验创作者 ID 和帖子 ID，并且只接受帖子目录内的单层相对文件名。非空且没有 sidecar 的目录只有在旧 state 指向其中的当前帖子文件，或至少一个严格预期文件名存在时才会被认领；否则保留原目录，并把新下载分流到带短帖子标识的可读冲突目录。

如果某篇创作者投稿因为当前账号付费等级不足而无法查看，脚本会把它记录到状态文件的 `inaccessible_posts` 中。后续检查仍然无权限时不会重复发送提醒；如果之后提升了付费等级并能访问该投稿，脚本会清除这条无权限状态并正常进入下载流程。

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

当创作者有新投稿但当前账号无权限下载时，也会发送一次提醒。提醒正文会包含帖子标题，并尽量从接口字段中提取需要的付费类型或档位；如果接口没有返回这类字段，会显示“接口未返回具体付费类型”。同一篇无权限投稿在状态文件里标记为已通知后，后续运行不会重复提醒。

## 运行产物

| 文件 | 说明 |
| --- | --- |
| `download_state.json` | v2 去重状态、创作者增量检查点及已提醒过的无权限投稿。 |
| `manifest.jsonl` | 每个成功、跳过、失败记录的明细日志。 |
| `创作者名称/YYYY-MM-DD-帖子标题/.afdian-post.json` | 帖子元数据及 v2 资源键到本地相对文件名的映射，用于安全的文件系统兜底。 |
| `*.part` | 下载中的临时文件，正常完成后会被替换成最终文件。 |

## 可选浏览器模式

项目仍保留 `afdian_downloader.py login` 和网页解析兜底模式，但服务器日常下载不需要它们。

只有需要浏览器登录或旧 CLI 网页解析时，才安装：

```powershell
pip install -r requirements-browser.txt
python -m playwright install chromium
```

## 开发校验

修改代码后可运行内置回归测试和语法校验：

```powershell
python -m unittest discover -s tests -v
python -m py_compile .\afdian_downloader.py .\afdian_config_common.py .\download_creators.py .\download_post.py
```

## 注意事项

- 不要提交 `config.json`、`cookies.txt`、`download_state.json`、`manifest.jsonl` 或下载文件。
- Cookie 失效后需要重新复制。
- 默认增量模式在首次运行时遍历全部可见投稿，之后从最新投稿扫描到检查点并回看；关闭增量后才会在无手动边界时每次遍历全部可见投稿。
- 设置任一手动边界会停用自动增量检查点，避免有限范围的扫描错误推进全局检查点。
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
