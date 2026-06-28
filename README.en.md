# Afdian / Ifdian File Downloader

<div align="center">

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![Dependency](https://img.shields.io/badge/Dependency-requests-green.svg)
![Platform](https://img.shields.io/badge/Platform-Qinglong%20%7C%20Server%20%7C%20Local-orange.svg)
![License](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-lightgrey.svg)
![Last Commit](https://img.shields.io/github/last-commit/qwe1187292926/afdian-file-downloader.svg)
![Issues](https://img.shields.io/github/issues/qwe1187292926/afdian-file-downloader.svg)
![Stars](https://img.shields.io/github/stars/qwe1187292926/afdian-file-downloader.svg?style=social)

**A downloader for sponsored Afdian / Ifdian post files**

[![中文](https://img.shields.io/badge/README-中文-red.svg)](README.md)
[![English](https://img.shields.io/badge/README-English-blue.svg)](README.en.md)
[![日本語](https://img.shields.io/badge/README-日本語-green.svg)](README.ja.md)

</div>

---

### If this project helps you, please consider giving it a Star.

---

## Overview

`afdian-file-downloader` downloads videos, audio files, and attachments from Afdian / Ifdian posts that your own account can access. It reuses your browser Cookie to call the logged-in web APIs and saves downloadable files locally.

It is designed for:

- Scheduled creator downloads after a configured date.
- One-off post downloads.
- Browserless server, Qinglong Panel, or cron usage.
- Optional Bark push notifications when new files are downloaded.

## Scope

This tool does not bypass paywalls, CAPTCHA, permission checks, or DRM. It only downloads file links that are already available to your logged-in account. If your account has no access, the Cookie expires, or the platform changes its APIs, downloads may fail or be skipped.

## Features

- Cookie-based operation. Playwright is not required for server mode.
- Batch downloads from creator feed pages.
- Single-post downloads by URL.
- `since` / `until` date boundaries.
- File and folder names based on post titles.
- `download_state.json` deduplication for safe reruns.
- Filesystem fallback when the state file is missing.
- Average download speed printed after each file.
- Bark notifications with downloaded titles in the message body.

## Quick Start

### 1. Install

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

Normal creator and post downloads only need `requirements.txt`. Playwright is not required unless you use the optional legacy browser mode.

### 2. Create Config

```bash
cp config.example.json config.json
```

Windows PowerShell:

```powershell
Copy-Item .\config.example.json .\config.json
```

### 3. Prepare Cookie

Recommended Cookie extraction flow:

1. Log in to [ifdian.net](https://ifdian.net/) in your browser.
2. Open Developer Tools and go to Network.
3. Refresh a creator page or post page.
4. Select a request sent to `ifdian.net`.
5. Copy the `Cookie` request header.
6. Save it to `cookies.txt`.

Example:

```text
cookie_a=value_a; cookie_b=value_b; cookie_c=value_c
```

You can also paste it into the `cookie` field in `config.json`, but using `cookie_file` is safer for Git hygiene.

## Configuration

Example `config.json`:

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

| Field | Description |
| --- | --- |
| `cookie` | Raw browser Cookie request header. |
| `cookie_file` | Cookie file path. `cookies.txt` is recommended. |
| `download_dir` | Output directory. |
| `state_file` | Download state file. A plain filename is stored under `download_dir`, for example `downloads/download_state.json`. |
| `include_images` | Whether to download image resources. Default is `false` to avoid avatars and covers. |
| `overwrite` | Whether to overwrite existing files. Default is `false`. |
| `skip_existing` | Whether to skip existing downloads. Default is `true`. |
| `dry_run` | Preview mode. Detect files without writing them. |
| `since` / `until` | Global date boundaries. Supports `YYYY-MM-DD` and `YYYYMMDD`. |
| `creators` | Creator feed URLs for batch downloads. |
| `single_posts` | Post URLs for one-off downloads. |
| `bark` | Bark notification settings. |

## Download Creator Posts

```bash
python download_creators.py
```

Windows PowerShell:

```powershell
python .\download_creators.py
```

Startup output includes resolved configuration:

```text
[config] config_dir=...
[creators] configured=1
[creator-config] #1 url=https://ifdian.net/a/creator?tab=feed
[config] download_dir=...
[config] state_file=...
[config] manifest=...
[config] options={...}
```

Output example:

```text
downloads/
  Creator Name/
    2026-06-27-Post Title/
      Post Title.mp4
  download_state.json
  manifest.jsonl
```

## Download One Post

Pass the post URL directly:

```bash
python download_post.py "https://www.ifdian.net/p/post_id"
```

Or list URLs in `single_posts` and run:

```bash
python download_post.py
```

## Deduplication

`download_state.json` stores downloaded file state. It is created at startup and saved immediately after every successful download or local-file skip.

The primary key is based on:

```text
post_id + download URL + filename hint
```

If the state file is missing, the script checks existing files in the target post directory. Multiple same-name files in one post are matched by order:

```text
1st file -> title.mp4
2nd file -> title-1.mp4
3rd file -> title-2.mp4
```

## Bark Notifications

Enable Bark in `config.json`:

```json
{
  "bark": {
    "enabled": true,
    "server": "https://api.day.app",
    "device_key": "YOUR_BARK_DEVICE_KEY",
    "group": "Ifdian Downloader",
    "sound": "",
    "icon_creator_avatar": true
  }
}
```

When new files are downloaded, the notification body includes the downloaded video or attachment titles.

## Runtime Files

| File | Description |
| --- | --- |
| `download_state.json` | Deduplication state. |
| `manifest.jsonl` | Detailed records for successful, skipped, and failed items. |
| `*.part` | Temporary file during download. |

## Optional Browser Mode

Legacy browser login and page parsing are still available, but not required for normal server usage.

Install browser dependencies only when needed:

```bash
pip install -r requirements-browser.txt
python -m playwright install chromium
```

## Notes

- Do not commit `config.json`, `cookies.txt`, `download_state.json`, `manifest.jsonl`, or downloaded files.
- Refresh your Cookie when it expires.
- Without `since`, `until`, `max_posts`, or `stop_post_id`, the script tries to scan every visible post for the configured creator.
- Platform API changes may require script updates.

## Author

Hoyoung

## License

The source code is publicly available, but use, copying, modification, and distribution are limited to noncommercial purposes. Commercial use requires separate permission from the author.

License: PolyForm Noncommercial License 1.0.0. See [LICENSE](LICENSE).

---

<div align="center">

**If this project helps you, please consider giving it a Star.**

Made by Hoyoung

</div>
