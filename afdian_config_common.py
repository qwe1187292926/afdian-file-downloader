from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse, urlunparse

import requests

from afdian_downloader import (
    Candidate,
    FeedPost,
    add_cookie_text,
    candidate_identity,
    candidates_from_post_detail,
    canonical_candidate_url,
    collision_directory_name,
    creator_directory_name,
    create_base_session,
    download_candidate,
    fetch_post_detail_api,
    format_publish_date,
    get_creator_profile,
    normalize_ifdian_url,
    parse_date_boundary,
    post_directory_name,
    post_id_from_url,
    post_title_from_api,
    scan_feed_posts_api,
    sanitize_filename,
)

POST_SIDECAR_NAME = ".afdian-post.json"
POST_SIDECAR_SCHEMA_VERSION = 1
ASSET_IDENTITY_VERSION = 2


def configure_stdio() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def load_config(config_path: str | Path = "config.json") -> tuple[dict[str, Any], Path]:
    if str(config_path) == "config.json" and os.getenv("IFDIAN_CONFIG"):
        config_path = os.environ["IFDIAN_CONFIG"]
    path = Path(config_path).resolve()
    if not path.exists():
        raise SystemExit(f"配置文件不存在: {path}\n请复制 config.example.json 为 config.json 后填写 cookie、下载目录和创作者地址。")
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise SystemExit(f"配置文件必须是 JSON object: {path}")
    return config, path.parent


def resolve_config_path(config_dir: Path, value: str | None, default: str) -> Path:
    raw = value or default
    path = Path(raw)
    if not path.is_absolute():
        path = config_dir / path
    return path.resolve()


def resolve_state_path(config_dir: Path, download_dir: Path, value: str | None) -> Path:
    raw = str(value or "").strip()
    if not raw:
        return (download_dir / "download_state.json").resolve()

    path = Path(raw)
    if path.is_absolute():
        return path.resolve()
    if path.parent == Path("."):
        return (download_dir / path).resolve()
    return (config_dir / path).resolve()


def create_session_from_config(config: dict[str, Any], config_dir: Path) -> requests.Session:
    session = create_base_session()
    cookie_values: list[str] = []
    cookie = str(config.get("cookie") or "").strip()
    if cookie:
        cookie_values.append(cookie)
    cookie_file = str(config.get("cookie_file") or "").strip()
    if cookie_file:
        path = resolve_config_path(config_dir, cookie_file, "cookies.txt")
        if path.exists():
            cookie_values.append(path.read_text(encoding="utf-8").strip())
    if not cookie_values:
        raise SystemExit("配置里没有可用 Cookie。请设置 cookie，或设置 cookie_file 并写入浏览器复制的 Cookie 请求头。")
    for value in cookie_values:
        add_cookie_text(session, value)
    return session


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_download_url(url: str) -> str:
    return canonical_candidate_url(url)


def legacy_canonical_download_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


def download_key(post_id: str, candidate: Candidate) -> str:
    source = "\0".join(["v2", post_id, candidate_identity(candidate)])
    return "v2:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def download_key_v1(post_id: str, candidate: Candidate) -> str:
    source = "|".join([post_id, legacy_canonical_download_url(candidate.url), candidate.filename_hint])
    return hashlib.sha1(source.encode("utf-8")).hexdigest()


def legacy_download_key(post_id: str, candidate: Candidate) -> str:
    source = "|".join(
        [
            post_id,
            candidate.source,
            legacy_canonical_download_url(candidate.url),
            candidate.filename_hint,
        ]
    )
    return hashlib.sha1(source.encode("utf-8")).hexdigest()


def file_claim_key(path: str | Path) -> str:
    try:
        resolved = Path(path).resolve()
    except (OSError, RuntimeError):
        resolved = Path(os.path.abspath(str(path)))
    return os.path.normcase(str(resolved))


class DownloadState:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {
            "schema_version": 2,
            "downloads": {},
            "inaccessible_posts": {},
            "creator_scans": {},
        }
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except json.JSONDecodeError:
                backup = path.with_suffix(path.suffix + ".broken")
                path.replace(backup)
        self.data["schema_version"] = 2
        for section in ("downloads", "inaccessible_posts", "creator_scans"):
            if not isinstance(self.data.get(section), dict):
                self.data[section] = {}

    def has(self, key: str) -> bool:
        entry = self.data.get("downloads", {}).get(key)
        if not isinstance(entry, dict):
            return False
        file_path = entry.get("path")
        return bool(file_path and Path(file_path).is_file())

    def get_existing_entry(self, key: str) -> dict[str, Any] | None:
        entry = self.data.get("downloads", {}).get(key)
        if not isinstance(entry, dict):
            return None
        file_path = entry.get("path")
        if file_path and Path(file_path).is_file():
            return entry
        return None

    def has_post_file_in_directory(self, post_id: str, output_dir: Path) -> bool:
        expected_parent = output_dir.resolve()
        for entry in self.data.get("downloads", {}).values():
            if not isinstance(entry, dict) or str(entry.get("post_id") or "") != post_id:
                continue
            file_path = str(entry.get("path") or "")
            if not file_path or not Path(file_path).is_file():
                continue
            if Path(file_path).resolve().parent == expected_parent:
                return True
        return False

    def path_claimed_by_other_v2_asset(
        self,
        path: str,
        asset_key: str,
        post_id: str,
        asset_locator: str,
    ) -> bool:
        target_path = file_claim_key(path)
        for existing_key, entry in self.data.get("downloads", {}).items():
            if (
                not isinstance(entry, dict)
                or entry.get("identity_version") != ASSET_IDENTITY_VERSION
                or str(existing_key) == asset_key
            ):
                continue
            existing_path = str(entry.get("path") or "")
            if not existing_path or not Path(existing_path).is_file():
                continue
            if file_claim_key(existing_path) != target_path:
                continue
            same_logical_asset = bool(
                asset_locator
                and str(entry.get("post_id") or "") == post_id
                and str(entry.get("asset_locator") or "") == asset_locator
            )
            if not same_logical_asset:
                return True
        return False

    def alias(
        self,
        new_key: str,
        existing_entry: dict[str, Any],
        post_meta: dict[str, Any],
        candidate: Candidate,
        migrated_from: str,
    ) -> None:
        entry = dict(existing_entry)
        entry.setdefault("post_id", post_meta.get("post_id"))
        entry.setdefault("post_title", post_meta.get("post_title"))
        entry["identity_version"] = ASSET_IDENTITY_VERSION
        entry["asset_key"] = new_key
        entry["identity_url"] = canonical_download_url(candidate.url)
        entry["asset_locator"] = candidate.asset_locator
        entry["migrated_from"] = migrated_from
        entry["migrated_at"] = now_iso()
        entry["aliased_at"] = now_iso()
        self.data.setdefault("downloads", {})[new_key] = entry

    def mark(self, key: str, record: dict[str, Any]) -> None:
        self.data.setdefault("downloads", {})[key] = {
            "path": record.get("path"),
            "bytes": record.get("bytes"),
            "post_id": record.get("post_id"),
            "post_title": record.get("post_title"),
            "url": canonical_download_url(str(record.get("url") or "")),
            "identity_version": ASSET_IDENTITY_VERSION,
            "asset_key": key,
            "asset_locator": record.get("asset_locator"),
            "downloaded_at": now_iso(),
        }

    def find_legacy_entry_for_url(
        self,
        post_id: str,
        candidate: Candidate,
        claimed_paths: set[str],
    ) -> tuple[str, dict[str, Any]] | None:
        target_url = legacy_canonical_download_url(candidate.url)
        matches: list[tuple[str, dict[str, Any]]] = []
        for existing_key, entry in self.data.get("downloads", {}).items():
            if not isinstance(entry, dict) or entry.get("identity_version") == ASSET_IDENTITY_VERSION:
                continue
            if str(entry.get("post_id") or "") != post_id:
                continue
            if legacy_canonical_download_url(str(entry.get("url") or "")) != target_url:
                continue
            path = str(entry.get("path") or "")
            if not path or file_claim_key(path) in claimed_paths or not Path(path).is_file():
                continue
            if self.path_claimed_by_other_v2_asset(
                path,
                download_key(post_id, candidate),
                post_id,
                candidate.asset_locator,
            ):
                continue
            matches.append((str(existing_key), entry))
        return matches[0] if len(matches) == 1 else None

    def get_inaccessible_post(self, post_id: str) -> dict[str, Any] | None:
        entry = self.data.get("inaccessible_posts", {}).get(post_id)
        return entry if isinstance(entry, dict) else None

    def is_inaccessible_notified(self, post_id: str) -> bool:
        entry = self.get_inaccessible_post(post_id)
        return bool(entry and entry.get("notified"))

    def mark_inaccessible(self, post_meta: dict[str, Any], error: str = "", access_requirement: str = "") -> None:
        post_id = str(post_meta.get("post_id") or "")
        if not post_id:
            return
        existing = self.get_inaccessible_post(post_id) or {}
        entry = dict(existing)
        entry.update(
            {
                "status": "inaccessible",
                "post_id": post_id,
                "post_title": post_meta.get("post_title"),
                "post_url": post_meta.get("post_url"),
                "creator_id": post_meta.get("creator_id"),
                "creator_name": post_meta.get("creator_name"),
                "publish_time": post_meta.get("publish_time"),
                "publish_date": post_meta.get("publish_date"),
                "error": error,
                "access_requirement": access_requirement,
                "last_seen_at": now_iso(),
            }
        )
        entry.setdefault("first_seen_at", now_iso())
        entry.setdefault("notified", False)
        self.data.setdefault("inaccessible_posts", {})[post_id] = entry

    def mark_inaccessible_notified(self, post_id: str) -> None:
        entry = self.get_inaccessible_post(post_id)
        if not entry:
            return
        entry["notified"] = True
        entry["notified_at"] = now_iso()

    def clear_inaccessible(self, post_id: str) -> dict[str, Any] | None:
        entry = self.get_inaccessible_post(post_id)
        if entry:
            self.data.setdefault("inaccessible_posts", {}).pop(post_id, None)
        return entry

    def inaccessible_posts_for_creator(self, creator_id: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for entry in self.data.get("inaccessible_posts", {}).values():
            if isinstance(entry, dict) and str(entry.get("creator_id") or "") == creator_id:
                entries.append(dict(entry))
        return entries

    def list_inaccessible_posts(self, creator_id: str) -> list[dict[str, Any]]:
        return self.inaccessible_posts_for_creator(creator_id)

    def get_creator_scan(self, creator_id: str) -> dict[str, Any] | None:
        entry = self.data.get("creator_scans", {}).get(creator_id)
        if not isinstance(entry, dict) or entry.get("version") != 1:
            return None
        checkpoint_ids = entry.get("checkpoint_post_ids")
        if not isinstance(checkpoint_ids, list) or not any(str(item).strip() for item in checkpoint_ids):
            return None
        return entry

    def mark_creator_scan(
        self,
        creator_id: str,
        checkpoint_post_ids: list[str],
        checkpoint_publish_time: int,
        full_scan: bool,
        include_images: bool,
    ) -> None:
        if not checkpoint_post_ids:
            return
        existing = self.get_creator_scan(creator_id) or {}
        entry = dict(existing)
        entry.update(
            {
                "version": 1,
                "checkpoint_post_id": checkpoint_post_ids[0],
                "checkpoint_post_ids": checkpoint_post_ids,
                "checkpoint_publish_time": checkpoint_publish_time,
                "include_images": include_images,
                "asset_identity_version": ASSET_IDENTITY_VERSION,
                "updated_at": now_iso(),
            }
        )
        if full_scan or not entry.get("last_full_scan_at"):
            entry["last_full_scan_at"] = now_iso()
        self.data.setdefault("creator_scans", {})[creator_id] = entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def creator_scan_requires_full_scan(
    checkpoint: dict[str, Any] | None,
    full_scan_days: int,
    include_images: bool,
) -> bool:
    if checkpoint is None:
        return True
    if (
        checkpoint.get("asset_identity_version") != ASSET_IDENTITY_VERSION
        or checkpoint.get("include_images") != include_images
    ):
        return True
    if full_scan_days <= 0:
        return False
    raw_timestamp = str(checkpoint.get("last_full_scan_at") or "")
    try:
        last_full_scan = datetime.fromisoformat(raw_timestamp)
    except ValueError:
        return True
    if last_full_scan.tzinfo is None:
        last_full_scan = last_full_scan.astimezone()
    age = datetime.now().astimezone() - last_full_scan
    return age.total_seconds() >= full_scan_days * 24 * 60 * 60


def bark_notify(config: dict[str, Any], title: str, body: str, icon: str = "", url: str = "") -> bool:
    bark = config.get("bark") or {}
    if not isinstance(bark, dict) or not bark.get("enabled"):
        return False
    device_key = str(bark.get("device_key") or "").strip()
    if not device_key:
        print("[bark] skipped: missing device_key")
        return False
    server = str(bark.get("server") or "https://api.day.app").rstrip("/")
    endpoint = f"{server}/{device_key}/{title}/{body}"
    params: dict[str, str] = {}
    if bark.get("group"):
        params["group"] = str(bark["group"])
    if bark.get("sound"):
        params["sound"] = str(bark["sound"])
    if icon and bark.get("icon_creator_avatar", True):
        params["icon"] = icon
    if url:
        params["url"] = url
    try:
        response = requests.get(endpoint, params=params, timeout=10)
        response.raise_for_status()
        print(f"[bark] sent: {title}")
        return True
    except Exception as exc:
        print(f"[bark] failed: {exc}")
        return False


def downloaded_titles(records: list[dict[str, Any]]) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for record in records:
        if record.get("status") != "downloaded":
            continue
        title = str(record.get("post_title") or record.get("filename_hint") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        titles.append(title)
    return titles


def bark_download_body(downloaded_count: int, records: list[dict[str, Any]], max_length: int = 420) -> str:
    titles = downloaded_titles(records)
    if not titles:
        return f"新增 {downloaded_count} 个文件"

    lines = [f"新增 {downloaded_count} 个文件：", *[f"- {title}" for title in titles]]
    body = "\n".join(lines)
    if len(body) <= max_length:
        return body

    compact = f"新增 {downloaded_count} 个文件："
    included: list[str] = []
    for title in titles:
        candidate = compact + "\n" + "\n".join([*[f"- {item}" for item in included], f"- {title}"])
        remaining = len(titles) - len(included) - 1
        suffix = f"\n... 还有 {remaining} 个标题" if remaining else ""
        if len(candidate + suffix) > max_length:
            break
        included.append(title)
    remaining = len(titles) - len(included)
    body = compact + "\n" + "\n".join(f"- {item}" for item in included)
    if remaining:
        body += f"\n... 还有 {remaining} 个标题"
    return body


ACCESS_CONTAINER_KEYWORDS = (
    "access",
    "album",
    "amount",
    "fee",
    "level",
    "paid",
    "pay",
    "permission",
    "plan",
    "price",
    "product",
    "right",
    "sale",
    "sku",
    "sponsor",
    "tier",
    "vip",
)

ACCESS_LABEL_KEYS = {
    "desc",
    "description",
    "display_name",
    "level_name",
    "name",
    "plan_name",
    "rank_name",
    "title",
}

ACCESS_DIRECT_KEYS = {
    "access_requirement",
    "album_title",
    "current_plan_name",
    "need_plan_name",
    "paid_type",
    "pay_type",
    "plan_name",
    "required_plan",
    "required_plan_name",
    "sku_name",
    "sponsor_level",
    "tier_name",
}


def access_value_to_text(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return " ".join(value.strip().split())
    return ""


def access_path_is_relevant(path: str) -> bool:
    lowered = path.lower()
    return any(keyword in lowered for keyword in ACCESS_CONTAINER_KEYWORDS)


def collect_access_requirement_values(value: Any, path: str = "", depth: int = 0) -> list[str]:
    if depth > 5:
        return []
    values: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            lowered_key = key_text.lower()
            item_path = f"{path}.{lowered_key}" if path else lowered_key
            relevant = access_path_is_relevant(item_path)
            if lowered_key in ACCESS_DIRECT_KEYS:
                text = access_value_to_text(item)
                if text:
                    values.append(text)
            elif relevant and lowered_key in ACCESS_LABEL_KEYS:
                text = access_value_to_text(item)
                if text:
                    values.append(text)
            elif relevant and isinstance(item, (str, int, float)) and any(token in lowered_key for token in ("amount", "fee", "price")):
                text = access_value_to_text(item)
                if text:
                    values.append(f"{key_text}: {text}")
            if isinstance(item, (dict, list)) and relevant:
                values.extend(collect_access_requirement_values(item, item_path, depth + 1))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            values.extend(collect_access_requirement_values(item, f"{path}[{index}]", depth + 1))
    return values


def access_requirement_from_api(*sources: Any, fallback: str = "") -> str:
    seen: set[str] = set()
    labels: list[str] = []
    for source in sources:
        for value in collect_access_requirement_values(source):
            if not value or value in seen:
                continue
            seen.add(value)
            labels.append(value)
    if labels:
        return "；".join(labels[:5])
    fallback_text = access_value_to_text(fallback)
    if fallback_text:
        return fallback_text
    return "接口未返回具体付费类型"


def bark_inaccessible_body(records: list[dict[str, Any]], max_length: int = 420) -> str:
    lines = [f"发现 {len(records)} 篇新投稿当前账号无权限下载："]
    for record in records:
        title = str(record.get("post_title") or record.get("post_id") or "未命名投稿").strip()
        requirement = str(record.get("access_requirement") or "").strip()
        if requirement:
            lines.append(f"- {title}（需要：{requirement}）")
        else:
            lines.append(f"- {title}")
    body = "\n".join(lines)
    if len(body) <= max_length:
        return body

    compact = lines[0]
    included: list[str] = []
    for line in lines[1:]:
        remaining = len(lines) - len(included) - 2
        suffix = f"\n... 还有 {remaining} 篇投稿" if remaining else ""
        candidate = compact + "\n" + "\n".join([*included, line])
        if len(candidate + suffix) > max_length:
            break
        included.append(line)
    remaining = len(lines) - len(included) - 1
    body = compact + ("\n" + "\n".join(included) if included else "")
    if remaining:
        body += f"\n... 还有 {remaining} 篇投稿"
    return body


def append_manifest(manifest_path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("a", encoding="utf-8") as manifest:
        for record in records:
            manifest.write(json.dumps(record, ensure_ascii=False) + "\n")


def post_sidecar_path(output_dir: Path) -> Path:
    return output_dir / POST_SIDECAR_NAME


def load_post_sidecar(output_dir: Path) -> dict[str, Any] | None:
    path = post_sidecar_path(output_dir)
    if not path.exists():
        return None
    try:
        sidecar = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read post sidecar {path}: {exc}") from exc
    if not isinstance(sidecar, dict):
        raise RuntimeError(f"Post sidecar must be a JSON object: {path}")
    return sidecar


def new_post_sidecar(post_meta: dict[str, Any]) -> dict[str, Any]:
    timestamp = now_iso()
    return {
        "schema_version": POST_SIDECAR_SCHEMA_VERSION,
        "creator": {
            "id": str(post_meta.get("creator_id") or ""),
            "name": str(post_meta.get("creator_name") or ""),
        },
        "post": {
            "id": str(post_meta.get("post_id") or ""),
            "title": str(post_meta.get("post_title") or ""),
            "url": str(post_meta.get("post_url") or ""),
            "publish_time": int(post_meta.get("publish_time") or 0),
        },
        "assets": {},
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def validate_post_sidecar(sidecar: dict[str, Any], post_meta: dict[str, Any], path: Path) -> None:
    creator = sidecar.get("creator")
    post = sidecar.get("post")
    expected_post_id = str(post_meta.get("post_id") or "")
    expected_creator_id = str(post_meta.get("creator_id") or "")
    if sidecar.get("schema_version") != POST_SIDECAR_SCHEMA_VERSION:
        raise RuntimeError(f"Unsupported post sidecar schema: {path}")
    if not isinstance(creator, dict) or not isinstance(post, dict):
        raise RuntimeError(f"Post sidecar creator/post metadata must be objects: {path}")
    if str(post.get("id") or "") != expected_post_id:
        raise RuntimeError(f"Post sidecar ID mismatch: {path}")
    actual_creator_id = str(creator.get("id") or "")
    if expected_creator_id and actual_creator_id != expected_creator_id:
        raise RuntimeError(f"Post sidecar creator ID mismatch: {path}")
    if not isinstance(sidecar.get("assets"), dict):
        raise RuntimeError(f"Post sidecar assets must be an object: {path}")


def save_post_sidecar(output_dir: Path, sidecar: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = post_sidecar_path(output_dir)
    tmp = path.with_suffix(path.suffix + ".tmp")
    sidecar["updated_at"] = now_iso()
    tmp.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def claim_post_directory(
    output_dir: Path,
    post_meta: dict[str, Any],
    dry_run: bool,
    allow_nonempty_without_sidecar: bool = False,
) -> dict[str, Any] | None:
    sidecar = load_post_sidecar(output_dir)
    if sidecar is not None:
        path = post_sidecar_path(output_dir)
        validate_post_sidecar(sidecar, post_meta, path)
        sidecar["creator"]["name"] = str(post_meta.get("creator_name") or "")
        sidecar["post"].update(
            {
                "title": str(post_meta.get("post_title") or ""),
                "url": str(post_meta.get("post_url") or ""),
                "publish_time": int(post_meta.get("publish_time") or 0),
            }
        )
        if not dry_run:
            save_post_sidecar(output_dir, sidecar)
        return sidecar

    if dry_run:
        return None
    if output_dir.exists() and any(output_dir.iterdir()) and not allow_nonempty_without_sidecar:
        raise RuntimeError(
            f"Refusing to claim non-empty post directory without {POST_SIDECAR_NAME}: {output_dir}"
        )
    sidecar = new_post_sidecar(post_meta)
    save_post_sidecar(output_dir, sidecar)
    return sidecar


def safe_sidecar_asset_path(output_dir: Path, sidecar: dict[str, Any], asset_key: str) -> Path | None:
    entry = sidecar.get("assets", {}).get(asset_key)
    if not isinstance(entry, dict):
        return None
    relative_name = str(entry.get("file") or "")
    relative_path = Path(relative_name)
    if not relative_name or relative_path.is_absolute() or len(relative_path.parts) != 1:
        return None
    target = (output_dir / relative_path).resolve()
    if target.parent != output_dir.resolve() or not target.is_file():
        return None
    return target


def sidecar_path_is_claimed_by_other_asset(
    output_dir: Path,
    sidecar: dict[str, Any],
    asset_key: str,
    target: Path,
) -> bool:
    target_key = file_claim_key(target)
    for other_key in sidecar.get("assets", {}):
        if other_key == asset_key:
            continue
        other_path = safe_sidecar_asset_path(output_dir, sidecar, str(other_key))
        if other_path and file_claim_key(other_path) == target_key:
            return True
    return False


def record_sidecar_asset(
    output_dir: Path,
    sidecar: dict[str, Any] | None,
    asset_key: str,
    candidate: Candidate,
    record: dict[str, Any],
) -> None:
    if sidecar is None:
        return
    path_value = str(record.get("path") or "")
    if not path_value:
        return
    file_path = Path(path_value).resolve()
    if file_path.parent != output_dir.resolve() or not file_path.is_file():
        return
    sidecar.setdefault("assets", {})[asset_key] = {
        "identity_url": canonical_download_url(candidate.url),
        "asset_locator": candidate.asset_locator,
        "source": candidate.source,
        "filename_hint": candidate.filename_hint,
        "file": file_path.name,
        "bytes": record.get("bytes"),
        "recorded_at": now_iso(),
    }
    save_post_sidecar(output_dir, sidecar)


def candidate_filename_parts(candidate: Candidate) -> tuple[str, str]:
    stem = sanitize_filename(candidate.filename_hint, fallback="download")
    suffix = Path(unquote(urlparse(candidate.url).path)).suffix
    if suffix and len(suffix) <= 16:
        if stem.lower().endswith(suffix.lower()):
            stem = stem[: -len(suffix)]
        return stem, suffix
    return stem, ""


def candidate_name_signature(candidate: Candidate) -> tuple[str, str]:
    stem, suffix = candidate_filename_parts(candidate)
    return stem, suffix.lower()


def expected_filename_for_candidate(candidate: Candidate, duplicate_index: int) -> str | None:
    stem, suffix = candidate_filename_parts(candidate)
    if not suffix:
        return None
    if duplicate_index <= 0:
        return f"{stem}{suffix}"
    return f"{stem}-{duplicate_index}{suffix}"


def legacy_expected_filename_for_candidate(candidate: Candidate, duplicate_index: int) -> str | None:
    stem = sanitize_filename(candidate.filename_hint, fallback="download")
    suffix = Path(unquote(urlparse(candidate.url).path)).suffix
    if not suffix or len(suffix) > 16:
        return None
    if duplicate_index <= 0:
        return f"{stem}{suffix}"
    return f"{stem}-{duplicate_index}{suffix}"


def expected_candidate_paths(output_dir: Path, candidate: Candidate, duplicate_index: int) -> list[Path]:
    names = [
        expected_filename_for_candidate(candidate, duplicate_index),
        legacy_expected_filename_for_candidate(candidate, duplicate_index),
    ]
    paths: list[Path] = []
    seen_names: set[str] = set()
    for name in names:
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        paths.append(output_dir / name)
    return paths


def is_safe_direct_child_file(output_dir: Path, path: Path) -> bool:
    try:
        return path.is_file() and path.resolve().parent == output_dir.resolve()
    except (OSError, RuntimeError):
        return False


def legacy_directory_has_candidate_artifact(output_dir: Path, candidates: list[Candidate]) -> bool:
    if not output_dir.is_dir():
        return False
    duplicate_counts: dict[tuple[str, str], int] = {}
    for candidate in candidates:
        signature = candidate_name_signature(candidate)
        duplicate_index = duplicate_counts.get(signature, 0)
        duplicate_counts[signature] = duplicate_index + 1
        for path in expected_candidate_paths(output_dir, candidate, duplicate_index):
            if is_safe_direct_child_file(output_dir, path):
                return True
            part_path = path.with_suffix(path.suffix + ".part")
            if is_safe_direct_child_file(output_dir, part_path):
                return True
    return False


def existing_candidate_file(
    output_dir: Path,
    candidate: Candidate,
    duplicate_index: int,
    asset_key: str,
    sidecar: dict[str, Any] | None,
    allow_legacy_fallback: bool = False,
) -> Path | None:
    if not output_dir.exists():
        return None

    if sidecar is not None:
        recorded = safe_sidecar_asset_path(output_dir, sidecar, asset_key)
        if recorded:
            return recorded
    elif not allow_legacy_fallback:
        return None

    for exact in expected_candidate_paths(output_dir, candidate, duplicate_index):
        if not is_safe_direct_child_file(output_dir, exact):
            continue
        if sidecar is not None and sidecar_path_is_claimed_by_other_asset(
            output_dir,
            sidecar,
            asset_key,
            exact,
        ):
            continue
        return exact
    return None


def print_run_options(
    label: str,
    download_dir: Path,
    state_path: Path,
    config: dict[str, Any],
    manifest_path: Path,
    extra: dict[str, Any] | None = None,
) -> None:
    print(f"[{label}] download_dir={download_dir}")
    print(f"[{label}] state_file={state_path}")
    print(f"[{label}] manifest={manifest_path}")
    option_names = (
        "since",
        "until",
        "max_posts",
        "stop_post_id",
        "per_page",
        "skip_existing",
        "overwrite",
        "dry_run",
        "include_images",
        "incremental_scan",
        "incremental_lookback_posts",
        "incremental_full_scan_days",
    )
    options = {name: config.get(name) for name in option_names if config.get(name) not in (None, "")}
    if extra:
        options.update(extra)
    print(f"[{label}] options={json.dumps(options, ensure_ascii=False)}")


def compatible_state_entry(
    state: DownloadState,
    post_id: str,
    candidate: Candidate,
    key: str,
    v1_counts: Counter[str],
    v0_counts: Counter[str],
    legacy_url_counts: Counter[str],
    claimed_paths: set[str],
) -> tuple[str, dict[str, Any]] | None:
    exact = state.get_existing_entry(key)
    if exact:
        path = str(exact.get("path") or "")
        if path and file_claim_key(path) not in claimed_paths:
            return key, exact

    v1_key = download_key_v1(post_id, candidate)
    if v1_counts[v1_key] == 1:
        entry = state.get_existing_entry(v1_key)
        path = str(entry.get("path") or "") if entry else ""
        if (
            entry
            and path
            and file_claim_key(path) not in claimed_paths
            and not state.path_claimed_by_other_v2_asset(
                path,
                key,
                post_id,
                candidate.asset_locator,
            )
        ):
            return v1_key, entry

    v0_key = legacy_download_key(post_id, candidate)
    if v0_counts[v0_key] == 1:
        entry = state.get_existing_entry(v0_key)
        path = str(entry.get("path") or "") if entry else ""
        if (
            entry
            and path
            and file_claim_key(path) not in claimed_paths
            and not state.path_claimed_by_other_v2_asset(
                path,
                key,
                post_id,
                candidate.asset_locator,
            )
        ):
            return v0_key, entry

    if urlparse(canonical_download_url(candidate.url)).query:
        return None

    legacy_url = legacy_canonical_download_url(candidate.url)
    if legacy_url_counts[legacy_url] == 1:
        return state.find_legacy_entry_for_url(post_id, candidate, claimed_paths)
    return None


def download_candidates_for_post(
    session: requests.Session,
    candidates: list[Candidate],
    output_dir: Path,
    state: DownloadState,
    config: dict[str, Any],
    post_meta: dict[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    include_images = bool(config.get("include_images", False))
    overwrite = bool(config.get("overwrite", False))
    skip_existing = bool(config.get("skip_existing", True))
    dry_run = bool(config.get("dry_run", False))
    timeout = float(config.get("timeout", 60))
    duplicate_counts: dict[tuple[str, str], int] = {}
    post_id = str(post_meta.get("post_id") or "")
    v1_counts = Counter(download_key_v1(post_id, candidate) for candidate in candidates)
    v0_counts = Counter(legacy_download_key(post_id, candidate) for candidate in candidates)
    legacy_url_counts = Counter(legacy_canonical_download_url(candidate.url) for candidate in candidates)
    claimed_paths: set[str] = set()
    sidecar: dict[str, Any] | None = None
    sidecar_claimed = False
    legacy_fallback_allowed = state.has_post_file_in_directory(post_id, output_dir) or (
        legacy_directory_has_candidate_artifact(output_dir, candidates)
    )
    if (
        not post_sidecar_path(output_dir).exists()
        and output_dir.is_dir()
        and any(output_dir.iterdir())
        and not legacy_fallback_allowed
    ):
        collision_dir = collision_post_output_dir(output_dir, post_meta)
        print(f"[path-collision] preserving unknown directory {output_dir}; using {collision_dir}")
        output_dir = collision_dir
        legacy_fallback_allowed = state.has_post_file_in_directory(post_id, output_dir) or (
            legacy_directory_has_candidate_artifact(output_dir, candidates)
        )

    for candidate in candidates:
        signature = candidate_name_signature(candidate)
        duplicate_index = duplicate_counts.get(signature, 0)
        duplicate_counts[signature] = duplicate_index + 1

        key = download_key(post_id, candidate)
        compatible = compatible_state_entry(
            state,
            post_id,
            candidate,
            key,
            v1_counts,
            v0_counts,
            legacy_url_counts,
            claimed_paths,
        )
        matched_key, existing_entry = compatible if compatible else ("", None)
        if skip_existing and not overwrite and existing_entry:
            existing_path = str(existing_entry.get("path") or "")
            claimed_paths.add(file_claim_key(existing_path))
            if matched_key != key and not dry_run:
                state.alias(key, existing_entry, post_meta, candidate, matched_key)
                state.save()
            record = {
                "status": "already-downloaded",
                "key": key,
                "url": candidate.url,
                "asset_locator": candidate.asset_locator,
                "path": existing_entry.get("path"),
                **post_meta,
            }
            print(f"[skip] {post_meta.get('post_title')} ({key})")
            records.append(record)
            continue

        if not sidecar_claimed:
            try:
                sidecar = claim_post_directory(
                    output_dir,
                    post_meta,
                    dry_run=dry_run,
                    allow_nonempty_without_sidecar=legacy_fallback_allowed,
                )
            except Exception as exc:
                records.append({"status": "failed", "key": key, **post_meta, "error": str(exc)})
                return records
            sidecar_claimed = True

        existing_path = (
            None
            if overwrite or not skip_existing
            else existing_candidate_file(
                output_dir,
                candidate,
                duplicate_index,
                key,
                sidecar,
                allow_legacy_fallback=legacy_fallback_allowed,
            )
        )
        if existing_path and file_claim_key(existing_path) in claimed_paths:
            existing_path = None
        if existing_path:
            claimed_paths.add(file_claim_key(existing_path))
            record = {
                "status": "already-downloaded",
                "key": key,
                "url": candidate.url,
                "source": candidate.source,
                "filename_hint": candidate.filename_hint,
                "asset_locator": candidate.asset_locator,
                "path": str(existing_path),
                "bytes": existing_path.stat().st_size,
                **post_meta,
            }
            if not dry_run:
                record_sidecar_asset(output_dir, sidecar, key, candidate, record)
                state.mark(key, record)
                state.save()
            print(f"[skip-file] {existing_path}")
            records.append(record)
            continue

        if not dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
        record = download_candidate(
            session=session,
            candidate=candidate,
            output_dir=output_dir,
            include_images=include_images,
            overwrite=overwrite,
            dry_run=dry_run,
            timeout=timeout,
        )
        record.update({"key": key, "asset_locator": candidate.asset_locator, **post_meta})
        records.append(record)
        if record.get("status") == "downloaded":
            downloaded_path = str(record.get("path") or "")
            if downloaded_path:
                claimed_paths.add(file_claim_key(downloaded_path))
            record_sidecar_asset(output_dir, sidecar, key, candidate, record)
            state.mark(key, record)
            state.save()
    return records


def creator_effective_config(global_config: dict[str, Any], creator_config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(global_config)
    merged.update(creator_config)
    return merged


def post_output_dir(
    download_dir: Path,
    creator_name: str,
    publish_time: int,
    title: str,
    post_id: str,
    *,
    creator_id: str = "",
) -> Path:
    return (
        download_dir
        / creator_directory_name(creator_name, creator_id)
        / post_directory_name(publish_time, title, post_id)
    )


def collision_post_output_dir(preferred_dir: Path, post_meta: dict[str, Any]) -> Path:
    return preferred_dir.with_name(
        collision_directory_name(
            preferred_dir.name,
            str(post_meta.get("post_id") or ""),
        )
    )


def resolve_post_output_dir(preferred_dir: Path, post_meta: dict[str, Any]) -> Path:
    sidecar = load_post_sidecar(preferred_dir)
    if sidecar is None:
        return preferred_dir

    path = post_sidecar_path(preferred_dir)
    creator = sidecar.get("creator")
    post = sidecar.get("post")
    if (
        sidecar.get("schema_version") != POST_SIDECAR_SCHEMA_VERSION
        or not isinstance(creator, dict)
        or not isinstance(post, dict)
        or not isinstance(sidecar.get("assets"), dict)
    ):
        validate_post_sidecar(sidecar, post_meta, path)

    expected_post_id = str(post_meta.get("post_id") or "")
    expected_creator_id = str(post_meta.get("creator_id") or "")
    actual_post_id = str(post.get("id") or "")
    actual_creator_id = str(creator.get("id") or "")
    if actual_post_id == expected_post_id and (
        not expected_creator_id or actual_creator_id == expected_creator_id
    ):
        validate_post_sidecar(sidecar, post_meta, path)
        return preferred_dir

    collision_dir = collision_post_output_dir(preferred_dir, post_meta)
    collision_sidecar = load_post_sidecar(collision_dir)
    if collision_sidecar is not None:
        validate_post_sidecar(collision_sidecar, post_meta, post_sidecar_path(collision_dir))
    return collision_dir


def run_creator(config: dict[str, Any], config_dir: Path, creator_config: dict[str, Any]) -> list[dict[str, Any]]:
    effective = creator_effective_config(config, creator_config)
    effective.setdefault("incremental_scan", True)
    effective.setdefault("incremental_lookback_posts", 20)
    effective.setdefault("incremental_full_scan_days", 30)
    url = str(creator_config.get("url") or "").strip()
    if not url:
        return [{"status": "config-error", "error": "creator url is empty"}]

    incremental_value = effective.get("incremental_scan", True)
    if not isinstance(incremental_value, bool):
        return [{"status": "config-error", "error": "incremental_scan must be a JSON boolean"}]

    session = create_session_from_config(effective, config_dir)
    download_dir = resolve_config_path(config_dir, str(effective.get("download_dir") or ""), "downloads")
    state_path = resolve_state_path(config_dir, download_dir, str(effective.get("state_file") or ""))
    state = DownloadState(state_path)
    state.save()
    print_run_options(
        "config",
        download_dir,
        state_path,
        effective,
        download_dir / "manifest.jsonl",
        extra={"creator_url": normalize_ifdian_url(url)},
    )

    creator = get_creator_profile(session, normalize_ifdian_url(url))
    creator_id = str(creator["user_id"])
    creator_name = sanitize_filename(str(creator.get("name") or creator.get("url_slug") or creator_id), fallback=creator_id)
    creator_avatar = str(creator.get("avatar") or "")
    since_ts = parse_date_boundary(str(effective.get("since") or "") or None, end_of_day=False)
    until_ts = parse_date_boundary(str(effective.get("until") or "") or None, end_of_day=True)
    max_posts = int(effective.get("max_posts") or 0)
    stop_post_id = str(effective.get("stop_post_id") or "")
    per_page = int(effective.get("per_page") or 10)
    lookback_posts = max(0, int(effective.get("incremental_lookback_posts", 20)))
    full_scan_days = max(0, int(effective.get("incremental_full_scan_days", 30)))
    include_images = bool(effective.get("include_images", False))
    dry_run = bool(effective.get("dry_run", False))
    has_manual_boundary = bool(
        since_ts is not None or until_ts is not None or max_posts > 0 or stop_post_id
    )
    incremental_enabled = bool(
        incremental_value
        and not has_manual_boundary
        and bool(effective.get("skip_existing", True))
        and not bool(effective.get("overwrite", False))
    )
    checkpoint = state.get_creator_scan(creator_id) if incremental_enabled else None
    full_scan_due = creator_scan_requires_full_scan(checkpoint, full_scan_days, include_images)
    known_post_ids = (
        {str(item) for item in checkpoint.get("checkpoint_post_ids", []) if str(item).strip()}
        if checkpoint and not full_scan_due
        else set()
    )
    if incremental_value and not incremental_enabled:
        print("[incremental] disabled by manual boundaries, overwrite, or skip_existing=false")
    elif incremental_enabled:
        mode = "full" if not known_post_ids else "incremental"
        print(f"[incremental] mode={mode}, lookback_posts={lookback_posts}, full_scan_days={full_scan_days}")

    scan = scan_feed_posts_api(
        session=session,
        creator_user_id=creator_id,
        max_posts=max_posts,
        since_ts=since_ts,
        until_ts=until_ts,
        stop_post_id=stop_post_id,
        per_page=per_page,
        known_post_ids=known_post_ids,
        incremental_lookback=lookback_posts if known_post_ids else 0,
    )
    posts = list(scan.posts)
    feed_post_ids = {post.post_id for post in posts}
    retry_post_ids: set[str] = set()
    if incremental_enabled:
        for entry in state.inaccessible_posts_for_creator(creator_id):
            post_id = str(entry.get("post_id") or "")
            if not post_id or post_id in feed_post_ids:
                continue
            retry_post_ids.add(post_id)
            posts.append(
                FeedPost(
                    post_id=post_id,
                    title=str(entry.get("post_title") or post_id),
                    publish_time=int(entry.get("publish_time") or 0),
                    publish_sn="",
                    url=str(entry.get("post_url") or f"https://www.ifdian.net/p/{post_id}"),
                    raw={**entry, "incremental_retry": True},
                )
            )

    print(
        f"[creator] {creator_name} ({creator_id}): {len(feed_post_ids)} feed post(s), "
        f"{len(retry_post_ids)} inaccessible retry post(s); stop={scan.stop_reason or 'unknown'}"
    )
    records: list[dict[str, Any]] = []
    inaccessible_to_notify: list[dict[str, Any]] = []
    downloaded = 0
    checkpoint_processing_failed = False
    for post in posts:
        is_feed_post = post.post_id in feed_post_ids
        post_meta = {
            "creator_id": creator_id,
            "creator_name": creator_name,
            "post_id": post.post_id,
            "post_title": post.title,
            "post_url": post.url,
            "publish_time": post.publish_time,
            "publish_date": format_publish_date(post.publish_time),
        }
        restored_entry = state.get_inaccessible_post(post.post_id)
        try:
            detail = fetch_post_detail_api(session, post.post_id)
            title = post_title_from_api(detail)
            post_meta["post_title"] = title
            post_meta["publish_time"] = int(detail.get("publish_time") or post.publish_time)
            post_meta["publish_date"] = format_publish_date(int(post_meta["publish_time"]))
            has_right = detail.get("has_right")
            if has_right in {0, False}:
                default_error = "current account has no right to this post"
                error = str(detail.get("has_right_errMsg") or default_error)
                access_requirement = access_requirement_from_api(
                    detail,
                    post.raw,
                    fallback="" if error == default_error else error,
                )
                already_notified = state.is_inaccessible_notified(post.post_id)
                record = {
                    "status": "no-right",
                    "error": error,
                    "access_requirement": access_requirement,
                    "notification_status": (
                        "dry-run" if dry_run else "already-sent" if already_notified else "pending"
                    ),
                    **post_meta,
                }
                records.append(record)
                if dry_run:
                    print(f"[no-right] {title} (dry-run; state unchanged)")
                else:
                    state.mark_inaccessible(post_meta, error=error, access_requirement=access_requirement)
                    state.save()
                if not dry_run and already_notified:
                    print(f"[no-right] {title} (notification already sent)")
                elif not dry_run:
                    print(f"[no-right] {title} (queued notification; required={access_requirement})")
                    inaccessible_to_notify.append(record)
                continue
            if restored_entry:
                print(f"[access-restored] {title}: access confirmed; checking downloads before clearing state")
            candidates = candidates_from_post_detail(detail, include_images=include_images)
        except Exception as exc:
            records.append({"status": "detail-failed", **post_meta, "error": str(exc)})
            if is_feed_post:
                checkpoint_processing_failed = True
            continue

        if not candidates:
            records.append({"status": "no-files", **post_meta})
            if restored_entry and not dry_run:
                state.clear_inaccessible(post.post_id)
                state.save()
                print(f"[access-restored] {title}: no downloadable files; retry state cleared")
            continue

        post_dir = resolve_post_output_dir(
            post_output_dir(
                download_dir,
                creator_name,
                post.publish_time,
                str(post_meta["post_title"]),
                post.post_id,
                creator_id=creator_id,
            ),
            post_meta,
        )
        post_records = download_candidates_for_post(session, candidates, post_dir, state, effective, post_meta)
        downloaded += sum(1 for record in post_records if record.get("status") == "downloaded")
        records.extend(post_records)
        post_statuses = {str(record.get("status") or "") for record in post_records}
        if is_feed_post and post_statuses & {"failed", "skipped", "detail-failed"}:
            checkpoint_processing_failed = True
        if (
            restored_entry
            and not dry_run
            and post_statuses
            and post_statuses <= {"downloaded", "already-downloaded"}
        ):
            state.clear_inaccessible(post.post_id)
            state.save()
            print(f"[access-restored] {title}: downloads verified; retry state cleared")

    state.save()
    manifest_path = download_dir / "manifest.jsonl"
    if inaccessible_to_notify:
        notification_sent = bark_notify(
            effective,
            f"{creator_name} 有新投稿无法下载",
            bark_inaccessible_body(inaccessible_to_notify),
            icon=creator_avatar,
            url=str(inaccessible_to_notify[0].get("post_url") or "") if len(inaccessible_to_notify) == 1 else normalize_ifdian_url(url),
        )
        if notification_sent:
            for record in inaccessible_to_notify:
                state.mark_inaccessible_notified(str(record.get("post_id") or ""))
                record["notification_status"] = "sent"
            state.save()
    append_manifest(manifest_path, records)
    can_advance_checkpoint = bool(
        incremental_enabled
        and not dry_run
        and scan.checkpoint_safe
        and not checkpoint_processing_failed
        and scan.checkpoint_post_ids
    )
    if can_advance_checkpoint:
        state.mark_creator_scan(
            creator_id,
            scan.checkpoint_post_ids,
            scan.checkpoint_publish_time,
            full_scan=not bool(known_post_ids),
            include_images=include_images,
        )
        state.save()
        print(
            f"[incremental] checkpoint advanced to {scan.checkpoint_post_ids[0]} "
            f"({len(scan.checkpoint_post_ids)} recent id(s))"
        )
    elif incremental_enabled:
        reason = "dry-run" if dry_run else "post-processing failure" if checkpoint_processing_failed else scan.stop_reason
        print(f"[incremental] checkpoint unchanged: {reason or 'scan incomplete'}")
    if downloaded:
        bark_notify(
            effective,
            f"{creator_name} 下载完成",
            bark_download_body(downloaded, records),
            icon=creator_avatar,
            url=normalize_ifdian_url(url),
        )
    return records


def run_single_post(config: dict[str, Any], config_dir: Path, post_url: str) -> list[dict[str, Any]]:
    session = create_session_from_config(config, config_dir)
    post_id = post_id_from_url(post_url)
    if not post_id:
        return [{"status": "config-error", "url": post_url, "error": "not a /p/<post_id> URL"}]

    download_dir = resolve_config_path(config_dir, str(config.get("download_dir") or ""), "downloads")
    state_path = resolve_state_path(config_dir, download_dir, str(config.get("state_file") or ""))
    state = DownloadState(state_path)
    state.save()
    print_run_options(
        "config",
        download_dir,
        state_path,
        config,
        download_dir / "manifest.jsonl",
        extra={"post_url": normalize_ifdian_url(post_url)},
    )
    detail = fetch_post_detail_api(session, post_id)
    title = post_title_from_api(detail)
    creator = detail.get("user") if isinstance(detail.get("user"), dict) else {}
    creator_id = str(detail.get("user_id") or creator.get("user_id") or "")
    creator_name = sanitize_filename(
        str(creator.get("name") or creator_id or "creator"),
        fallback="creator",
    )
    creator_avatar = str(creator.get("avatar") or "")
    candidates = candidates_from_post_detail(detail, include_images=bool(config.get("include_images", False)))

    post_meta = {
        "creator_id": creator_id,
        "creator_name": creator_name,
        "post_id": post_id,
        "post_title": title,
        "post_url": normalize_ifdian_url(post_url),
        "publish_time": int(detail.get("publish_time") or 0),
        "publish_date": format_publish_date(int(detail.get("publish_time") or 0)),
    }
    if not candidates:
        records = [{"status": "no-files", **post_meta}]
    else:
        post_dir = resolve_post_output_dir(
            post_output_dir(
                download_dir,
                creator_name,
                int(detail.get("publish_time") or 0),
                title,
                post_id,
                creator_id=creator_id,
            ),
            post_meta,
        )
        records = download_candidates_for_post(session, candidates, post_dir, state, config, post_meta)

    state.save()
    append_manifest(download_dir / "manifest.jsonl", records)
    downloaded = sum(1 for record in records if record.get("status") == "downloaded")
    if downloaded:
        bark_notify(config, "单帖下载完成", bark_download_body(downloaded, records), icon=creator_avatar, url=normalize_ifdian_url(post_url))
    return records
