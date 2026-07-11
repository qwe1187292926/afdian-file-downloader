from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode, urlparse, urlunparse

import requests

from afdian_downloader import (
    Candidate,
    add_cookie_text,
    candidates_from_post_detail,
    create_base_session,
    download_candidate,
    fetch_post_detail_api,
    format_publish_date,
    get_creator_profile,
    iter_feed_posts_api,
    normalize_ifdian_url,
    parse_date_boundary,
    post_id_from_url,
    post_title_from_api,
    sanitize_filename,
    slug_from_text,
)


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
    parsed = urlparse(url)
    return urlunparse(parsed._replace(query="", fragment=""))


def download_key(post_id: str, candidate: Candidate) -> str:
    source = "|".join(
        [
            post_id,
            canonical_download_url(candidate.url),
            candidate.filename_hint,
        ]
    )
    return hashlib.sha1(source.encode("utf-8")).hexdigest()


def legacy_download_key(post_id: str, candidate: Candidate) -> str:
    source = "|".join(
        [
            post_id,
            candidate.source,
            canonical_download_url(candidate.url),
            candidate.filename_hint,
        ]
    )
    return hashlib.sha1(source.encode("utf-8")).hexdigest()


class DownloadState:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"downloads": {}}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except json.JSONDecodeError:
                backup = path.with_suffix(path.suffix + ".broken")
                path.replace(backup)
        self.data.setdefault("downloads", {})
        self.data.setdefault("inaccessible_posts", {})

    def has(self, key: str) -> bool:
        entry = self.data.get("downloads", {}).get(key)
        if not isinstance(entry, dict):
            return False
        file_path = entry.get("path")
        return bool(file_path and Path(file_path).exists())

    def get_existing_entry(self, key: str) -> dict[str, Any] | None:
        entry = self.data.get("downloads", {}).get(key)
        if not isinstance(entry, dict):
            return None
        file_path = entry.get("path")
        if file_path and Path(file_path).exists():
            return entry
        return None

    def alias(self, new_key: str, existing_entry: dict[str, Any], post_meta: dict[str, Any]) -> None:
        entry = dict(existing_entry)
        entry.setdefault("post_id", post_meta.get("post_id"))
        entry.setdefault("post_title", post_meta.get("post_title"))
        entry["aliased_at"] = now_iso()
        self.data.setdefault("downloads", {})[new_key] = entry

    def mark(self, key: str, record: dict[str, Any]) -> None:
        self.data.setdefault("downloads", {})[key] = {
            "path": record.get("path"),
            "bytes": record.get("bytes"),
            "post_id": record.get("post_id"),
            "post_title": record.get("post_title"),
            "url": canonical_download_url(str(record.get("url") or "")),
            "downloaded_at": now_iso(),
        }

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

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


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


def candidate_filename_parts(candidate: Candidate) -> tuple[str, str]:
    stem = sanitize_filename(candidate.filename_hint, fallback="download")
    suffix = Path(unquote(urlparse(candidate.url).path)).suffix
    if suffix and len(suffix) <= 16:
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


def existing_candidate_file(output_dir: Path, candidate: Candidate, duplicate_index: int) -> Path | None:
    if not output_dir.exists():
        return None

    expected_name = expected_filename_for_candidate(candidate, duplicate_index)
    if not expected_name:
        return None

    exact = output_dir / expected_name
    if exact.is_file():
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
    )
    options = {name: config.get(name) for name in option_names if config.get(name) not in (None, "")}
    if extra:
        options.update(extra)
    print(f"[{label}] options={json.dumps(options, ensure_ascii=False)}")


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

    for candidate in candidates:
        signature = candidate_name_signature(candidate)
        duplicate_index = duplicate_counts.get(signature, 0)
        duplicate_counts[signature] = duplicate_index + 1

        key = download_key(str(post_meta.get("post_id") or ""), candidate)
        legacy_key = legacy_download_key(str(post_meta.get("post_id") or ""), candidate)
        existing_entry = state.get_existing_entry(key) or state.get_existing_entry(legacy_key)
        if skip_existing and not overwrite and existing_entry:
            if not state.get_existing_entry(key):
                state.alias(key, existing_entry, post_meta)
                state.save()
            record = {
                "status": "already-downloaded",
                "key": key,
                "url": candidate.url,
                "path": existing_entry.get("path"),
                **post_meta,
            }
            print(f"[skip] {post_meta.get('post_title')} ({key})")
            records.append(record)
            continue

        existing_path = None if overwrite or not skip_existing else existing_candidate_file(output_dir, candidate, duplicate_index)
        if existing_path:
            record = {
                "status": "already-downloaded",
                "key": key,
                "url": candidate.url,
                "source": candidate.source,
                "filename_hint": candidate.filename_hint,
                "path": str(existing_path),
                "bytes": existing_path.stat().st_size,
                **post_meta,
            }
            state.mark(key, record)
            state.save()
            print(f"[skip-file] {existing_path}")
            records.append(record)
            continue

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
        record.update({"key": key, **post_meta})
        records.append(record)
        if record.get("status") == "downloaded":
            state.mark(key, record)
            state.save()
    return records


def creator_effective_config(global_config: dict[str, Any], creator_config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(global_config)
    merged.update(creator_config)
    return merged


def post_output_dir(download_dir: Path, creator_name: str, publish_time: int, title: str, post_id: str) -> Path:
    dirname = slug_from_text(f"{format_publish_date(publish_time)} {title}", fallback=post_id[:12] or "post")
    return download_dir / sanitize_filename(creator_name, fallback="creator") / dirname


def run_creator(config: dict[str, Any], config_dir: Path, creator_config: dict[str, Any]) -> list[dict[str, Any]]:
    effective = creator_effective_config(config, creator_config)
    url = str(creator_config.get("url") or "").strip()
    if not url:
        return [{"status": "config-error", "error": "creator url is empty"}]

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
    posts = iter_feed_posts_api(
        session=session,
        creator_user_id=creator_id,
        max_posts=int(effective.get("max_posts") or 0),
        since_ts=since_ts,
        until_ts=until_ts,
        stop_post_id=str(effective.get("stop_post_id") or ""),
        per_page=int(effective.get("per_page") or 10),
    )

    print(f"[creator] {creator_name} ({creator_id}): {len(posts)} post(s)")
    records: list[dict[str, Any]] = []
    inaccessible_to_notify: list[dict[str, Any]] = []
    downloaded = 0
    for post in posts:
        post_meta = {
            "creator_id": creator_id,
            "creator_name": creator_name,
            "post_id": post.post_id,
            "post_title": post.title,
            "post_url": post.url,
            "publish_time": post.publish_time,
            "publish_date": format_publish_date(post.publish_time),
        }
        try:
            detail = fetch_post_detail_api(session, post.post_id)
            title = post_title_from_api(detail)
            post_meta["post_title"] = title
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
                    "notification_status": "already-sent" if already_notified else "pending",
                    **post_meta,
                }
                records.append(record)
                state.mark_inaccessible(post_meta, error=error, access_requirement=access_requirement)
                state.save()
                if already_notified:
                    print(f"[no-right] {title} (notification already sent)")
                else:
                    print(f"[no-right] {title} (queued notification; required={access_requirement})")
                    inaccessible_to_notify.append(record)
                continue
            restored_entry = state.clear_inaccessible(post.post_id)
            if restored_entry:
                state.save()
                print(f"[access-restored] {title}: previous no-right state cleared; checking downloads")
            candidates = candidates_from_post_detail(detail, include_images=bool(effective.get("include_images", False)))
        except Exception as exc:
            records.append({"status": "detail-failed", **post_meta, "error": str(exc)})
            continue

        if not candidates:
            records.append({"status": "no-files", **post_meta})
            continue

        post_dir = post_output_dir(download_dir, creator_name, post.publish_time, str(post_meta["post_title"]), post.post_id)
        post_records = download_candidates_for_post(session, candidates, post_dir, state, effective, post_meta)
        downloaded += sum(1 for record in post_records if record.get("status") == "downloaded")
        records.extend(post_records)

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
    creator_name = sanitize_filename(str(creator.get("name") or detail.get("user_id") or "creator"), fallback="creator")
    creator_avatar = str(creator.get("avatar") or "")
    candidates = candidates_from_post_detail(detail, include_images=bool(config.get("include_images", False)))

    post_meta = {
        "creator_id": str(detail.get("user_id") or ""),
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
        post_dir = post_output_dir(download_dir, creator_name, int(detail.get("publish_time") or 0), title, post_id)
        records = download_candidates_for_post(session, candidates, post_dir, state, config, post_meta)

    state.save()
    append_manifest(download_dir / "manifest.jsonl", records)
    downloaded = sum(1 for record in records if record.get("status") == "downloaded")
    if downloaded:
        bark_notify(config, "单帖下载完成", bark_download_body(downloaded, records), icon=creator_avatar, url=normalize_ifdian_url(post_url))
    return records
