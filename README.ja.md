# Afdian / Ifdian File Downloader

<div align="center">

![Python](https://img.shields.io/badge/Python-3.9+-blue.svg)
![Dependency](https://img.shields.io/badge/Dependency-requests-green.svg)
![Platform](https://img.shields.io/badge/Platform-Qinglong%20%7C%20Server%20%7C%20Local-orange.svg)
![License](https://img.shields.io/badge/License-PolyForm%20Noncommercial%201.0.0-lightgrey.svg)
![Last Commit](https://img.shields.io/github/last-commit/qwe1187292926/afdian-file-downloader.svg)
![Issues](https://img.shields.io/github/issues/qwe1187292926/afdian-file-downloader.svg)
![Stars](https://img.shields.io/github/stars/qwe1187292926/afdian-file-downloader.svg?style=social)

**Afdian / Ifdian の支援者向け投稿ファイルダウンローダー**

[![中文](https://img.shields.io/badge/README-中文-red.svg)](README.md)
[![English](https://img.shields.io/badge/README-English-blue.svg)](README.en.md)
[![日本語](https://img.shields.io/badge/README-日本語-green.svg)](README.ja.md)

</div>

---

### このプロジェクトが役に立った場合は、Star を付けていただけると嬉しいです。

---

## 概要

`afdian-file-downloader` は、Afdian / Ifdian の投稿から、自分のアカウントでアクセスできる動画、音声、添付ファイルをダウンロードするためのツールです。ブラウザから取得した Cookie を使い、ログイン済みの Web API を呼び出してファイルを保存します。

主な用途：

- 指定日以降のクリエイター投稿を定期的にダウンロードする。
- 特定の投稿だけを追加でダウンロードする。
- サーバー、Qinglong Panel、cron でブラウザなしに実行する。
- 新規ダウンロード完了時に Bark 通知を受け取る。

## 対象範囲

このツールは、ペイウォール、CAPTCHA、権限チェック、DRM を回避しません。現在ログインしている自分のアカウントで閲覧可能なファイルだけを対象にします。権限がない場合、Cookie が期限切れの場合、またはプラットフォームの API が変更された場合、ダウンロードは失敗またはスキップされます。

## 機能

- Cookie ベースで実行可能。サーバーモードでは Playwright は不要。
- クリエイターページから投稿を一括ダウンロード。
- 投稿 URL を指定した単一投稿ダウンロード。
- `since` / `until` による日付範囲指定。
- 投稿タイトルを使ったファイル名とディレクトリ名。
- `download_state.json` による重複回避。
- 状態ファイルがない場合も既存ファイルを確認。
- ファイルごとの平均ダウンロード速度を表示。
- Bark 通知に対応し、通知本文に今回のダウンロードタイトルを含める。

## クイックスタート

### 1. インストール

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

通常のクリエイター投稿ダウンロードと単一投稿ダウンロードでは `requirements.txt` のみで動作します。Playwright は不要です。

### 2. 設定ファイルを作成

```bash
cp config.example.json config.json
```

Windows PowerShell:

```powershell
Copy-Item .\config.example.json .\config.json
```

### 3. Cookie を準備

推奨手順：

1. ブラウザで [ifdian.net](https://ifdian.net/) にログインします。
2. 開発者ツールを開き、Network を表示します。
3. クリエイターページまたは投稿ページを更新します。
4. `ifdian.net` へのリクエストを選択します。
5. リクエストヘッダーの `Cookie` をコピーします。
6. `cookies.txt` に保存します。

例：

```text
cookie_a=value_a; cookie_b=value_b; cookie_c=value_c
```

`config.json` の `cookie` に直接貼り付けることもできますが、Git に誤って含めないためには `cookie_file` の利用を推奨します。

## 設定

`config.json` の例：

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

| 項目 | 説明 |
| --- | --- |
| `cookie` | ブラウザの Cookie リクエストヘッダーを直接指定します。 |
| `cookie_file` | Cookie ファイルのパスです。`cookies.txt` を推奨します。 |
| `download_dir` | ダウンロード先ディレクトリです。 |
| `state_file` | ダウンロード状態ファイルです。ファイル名だけを指定した場合は `download_dir` 配下に保存されます。 |
| `include_images` | 画像リソースをダウンロードするかどうか。デフォルトは `false` です。 |
| `overwrite` | 既存ファイルを上書きするかどうか。デフォルトは `false` です。 |
| `skip_existing` | 既存のダウンロードをスキップするかどうか。デフォルトは `true` です。 |
| `dry_run` | プレビューモードです。ファイルを書き込みません。 |
| `since` / `until` | 全体の日付範囲です。`YYYY-MM-DD` または `YYYYMMDD` を指定できます。 |
| `creators` | 一括ダウンロード対象のクリエイターページです。 |
| `single_posts` | 個別ダウンロード対象の投稿 URL です。 |
| `bark` | Bark 通知設定です。 |

## クリエイター投稿をダウンロード

```bash
python download_creators.py
```

Windows PowerShell:

```powershell
python .\download_creators.py
```

起動時に解決済みの設定が表示されます：

```text
[config] config_dir=...
[creators] configured=1
[creator-config] #1 url=https://ifdian.net/a/creator?tab=feed
[config] download_dir=...
[config] state_file=...
[config] manifest=...
[config] options={...}
```

出力例：

```text
downloads/
  Creator Name/
    2026-06-27-Post Title/
      Post Title.mp4
  download_state.json
  manifest.jsonl
```

## 単一投稿をダウンロード

投稿 URL を直接指定します：

```bash
python download_post.py "https://www.ifdian.net/p/post_id"
```

または `single_posts` に URL を設定して実行します：

```bash
python download_post.py
```

## 重複管理

`download_state.json` はダウンロード済みファイルの状態を保存します。起動時に作成され、ファイルのダウンロード成功時またはローカル既存ファイルの検出時にすぐ保存されます。

通常は次の情報でファイルを識別します：

```text
post_id + download URL + filename hint
```

状態ファイルがない場合でも、投稿ディレクトリ内の既存ファイルを確認します。同じ投稿内に同名ファイルが複数ある場合は、順番で対応します：

```text
1 個目 -> title.mp4
2 個目 -> title-1.mp4
3 個目 -> title-2.mp4
```

## Bark 通知

`config.json` で Bark を有効にします：

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

新しいファイルがダウンロードされた場合、通知本文に今回ダウンロードした動画または添付ファイルのタイトルが含まれます。

## 実行時ファイル

| ファイル | 説明 |
| --- | --- |
| `download_state.json` | 重複管理用の状態ファイルです。 |
| `manifest.jsonl` | 成功、スキップ、失敗した項目の詳細ログです。 |
| `*.part` | ダウンロード中の一時ファイルです。 |

## オプションのブラウザモード

レガシーのブラウザログインとページ解析モードは残っていますが、通常のサーバー利用では不要です。

必要な場合のみインストールしてください：

```bash
pip install -r requirements-browser.txt
python -m playwright install chromium
```

## 注意事項

- `config.json`、`cookies.txt`、`download_state.json`、`manifest.jsonl`、ダウンロード済みファイルをコミットしないでください。
- Cookie が期限切れになった場合は再取得してください。
- `since`、`until`、`max_posts`、`stop_post_id` を設定しない場合、対象クリエイターの表示可能な全投稿をスキャンします。
- プラットフォーム API が変更された場合、スクリプトの更新が必要になることがあります。

## 作者

Hoyoung

## ライセンス

本プロジェクトのソースコードは公開されていますが、使用、コピー、変更、配布は非商用目的に限られます。商用利用には作者からの別途許可が必要です。

ライセンス：PolyForm Noncommercial License 1.0.0。詳細は [LICENSE](LICENSE) を参照してください。

---

<div align="center">

**このプロジェクトが役に立った場合は、Star を付けていただけると嬉しいです。**

Made by Hoyoung

</div>
