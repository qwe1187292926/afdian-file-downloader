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
- Incremental creator-feed scans by default, with periodic full rescans.
- Readable `creator name / publish date-post title` directories; a short post identity is appended only for a confirmed post conflict or when an existing nonempty directory cannot be claimed safely.
- Readable filenames based on post or attachment titles, with post and file mappings stored in `.afdian-post.json`.
- Stable v2 asset identity in `download_state.json`, with compatible migration of legacy state.
- Sidecar and expected-filename fallback when the state file is missing.
- Average download speed printed after each file.
- Bark notifications with downloaded titles in the message body.
- One-time Bark alerts for new creator posts that your current tier cannot access, with state tracking to avoid repeated alerts.

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

| Field | Description |
| --- | --- |
| `cookie` | Raw browser Cookie request header. |
| `cookie_file` | Cookie file path. `cookies.txt` is recommended. |
| `download_dir` | Output directory. |
| `state_file` | Download state file. A plain filename is stored under `download_dir`, for example `downloads/download_state.json`. |
| `include_images` | Whether to download image resources. Default is `false` to avoid avatars and covers. |
| `overwrite` | Whether to overwrite existing files. Default is `false`. |
| `skip_existing` | Whether to skip existing downloads. Default is `true`. |
| `dry_run` | Preview mode. It does not download content, advance incremental checkpoints, or clear inaccessible-post state. Runtime state and manifest files may still be created or recorded. |
| `timeout` | Timeout in seconds for each download request. Default is `60`. |
| `per_page` | Number of creator posts requested per API page. Default is `10`. |
| `max_posts` | Maximum posts to process in one run; `0` means unlimited. A nonzero value is a manual scan boundary and disables automatic incremental checkpoints. |
| `since` / `until` | Global date boundaries. Supports `YYYY-MM-DD` and `YYYYMMDD`. |
| `stop_post_id` | Stop when this post ID is reached. An empty string means unset; a nonempty value disables automatic incremental checkpoints. |
| `incremental_scan` | Enable automatic incremental creator-feed scanning. It must be a JSON boolean and defaults to `true`. |
| `incremental_lookback_posts` | Number of non-pinned posts to keep scanning after a known checkpoint is reached. Default is `20`; negative values are treated as `0`. |
| `incremental_full_scan_days` | Days between periodic full rescans. Default is `30`; `0` disables periodic full rescans, but the first run is still full. |
| `creators` | Creator feed URLs for batch downloads. |
| `single_posts` | Post URLs for one-off downloads. |
| `bark` | Bark notification settings. |

Fields inside a `creators` entry override global fields with the same name, so each creator can use different date boundaries or scan settings.

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
[config] options={..., "incremental_scan": true, "incremental_lookback_posts": 20, "incremental_full_scan_days": 30}
[incremental] mode=full|incremental, lookback_posts=20, full_scan_days=30
```

Output example:

```text
downloads/
  Creator Name/
    2026-07-18-Post-Title/
      .afdian-post.json
      Post Title.mp4
  download_state.json
  manifest.jsonl
```

Normal directories keep the pre-upgrade readable layout: the creator directory uses the creator name, and the post directory uses the publish date and post title. Creator batch runs keep using the feed publish time, while the single-post entry point keeps using the publish time returned by the detail API. Stable post and asset identities stay inside `download_state.json` and `.afdian-post.json` instead of appearing in everyday folder names. A post uses `date-title--short-post-identity` only when an existing sidecar proves that the preferred readable directory belongs to another post, or when an existing nonempty directory cannot be claimed safely. Each claimed post directory stores full IDs, current title, URL, publish time, and asset-to-filename mappings in its sidecar.

This version does not automatically move the short-lived ID-only `creator-.../post-...` directories. After confirming that they contain duplicates, you may delete them directly. When those state paths no longer exist, the downloader falls back to the pre-upgrade readable directory and refreshes the v2 state to the existing old file without downloading it again.

## Incremental Scanning

`incremental_scan` defaults to `true`. When no usable checkpoint exists, normally on the first run, the script scans every visible post for the creator and stores recent non-pinned post IDs. Later runs start from the newest page, stop after reaching any known checkpoint plus `incremental_lookback_posts` additional non-pinned posts, and then save the new checkpoint. Pinned posts are processed normally, but they neither establish a checkpoint nor consume the lookback count.

A full rescan runs every `incremental_full_scan_days=30` days by default. Setting it to `0` disables only periodic full rescans; the initial scan is still full. Changing `include_images` or encountering a checkpoint from an older asset-identity version also forces a full scan.

Any of the following disables automatic incremental checkpoints and performs a normal scan under the active configuration:

- `since`, `until`, a nonzero `max_posts`, or `stop_post_id` is set;
- `skip_existing=false`;
- `overwrite=true`;
- `incremental_scan=false`.

Posts persisted in `inaccessible_posts` are retried separately even when they fall outside the incremental window, so restored access to an old post can still be detected. The entry is cleared only after the post is confirmed to have no downloadable files, or every candidate is downloaded/already downloaded. A failure, skip, or preview run keeps the entry.

A checkpoint advances only after the scan ends safely, the manifest is written, and no feed post has a detail failure, download failure, or skipped result. `dry_run` may read an existing checkpoint to reduce the scan range, but never advances it.

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

`download_state.json` stores downloaded file state, creator incremental checkpoints, and inaccessible-post alert state. It is created at startup and saved immediately after every successful download, local-file skip, or newly detected inaccessible post.

The current v2 asset key is SHA-256 hashed from stable identity inputs:

```text
post_id + (asset locator when available) + canonical download URL
```

Post titles and mutable filename hints are not part of the asset identity. URL canonicalization removes the fragment and removes signature fields only when a complete recognized AWS, Google Cloud, Tencent COS, Alibaba OSS, or CloudFront signing family is present. For Ifdian video URLs on `vod.afdiancdn.com`, the temporary `sign`, `t`, and `us` fields are removed only when the complete three-field family is present. Other hosts, incomplete families, and every additional functional query field remain part of the identity so genuinely different assets are not accidentally merged.

Legacy v1/v0 state is migrated lazily. To avoid redownloading files created before this upgrade, a unique exact v1/v0 key in the current candidate set reuses the original readable-path file even when the current URL contains query fields that the old algorithm could not distinguish. A shared ambiguous old key is still rejected. URL-only fuzzy migration is considered only when there is no exact legacy key and the canonical URL has no remaining functional query. The referenced file must still exist, and one physical path cannot be claimed by different posts or asset locators. Incorrect v2 keys previously created from rotating `sign/t/us` values are also migrated when the post, nonempty asset locator, and newly canonicalized URL all match exactly. If several duplicate records exist, the strict expected base filename is preferred; no file is moved or deleted automatically.

If the state file is missing, the script first uses `.afdian-post.json` in the readable post directory. A pre-upgrade directory without a sidecar is checked only for exact direct-child expected filenames; after a match, the v2 state and sidecar are added without moving or renaming the old file. Multiple same-name files in one post are matched by order:

```text
1st file -> title.mp4
2nd file -> title-1.mp4
3rd file -> title-2.mp4
```

Legacy doubled-extension names such as `archive.zip.zip` and `archive.zip-1.zip` are also recognized. The sidecar validates creator and post IDs and accepts only direct relative filenames inside the post directory. A nonempty sidecar-less directory is claimed only when old state points to a file for this post inside it, or at least one exact expected filename exists. Otherwise the original directory is preserved and new files use a readable collision directory with a short post identity.

If a creator post cannot be viewed because the current account does not have the required paid tier, the script records it under `inaccessible_posts`. Later runs do not repeat the alert while the post is still inaccessible. If you upgrade your tier and the post becomes accessible, the script clears that inaccessible state and proceeds through the normal download flow.

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

When a creator has a new post that cannot be downloaded with the current account permissions, the script also sends a one-time alert. The alert body includes the post title and tries to extract the required paid tier or plan from API fields. If Ifdian does not return that information, the body says that the API did not return a specific paid tier.

## Runtime Files

| File | Description |
| --- | --- |
| `download_state.json` | v2 deduplication state, creator incremental checkpoints, and inaccessible-post alert state. |
| `manifest.jsonl` | Detailed records for successful, skipped, and failed items. |
| `Creator Name/YYYY-MM-DD-Post-Title/.afdian-post.json` | Post metadata and the mapping from v2 asset keys to safe relative filenames, used for filesystem fallback. |
| `*.part` | Temporary file during download. |

## Optional Browser Mode

Legacy browser login and page parsing are still available, but not required for normal server usage.

Install browser dependencies only when needed:

```bash
pip install -r requirements-browser.txt
python -m playwright install chromium
```

## Development Verification

After changing the code, run the built-in regression tests and syntax check:

```powershell
python -m unittest discover -s tests -v
python -m py_compile .\afdian_downloader.py .\afdian_config_common.py .\download_creators.py .\download_post.py
```

## Notes

- Do not commit `config.json`, `cookies.txt`, `download_state.json`, `manifest.jsonl`, or downloaded files.
- Refresh your Cookie when it expires.
- In the default incremental mode, the first run scans every visible post; later runs scan from the newest post to the checkpoint plus lookback. Only disabling incremental mode makes every boundary-free run scan the full visible feed.
- Setting any manual boundary disables automatic incremental checkpoints, preventing a limited scan from incorrectly advancing the global checkpoint.
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
