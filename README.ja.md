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
- デフォルトでクリエイターフィードを増分スキャンし、定期的にフルスキャンを実行。
- クリエイター ID と投稿 ID に基づく安定したディレクトリを使用し、クリエイター名、投稿タイトル、公開日時が変わっても保存先を維持。
- 投稿または添付ファイルのタイトルから読みやすいファイル名を生成し、`.afdian-post.json` に投稿とファイルの対応を保存。
- `download_state.json` の安定した v2 アセット識別子で重複を回避し、旧形式の状態も互換移行。
- 状態ファイルがない場合は sidecar と予想ファイル名で既存ファイルを確認。
- ファイルごとの平均ダウンロード速度を表示。
- Bark 通知に対応し、通知本文に今回のダウンロードタイトルを含める。
- 現在の支援プランではアクセスできない新規クリエイター投稿について、Bark で一度だけ通知し、重複通知を避けるため状態を保存。

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

| 項目 | 説明 |
| --- | --- |
| `cookie` | ブラウザの Cookie リクエストヘッダーを直接指定します。 |
| `cookie_file` | Cookie ファイルのパスです。`cookies.txt` を推奨します。 |
| `download_dir` | ダウンロード先ディレクトリです。 |
| `state_file` | ダウンロード状態ファイルです。ファイル名だけを指定した場合は `download_dir` 配下に保存されます。 |
| `include_images` | 画像リソースをダウンロードするかどうか。デフォルトは `false` です。 |
| `overwrite` | 既存ファイルを上書きするかどうか。デフォルトは `false` です。 |
| `skip_existing` | 既存のダウンロードをスキップするかどうか。デフォルトは `true` です。 |
| `dry_run` | プレビューモードです。コンテンツをダウンロードせず、増分チェックポイントを進めず、アクセス不可状態も解除しません。実行時の状態ファイルや manifest は作成・記録される場合があります。 |
| `timeout` | 各ダウンロードリクエストのタイムアウト秒数です。デフォルトは `60` です。 |
| `per_page` | クリエイター投稿 API の 1 ページあたりの取得数です。デフォルトは `10` です。 |
| `max_posts` | 1 回の実行で処理する投稿数の上限です。`0` は無制限です。0 以外は手動スキャン境界として扱われ、自動増分チェックポイントを無効にします。 |
| `since` / `until` | 全体の日付範囲です。`YYYY-MM-DD` または `YYYYMMDD` を指定できます。 |
| `stop_post_id` | この投稿 ID に達した時点でスキャンを停止します。空文字列は未設定です。値を設定すると自動増分チェックポイントが無効になります。 |
| `incremental_scan` | クリエイターフィードの自動増分スキャンを有効にします。JSON の真偽値で指定し、デフォルトは `true` です。 |
| `incremental_lookback_posts` | 既知のチェックポイント到達後も追加で確認する非固定投稿数です。デフォルトは `20` で、負の値は `0` として扱われます。 |
| `incremental_full_scan_days` | 定期フルスキャンの間隔（日数）です。デフォルトは `30` です。`0` にすると定期フルスキャンを無効にしますが、初回は引き続きフルスキャンされます。 |
| `creators` | 一括ダウンロード対象のクリエイターページです。 |
| `single_posts` | 個別ダウンロード対象の投稿 URL です。 |
| `bark` | Bark 通知設定です。 |

`creators` 内のフィールドは同名のグローバル設定を上書きするため、クリエイターごとに異なる日付境界やスキャン設定を使用できます。

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
[config] options={..., "incremental_scan": true, "incremental_lookback_posts": 20, "incremental_full_scan_days": 30}
[incremental] mode=full|incremental, lookback_posts=20, full_scan_days=30
```

出力例：

```text
downloads/
  creator-<安全な ID トークン>/
    post-<安全な ID トークン>/
      .afdian-post.json
      Post Title.mp4
  download_state.json
  manifest.jsonl
```

ディレクトリトークンは安定した ID だけから生成し、サニタイズした短い接頭辞と 12 文字の SHA-256 ダイジェストを組み合わせます。クリエイター名、投稿タイトル、公開日時が変わっても同じパスを再利用します。各新規投稿ディレクトリの `.afdian-post.json` には、完全な ID、現在のタイトル、URL、公開日時、およびアセットから相対ファイル名への対応が保存されます。

## 増分スキャン

`incremental_scan` のデフォルトは `true` です。利用可能なチェックポイントがない場合（通常は初回実行）、対象クリエイターの表示可能な全投稿をスキャンし、最近の非固定投稿 ID を保存します。以後は最新ページから開始し、既知のチェックポイントのいずれかに到達した後、さらに `incremental_lookback_posts` 件の非固定投稿を確認して停止します。固定投稿も通常どおり処理しますが、チェックポイントにはならず、追加確認数も消費しません。

デフォルトでは `incremental_full_scan_days=30` 日ごとにフルスキャンを行います。`0` にすると定期フルスキャンだけを無効にし、初回スキャンは引き続きフルです。`include_images` を変更した場合や、旧アセット識別形式のチェックポイントを検出した場合もフルスキャンを実行します。

次のいずれかに該当すると、自動増分チェックポイントは無効になり、現在の設定に従って通常スキャンを実行します：

- `since`、`until`、0 以外の `max_posts`、または `stop_post_id` を設定した場合。
- `skip_existing=false` の場合。
- `overwrite=true` の場合。
- `incremental_scan=false` の場合。

`inaccessible_posts` に保存されたアクセス不可投稿は、増分ウィンドウ外でも個別に再確認されます。そのため、古い投稿へのアクセス権が復旧した場合も検出できます。ダウンロード可能なファイルがないことを確認した場合、または全候補がダウンロード済み・既存である場合にのみ記録を解除します。失敗、スキップ、プレビュー実行では記録を維持します。

チェックポイントは、スキャンが安全に完了し、manifest の書き込みに成功し、今回のフィード投稿で詳細取得失敗、ダウンロード失敗、スキップが発生しなかった場合にのみ進みます。`dry_run` は既存チェックポイントを利用してスキャン範囲を縮小できますが、チェックポイントは更新しません。

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

`download_state.json` はダウンロード済みファイルの状態、クリエイターの増分チェックポイント、およびアクセス不可投稿の通知状態を保存します。起動時に作成され、ファイルのダウンロード成功時、ローカル既存ファイルの検出時、またはアクセス不可投稿の検出時にすぐ保存されます。

現在の v2 アセットキーは、次の安定した識別情報を SHA-256 でハッシュして生成します：

```text
post_id + （利用可能な場合はアセットロケーター） + 正規化ダウンロード URL
```

投稿タイトルと変更される可能性があるファイル名ヒントは識別子に含めません。URL の正規化では fragment を削除し、AWS、Google Cloud、Tencent COS、Alibaba OSS、CloudFront の完全な署名パラメータ群を認識した場合に限って、その署名フィールドを除外します。用途を確定できない単独の `sign` や `token` などのクエリパラメータは保持し、実際には異なるアセットを誤って統合しないようにします。

旧 v1/v0 状態は、再度一致した時点で遅延移行されます。旧キーまたは URL の一致が一意であり、現在の正規化 URL に機能性または用途不明の query が残っておらず、参照先ファイルが存在し、同じ物理パスが別の v2 アセットにまだ割り当てられていない場合に限り、v2 エイリアスを作成します。1 つの旧ファイルを 2 つの新アセットに割り当てることはありません。同一性を安全に証明できない query 付き旧記録は一度再ダウンロードします。旧タイトルベースのディレクトリは自動で名前変更されませんが、旧状態がファイルを参照していれば引き続き認識されます。

状態ファイルがない場合、まず ID ベースの投稿ディレクトリにある `.afdian-post.json` から正確な相対ファイル名を確認し、その後に予想ファイル名で補完します。同じ投稿内に同名ファイルが複数ある場合は、順番で対応します：

```text
1 個目 -> title.mp4
2 個目 -> title-1.mp4
3 個目 -> title-2.mp4
```

sidecar はクリエイター ID と投稿 ID を検証し、投稿ディレクトリ直下の単一階層の相対ファイル名だけを受け付けます。関係のないユーザーファイルを誤って引き継がないよう、`.afdian-post.json` がない空でない ID ベースの投稿ディレクトリは使用を拒否し、失敗として記録します。

現在のアカウントの支援プランでは閲覧できないクリエイター投稿は、状態ファイルの `inaccessible_posts` に記録されます。以後もアクセス不可のままなら通知は繰り返しません。支援プランを上げてアクセスできるようになった場合は、そのアクセス不可状態を解除し、通常のダウンロード処理に進みます。

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

クリエイターに新規投稿があり、現在のアカウント権限ではダウンロードできない場合も、一度だけ通知します。通知本文には投稿タイトルを含め、API フィールドから必要な支援プランや料金タイプを可能な範囲で抽出します。Ifdian の API がその情報を返さない場合は、具体的な料金タイプが返されなかったことを表示します。

## 実行時ファイル

| ファイル | 説明 |
| --- | --- |
| `download_state.json` | v2 重複管理状態、クリエイター増分チェックポイント、アクセス不可投稿の通知状態を保存します。 |
| `manifest.jsonl` | 成功、スキップ、失敗した項目の詳細ログです。 |
| `creator-<安全な ID トークン>/post-<安全な ID トークン>/.afdian-post.json` | 投稿メタデータと、v2 アセットキーから安全な相対ファイル名への対応です。ファイルシステムからの補完確認に使われます。 |
| `*.part` | ダウンロード中の一時ファイルです。 |

## オプションのブラウザモード

レガシーのブラウザログインとページ解析モードは残っていますが、通常のサーバー利用では不要です。

必要な場合のみインストールしてください：

```bash
pip install -r requirements-browser.txt
python -m playwright install chromium
```

## 開発時の検証

コードを変更した後は、組み込みの回帰テストと構文チェックを実行できます：

```powershell
python -m unittest discover -s tests -v
python -m py_compile .\afdian_downloader.py .\afdian_config_common.py .\download_creators.py .\download_post.py
```

## 注意事項

- `config.json`、`cookies.txt`、`download_state.json`、`manifest.jsonl`、ダウンロード済みファイルをコミットしないでください。
- Cookie が期限切れになった場合は再取得してください。
- デフォルトの増分モードでは、初回は表示可能な全投稿をスキャンし、以後は最新投稿からチェックポイントと追加確認分までをスキャンします。増分モードを無効にした場合のみ、手動境界のない実行で毎回フィード全体をスキャンします。
- 手動境界を 1 つでも設定すると自動増分チェックポイントが無効になり、限定範囲のスキャンで全体チェックポイントを誤って進めることを防ぎます。
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
