from __future__ import annotations

import argparse
import html
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlunparse, unquote

import requests
try:
    from playwright.sync_api import (
        BrowserContext,
        Page,
        TimeoutError as PlaywrightTimeoutError,
        sync_playwright,
    )
except ModuleNotFoundError:
    BrowserContext = object
    Page = object
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None


AFDIAN_HOME = "https://ifdian.net/"
AFDIAN_LOGIN_URLS = (
    "https://ifdian.net/",
    "https://afdian.com/",
    "https://afdian.net/",
)
DEFAULT_PROFILE = Path("browser-profile")
DEFAULT_DOWNLOAD_DIR = Path("downloads")
DEFAULT_TIMEOUT_MS = 30_000
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

TEXT_BUTTON_RE = re.compile(r"(下载|附件|download|attachment|file)", re.I)
URL_RE = re.compile(r"https?:\\?/\\?/[^\"'\\\s<>]+", re.I)

IMAGE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
}

FILE_EXTENSIONS = {
    ".7z",
    ".apk",
    ".avi",
    ".blend",
    ".csv",
    ".doc",
    ".docx",
    ".dmg",
    ".epub",
    ".exe",
    ".flac",
    ".gz",
    ".iso",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".msi",
    ".ogg",
    ".pdf",
    ".psd",
    ".rar",
    ".tar",
    ".txt",
    ".wav",
    ".webm",
    ".xls",
    ".xlsx",
    ".yaml",
    ".yml",
    ".zip",
}

WEB_ASSET_EXTENSIONS = {
    ".css",
    ".eot",
    ".ico",
    ".js",
    ".map",
    ".otf",
    ".ttf",
    ".wasm",
    ".woff",
    ".woff2",
}

ATTACHMENT_HOST_HINTS = (
    "afdiancdn.com",
    "ifdian.net",
    "afdian.net",
    "afdian.com",
)


@dataclass(frozen=True)
class Candidate:
    url: str
    source: str
    text: str = ""
    referer: str = ""
    filename_hint: str = ""
    asset_locator: str = ""


@dataclass(frozen=True)
class FeedPost:
    post_id: str
    title: str
    publish_time: int
    publish_sn: str
    url: str
    raw: dict[str, object]


@dataclass(frozen=True)
class FeedScanResult:
    posts: list[FeedPost]
    checkpoint_post_ids: list[str]
    checkpoint_publish_time: int
    incremental_boundary_reached: bool
    checkpoint_safe: bool
    stop_reason: str


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def sanitize_filename(name: str, fallback: str = "download") -> str:
    name = unquote(name or "").strip().strip(".")
    if not name:
        name = fallback
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        name = fallback
        name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", name)
        name = re.sub(r"\s+", " ", name).strip()
    if len(name) > 180:
        stem, suffix = os.path.splitext(name)
        name = stem[: 180 - len(suffix)] + suffix
    return name


def slug_from_text(text: str, fallback: str) -> str:
    text = sanitize_filename(text, fallback=fallback)
    text = re.sub(r"\s+", "-", text)
    return text[:80] or fallback


def stable_id_component(value: str, fallback: str) -> str:
    raw = str(value or "").strip()
    identity = raw or fallback
    prefix = slug_from_text(identity, fallback=fallback)[:24]
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def clean_post_title(title: str) -> str:
    title = html.unescape(title or "").strip()
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"\s*[-|]\s*爱发电.*$", "", title)
    if "丨" in title:
        parts = [part.strip() for part in title.split("丨") if part.strip()]
        if parts:
            title = parts[0]
    return sanitize_filename(title, fallback="")


def normalize_ifdian_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.lower() in {"ifdian.net", "www.ifdian.net"}:
        return urlunparse(parsed._replace(scheme="https", netloc="www.ifdian.net"))
    return url


def ifdian_api_url(path: str, params: dict[str, object] | None = None) -> str:
    query = urlencode({k: "" if v is None else v for k, v in (params or {}).items()})
    return f"https://www.ifdian.net{path}" + (f"?{query}" if query else "")


def creator_slug_from_url(url: str) -> str:
    parsed = urlparse(normalize_ifdian_url(url))
    match = re.search(r"/a/([^/?#]+)", parsed.path)
    if not match:
        raise ValueError(f"Creator feed URL must look like https://www.ifdian.net/a/<slug>?tab=feed: {url}")
    return unquote(match.group(1))


def post_id_from_url(url: str) -> str | None:
    parsed = urlparse(normalize_ifdian_url(url))
    match = re.search(r"/p/([^/?#]+)", parsed.path)
    return unquote(match.group(1)) if match else None


def parse_date_boundary(value: str | None, end_of_day: bool = False) -> int | None:
    if not value:
        return None
    value = value.strip()
    date_format = "%Y%m%d" if re.fullmatch(r"\d{8}", value) else "%Y-%m-%d"
    date = datetime.strptime(value, date_format).date()
    clock = dt_time.max if end_of_day else dt_time.min
    tz = timezone(timedelta(hours=8))
    return int(datetime.combine(date, clock, tzinfo=tz).timestamp())


def format_publish_date(timestamp: int) -> str:
    if not timestamp:
        return "unknown-date"
    tz = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(timestamp, tz=tz).strftime("%Y-%m-%d")


def creator_directory_name(creator_name: str, creator_id: str) -> str:
    del creator_name
    identity = stable_id_component(creator_id, fallback="unknown-creator")
    return f"creator-{identity}"


def post_directory_name(publish_time: int, title: str, post_id: str) -> str:
    del publish_time, title
    identity = stable_id_component(post_id, fallback="unknown-post")
    return f"post-{identity}"


def signed_query_keys_to_remove(query_items: list[tuple[str, str]]) -> set[str]:
    keys = {key.lower() for key, _value in query_items}
    removable: set[str] = set()
    aws_required = {
        "x-amz-algorithm",
        "x-amz-credential",
        "x-amz-date",
        "x-amz-expires",
        "x-amz-signedheaders",
        "x-amz-signature",
    }
    google_required = {
        "x-goog-algorithm",
        "x-goog-credential",
        "x-goog-date",
        "x-goog-expires",
        "x-goog-signedheaders",
        "x-goog-signature",
    }
    cos_required = {
        "q-sign-algorithm",
        "q-ak",
        "q-sign-time",
        "q-key-time",
        "q-header-list",
        "q-url-param-list",
        "q-signature",
    }
    if aws_required <= keys:
        removable.update(key for key in keys if key.startswith("x-amz-"))
    if google_required <= keys:
        removable.update(key for key in keys if key.startswith("x-goog-"))
    if cos_required <= keys:
        removable.update(
            key
            for key in keys
            if key.startswith("q-sign-")
            or key in {"q-ak", "q-key-time", "q-header-list", "q-url-param-list"}
        )
    if {"ossaccesskeyid", "expires", "signature"} <= keys:
        removable.update(
            {
                "ossaccesskeyid",
                "signature",
                "expires",
                "security-token",
                "x-oss-security-token",
            }
            & keys
        )
    if {"signature", "key-pair-id"} <= keys and ({"expires", "policy"} & keys):
        removable.update({"signature", "key-pair-id", "policy", "expires"} & keys)
    return removable


def canonical_candidate_url(url: str) -> str:
    parsed = urlparse(url)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    removable_keys = signed_query_keys_to_remove(query_items)
    stable_query = [
        (key, value)
        for key, value in query_items
        if key.lower() not in removable_keys
    ]
    return urlunparse(
        parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            query=urlencode(stable_query),
            fragment="",
        )
    )


def candidate_identity(candidate: Candidate) -> str:
    canonical_url = canonical_candidate_url(candidate.url)
    locator = candidate.asset_locator.strip()
    if locator:
        return f"locator:{locator}|url:{canonical_url}"
    return f"url:{canonical_url}"


def api_get_json(session: requests.Session, path: str, params: dict[str, object] | None = None) -> dict[str, object]:
    url = ifdian_api_url(path, params)
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            break
        except Exception as exc:
            last_error = exc
            if attempt == 3:
                raise
            time.sleep(attempt)
    else:
        raise RuntimeError(f"Ifdian API request failed: {last_error}")
    if data.get("ec") != 200:
        raise RuntimeError(f"Ifdian API error {data.get('ec')}: {data.get('em')} ({url})")
    return data


def normalize_url(raw_url: str, base_url: str) -> str | None:
    if not raw_url:
        return None
    raw_url = html.unescape(raw_url.strip())
    if raw_url.startswith(("data:", "blob:", "javascript:", "mailto:", "tel:")):
        return None
    try:
        return urljoin(base_url, raw_url)
    except ValueError:
        return None


def url_extension(url: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path)
    ext = Path(path).suffix.lower()
    if ext:
        return ext
    qs = parse_qs(parsed.query)
    for key in ("filename", "file", "name", "download"):
        for value in qs.get(key, []):
            ext = Path(unquote(value)).suffix.lower()
            if ext:
                return ext
    return ""


def is_site_asset_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = unquote(parsed.path).lower()
    ext = Path(path).suffix.lower()

    if ext in WEB_ASSET_EXTENSIONS:
        return True
    if path.endswith("/manifest.json") or path.endswith("/site.webmanifest"):
        return True
    if "iconfont" in path:
        return True
    if "static.afdiancdn.com" in host and path.startswith(("/static/", "/fonts/", "/assets/")):
        return True
    return False


def is_obvious_non_file_link(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/").lower()
    if is_afdian_host(url) and path in {"", "/app", "/login", "/register"}:
        return True
    return False


def has_attachment_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(hint in host for hint in ATTACHMENT_HOST_HINTS)


def is_afdian_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(hint in host for hint in ("ifdian.net", "afdian.net", "afdian.com"))


def goto_first_available(page: Page, urls: Iterable[str], timeout_ms: int = DEFAULT_TIMEOUT_MS) -> str | None:
    last_error: Exception | None = None
    for url in urls:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return url
        except Exception as exc:
            last_error = exc
            eprint(f"Could not open {url}: {exc}")
    if last_error:
        eprint("All configured Afdian login URLs failed. Keeping the browser open for manual navigation.")
    return None


def require_playwright():
    if sync_playwright is None:
        raise SystemExit("当前功能需要 Playwright，但服务器精简模式未安装。日常下载请使用 download_creators.py / download_post.py 的 Cookie API 模式。")
    return sync_playwright


def looks_like_download_url(url: str, include_images: bool, headers: dict[str, str] | None = None) -> bool:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    disposition = headers.get("content-disposition", "")
    content_type = headers.get("content-type", "").split(";")[0].strip().lower()

    if is_site_asset_url(url) and "attachment" not in disposition.lower():
        return False

    if "attachment" in disposition.lower():
        return True

    ext = url_extension(url)
    if ext in FILE_EXTENSIONS:
        return True
    if include_images and ext in IMAGE_EXTENSIONS:
        return True

    if content_type:
        if content_type in {"text/html", "application/json", "text/plain"}:
            return False
        if content_type.startswith(("audio/", "video/")):
            return True
        if include_images and content_type.startswith("image/"):
            return True
        if content_type in {
            "application/octet-stream",
            "application/pdf",
            "application/zip",
            "application/x-7z-compressed",
            "application/x-rar-compressed",
        }:
            return True

    return False


def dedupe_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    result: list[Candidate] = []
    for candidate in candidates:
        url = candidate.url.split("#", 1)[0]
        normalized = Candidate(
            url=url,
            source=candidate.source,
            text=candidate.text,
            referer=candidate.referer,
            filename_hint=candidate.filename_hint,
            asset_locator=candidate.asset_locator,
        )
        key = candidate_identity(normalized)
        if not url or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def with_filename_hint(candidates: Iterable[Candidate], filename_hint: str) -> list[Candidate]:
    hint = sanitize_filename(filename_hint, fallback="")
    result: list[Candidate] = []
    for candidate in candidates:
        result.append(
            Candidate(
                url=candidate.url,
                source=candidate.source,
                text=candidate.text,
                referer=candidate.referer,
                filename_hint=candidate.filename_hint or hint,
                asset_locator=candidate.asset_locator,
            )
        )
    return result


def cookies_to_session(context: BrowserContext) -> requests.Session:
    session = create_base_session()
    for cookie in context.cookies():
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )
    return session


def create_base_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    return session


def add_cookie_string(session: requests.Session, cookie_string: str) -> None:
    cookie_string = cookie_string.strip()
    if not cookie_string:
        return
    for part in cookie_string.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        session.cookies.set(name, value, domain=".ifdian.net", path="/")
        session.cookies.set(name, value, domain=".www.ifdian.net", path="/")


def add_cookie_json(session: requests.Session, value: object) -> bool:
    if isinstance(value, dict):
        for name, cookie_value in value.items():
            if isinstance(cookie_value, dict):
                session.cookies.set(
                    str(cookie_value.get("name") or name),
                    str(cookie_value.get("value") or ""),
                    domain=str(cookie_value.get("domain") or ".ifdian.net"),
                    path=str(cookie_value.get("path") or "/"),
                )
            else:
                session.cookies.set(str(name), str(cookie_value), domain=".ifdian.net", path="/")
                session.cookies.set(str(name), str(cookie_value), domain=".www.ifdian.net", path="/")
        return True

    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict) or "name" not in item:
                continue
            session.cookies.set(
                str(item.get("name") or ""),
                str(item.get("value") or ""),
                domain=str(item.get("domain") or ".ifdian.net"),
                path=str(item.get("path") or "/"),
            )
        return True

    return False


def add_netscape_cookie_lines(session: requests.Session, text: str) -> bool:
    loaded = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            return False
        domain, _flag, path, _secure, _expires, name, value = parts[:7]
        session.cookies.set(name, value, domain=domain, path=path or "/")
        loaded = True
    return loaded


def add_cookie_text(session: requests.Session, text: str) -> None:
    text = text.strip()
    if not text:
        return
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = None
    if value is not None and add_cookie_json(session, value):
        return
    if "\t" in text and add_netscape_cookie_lines(session, text):
        return
    add_cookie_string(session, text)


def session_from_cookie_args(args: argparse.Namespace) -> requests.Session | None:
    cookie_values: list[str] = []
    cookie_arg = getattr(args, "cookie", None)
    if cookie_arg:
        cookie_values.extend(cookie_arg)
    cookie_file = getattr(args, "cookie_file", None)
    if cookie_file:
        cookie_values.append(Path(cookie_file).read_text(encoding="utf-8"))
    env_cookie = os.getenv("IFDIAN_COOKIE") or os.getenv("AFDIAN_COOKIE")
    if env_cookie and not cookie_values:
        cookie_values.append(env_cookie)
    if not cookie_values:
        return None

    session = create_base_session()
    for value in cookie_values:
        add_cookie_text(session, value)
    return session


def get_creator_profile(session: requests.Session, creator_url: str) -> dict[str, object]:
    slug = creator_slug_from_url(creator_url)
    data = api_get_json(session, "/api/user/get-profile-by-slug", {"url_slug": slug})
    user = (data.get("data") or {}).get("user") or {}
    if not isinstance(user, dict) or not user.get("user_id"):
        raise RuntimeError(f"Could not resolve creator profile for {creator_url}")
    return user


def post_title_from_api(post: dict[str, object]) -> str:
    title = clean_post_title(str(post.get("title") or ""))
    if title:
        return title
    content = re.sub(r"<[^>]+>", "", str(post.get("content") or post.get("preview_text") or "")).strip()
    content = re.sub(r"\s+", " ", html.unescape(content))
    if content:
        return sanitize_filename(content[:60], fallback="")
    return sanitize_filename(str(post.get("post_id") or "post"), fallback="post")


def feed_post_from_api(raw: dict[str, object]) -> FeedPost:
    post_id = str(raw.get("post_id") or "")
    publish_time = int(raw.get("publish_time") or 0)
    publish_sn = str(raw.get("publish_sn") or "")
    return FeedPost(
        post_id=post_id,
        title=post_title_from_api(raw),
        publish_time=publish_time,
        publish_sn=publish_sn,
        url=f"https://www.ifdian.net/p/{post_id}",
        raw=raw,
    )


def scan_feed_posts_api(
    session: requests.Session,
    creator_user_id: str,
    max_posts: int,
    since_ts: int | None,
    until_ts: int | None,
    stop_post_id: str,
    per_page: int,
    known_post_ids: set[str] | None = None,
    incremental_lookback: int = 0,
    checkpoint_id_limit: int = 50,
) -> FeedScanResult:
    posts: list[FeedPost] = []
    publish_sn = ""
    seen_ids: set[str] = set()
    known_ids = set(known_post_ids or ())
    checkpoint_post_times: dict[str, int] = {}
    incremental_boundary_reached = False
    lookback_remaining = 0
    checkpoint_safe = False
    stop_reason = ""

    while True:
        data = api_get_json(
            session,
            "/api/post/get-list",
            {
                "user_id": creator_user_id,
                "type": "old",
                "publish_sn": publish_sn,
                "per_page": per_page,
                "group_id": "",
                "all": 1,
                "is_public": "",
                "plan_id": "",
                "title": "",
                "name": "",
            },
        )
        payload = data.get("data") or {}
        raw_list = payload.get("list") if isinstance(payload, dict) else []
        if not isinstance(raw_list, list):
            raise RuntimeError("Ifdian feed API returned a non-list data.list payload")
        if not raw_list:
            checkpoint_safe = True
            stop_reason = "feed-exhausted"
            break

        oldest_seen_sn = ""
        stop_due_to_boundary = False
        new_ids_on_page = 0
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            post = feed_post_from_api(raw)
            if not post.post_id or post.post_id in seen_ids:
                continue
            seen_ids.add(post.post_id)
            new_ids_on_page += 1
            oldest_seen_sn = post.publish_sn or oldest_seen_sn

            if stop_post_id and post.post_id == stop_post_id:
                stop_due_to_boundary = True
                checkpoint_safe = True
                stop_reason = "configured-stop-post"
                break
            if until_ts is not None and post.publish_time > until_ts:
                continue
            if since_ts is not None and post.publish_time < since_ts:
                if raw.get("user_top"):
                    continue
                stop_due_to_boundary = True
                checkpoint_safe = True
                stop_reason = "since-boundary"
                break

            is_pinned = bool(raw.get("user_top"))
            if not is_pinned:
                checkpoint_post_times[post.post_id] = post.publish_time
            posts.append(post)

            if known_ids and not is_pinned and post.post_id in known_ids and not incremental_boundary_reached:
                incremental_boundary_reached = True
                lookback_remaining = max(0, incremental_lookback)
                if lookback_remaining == 0:
                    stop_due_to_boundary = True
                    checkpoint_safe = True
                    stop_reason = "incremental-boundary"
                    break
            elif incremental_boundary_reached and not is_pinned:
                lookback_remaining -= 1
                if lookback_remaining <= 0:
                    stop_due_to_boundary = True
                    checkpoint_safe = True
                    stop_reason = "incremental-lookback-complete"
                    break

            if max_posts and len(posts) >= max_posts:
                stop_due_to_boundary = True
                checkpoint_safe = incremental_boundary_reached
                stop_reason = "max-posts"
                break

        if stop_due_to_boundary:
            break

        if new_ids_on_page == 0:
            raise RuntimeError(f"Ifdian feed pagination made no progress at cursor {publish_sn!r}")

        last_item = raw_list[-1] if isinstance(raw_list[-1], dict) else {}
        next_publish_sn = oldest_seen_sn or str(last_item.get("publish_sn") or "")
        has_more = payload.get("has_more") if isinstance(payload, dict) else None
        if has_more in {0, False, "0", "false", "False"}:
            checkpoint_safe = True
            stop_reason = "feed-exhausted"
            break
        if not next_publish_sn:
            raise RuntimeError("Ifdian feed API reported more pages without a publish_sn cursor")
        if next_publish_sn == publish_sn:
            raise RuntimeError(f"Ifdian feed pagination cursor did not advance from {publish_sn!r}")
        publish_sn = next_publish_sn

    checkpoint_post_ids = sorted(
        checkpoint_post_times,
        key=lambda post_id: checkpoint_post_times[post_id],
        reverse=True,
    )[: max(1, checkpoint_id_limit)]
    checkpoint_publish_time = checkpoint_post_times.get(checkpoint_post_ids[0], 0) if checkpoint_post_ids else 0
    return FeedScanResult(
        posts=posts,
        checkpoint_post_ids=checkpoint_post_ids,
        checkpoint_publish_time=checkpoint_publish_time,
        incremental_boundary_reached=incremental_boundary_reached,
        checkpoint_safe=checkpoint_safe,
        stop_reason=stop_reason,
    )


def iter_feed_posts_api(
    session: requests.Session,
    creator_user_id: str,
    max_posts: int,
    since_ts: int | None,
    until_ts: int | None,
    stop_post_id: str,
    per_page: int,
) -> list[FeedPost]:
    return scan_feed_posts_api(
        session=session,
        creator_user_id=creator_user_id,
        max_posts=max_posts,
        since_ts=since_ts,
        until_ts=until_ts,
        stop_post_id=stop_post_id,
        per_page=per_page,
    ).posts


def find_named_value(data: dict[str, object], names: tuple[str, ...]) -> str:
    for name in names:
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def attachment_candidates_from_value(
    value: object,
    base_url: str,
    source: str,
    filename_hint: str,
    include_images: bool,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    if isinstance(value, str):
        url = normalize_url(value, base_url)
        if url and looks_like_download_url(url, include_images):
            candidates.append(
                Candidate(
                    url=url,
                    source=source,
                    referer=base_url,
                    filename_hint=filename_hint,
                    asset_locator=source,
                )
            )
        return candidates

    if isinstance(value, list):
        for index, item in enumerate(value, start=1):
            candidates.extend(
                attachment_candidates_from_value(
                    item,
                    base_url=base_url,
                    source=f"{source}[{index}]",
                    filename_hint=filename_hint,
                    include_images=include_images,
                )
            )
        return candidates

    if isinstance(value, dict):
        item_name = find_named_value(
            value,
            ("name", "filename", "file_name", "title", "origin_name", "original_name"),
        )
        item_hint = filename_hint
        if item_name:
            item_hint = sanitize_filename(f"{filename_hint} - {item_name}", fallback=filename_hint)

        for key, item_value in value.items():
            if not isinstance(item_value, str):
                continue
            if key.lower() not in {"url", "file", "path", "link", "href", "src", "download_url", "download"}:
                continue
            url = normalize_url(item_value, base_url)
            if url and looks_like_download_url(url, include_images):
                candidates.append(
                    Candidate(
                        url=url,
                        source=f"{source}.{key}",
                        referer=base_url,
                        filename_hint=item_hint,
                        asset_locator=source,
                    )
                )
        return candidates

    return candidates


def candidates_from_post_detail(post: dict[str, object], include_images: bool) -> list[Candidate]:
    post_id = str(post.get("post_id") or "")
    post_url = f"https://www.ifdian.net/p/{post_id}" if post_id else AFDIAN_HOME
    title = post_title_from_api(post)
    candidates: list[Candidate] = []

    for field in ("video", "audio"):
        value = post.get(field)
        if isinstance(value, str) and value.strip():
            url = normalize_url(value, post_url)
            if url:
                candidates.append(
                    Candidate(
                        url=url,
                        source=f"api:{field}",
                        referer=post_url,
                        filename_hint=title,
                        asset_locator=f"api:{field}",
                    )
                )

    candidates.extend(
        attachment_candidates_from_value(
            post.get("attachment") or [],
            base_url=post_url,
            source="api:attachment",
            filename_hint=title,
            include_images=include_images,
        )
    )

    if include_images:
        candidates.extend(
            attachment_candidates_from_value(
                post.get("pics") or [],
                base_url=post_url,
                source="api:pics",
                filename_hint=title,
                include_images=True,
            )
        )

    return dedupe_candidates(candidates)


def fetch_post_detail_api(session: requests.Session, post_id: str) -> dict[str, object]:
    data = api_get_json(session, "/api/post/get-detail", {"post_id": post_id, "album_id": ""})
    post = ((data.get("data") or {}).get("post") if isinstance(data.get("data"), dict) else None) or {}
    if not isinstance(post, dict) or not post.get("post_id"):
        raise RuntimeError(f"Could not fetch post detail for {post_id}")
    return post


def filename_from_headers(headers: dict[str, str]) -> str | None:
    disposition = headers.get("content-disposition") or headers.get("Content-Disposition")
    if not disposition:
        return None

    utf8_match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, flags=re.I)
    if utf8_match:
        return unquote(utf8_match.group(1).strip().strip('"'))

    ascii_match = re.search(r'filename="?([^";]+)"?', disposition, flags=re.I)
    if ascii_match:
        return unquote(ascii_match.group(1).strip())

    return None


def filename_suffix_from_url_or_headers(url: str, headers: dict[str, str]) -> str:
    header_name = filename_from_headers(headers)
    if header_name:
        suffix = Path(header_name).suffix
        if suffix:
            return suffix

    parsed = urlparse(url)
    path_suffix = Path(unquote(parsed.path)).suffix
    if path_suffix:
        return path_suffix

    content_type = (headers.get("content-type") or "").split(";", 1)[0].strip()
    return mimetypes.guess_extension(content_type) or ".bin"


def filename_from_url(url: str, headers: dict[str, str], preferred_stem: str = "") -> str:
    if preferred_stem:
        filename = sanitize_filename(preferred_stem)
        suffix = filename_suffix_from_url_or_headers(url, headers)
        if suffix and filename.lower().endswith(suffix.lower()):
            return filename
        return filename + suffix

    header_name = filename_from_headers(headers)
    if header_name:
        return sanitize_filename(header_name)

    parsed = urlparse(url)
    path_name = Path(unquote(parsed.path)).name
    if path_name:
        return sanitize_filename(path_name)

    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    content_type = (headers.get("content-type") or "").split(";", 1)[0].strip()
    suffix = mimetypes.guess_extension(content_type) or ".bin"
    return f"download-{digest}{suffix}"


def unique_path(path: Path, overwrite: bool) -> Path:
    if overwrite or not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 10_000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create unique filename for {path}")


def download_candidate(
    session: requests.Session,
    candidate: Candidate,
    output_dir: Path,
    include_images: bool,
    overwrite: bool,
    dry_run: bool,
    timeout: float,
) -> dict[str, object]:
    headers = {"Referer": candidate.referer or AFDIAN_HOME}
    record: dict[str, object] = {
            "url": candidate.url,
            "source": candidate.source,
            "referer": candidate.referer,
            "filename_hint": candidate.filename_hint,
        }

    if dry_run:
        record["status"] = "dry-run"
        print(f"[dry-run] {candidate.url}")
        return record

    try:
        with session.get(candidate.url, headers=headers, stream=True, timeout=timeout, allow_redirects=True) as response:
            response.raise_for_status()
            if not looks_like_download_url(response.url, include_images, dict(response.headers)):
                record["status"] = "skipped"
                record["reason"] = "response does not look like a downloadable file"
                return record

            filename = filename_from_url(response.url, dict(response.headers), preferred_stem=candidate.filename_hint)
            target_path = unique_path(output_dir / filename, overwrite=overwrite)
            part_path = target_path.with_suffix(target_path.suffix + ".part")
            total = 0
            started_at = time.perf_counter()
            with part_path.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=1024 * 512):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    total += len(chunk)
            part_path.replace(target_path)
            elapsed_seconds = max(time.perf_counter() - started_at, 0.001)
            speed_mib_s = (total / 1024 / 1024) / elapsed_seconds
            record.update(
                {
                    "status": "downloaded",
                    "path": str(target_path),
                    "bytes": total,
                    "elapsed_seconds": round(elapsed_seconds, 3),
                    "speed_mib_s": round(speed_mib_s, 3),
                    "final_url": response.url,
                }
            )
            print(f"[ok] {target_path} ({total} bytes, {speed_mib_s:.2f} MiB/s)")
            return record
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = str(exc)
        print(f"[failed] {candidate.url}: {exc}")
        return record


def page_title_slug(page: Page, fallback_url: str) -> str:
    post_title = extract_post_title(page)
    if post_title:
        return slug_from_text(post_title, fallback="post")

    try:
        title = page.title().strip()
    except Exception:
        title = ""
    if title:
        title = clean_post_title(title)
    parsed = urlparse(fallback_url)
    post_match = re.search(r"/p/([^/?#]+)", parsed.path)
    if not title or title in {"下载爱发电", "下载爱发电-App丨爱发电", "登录爱发电", "登录"}:
        if post_match:
            return f"post-{sanitize_filename(post_match.group(1))[:12]}"
    host = urlparse(fallback_url).netloc or "afdian"
    return slug_from_text(title, fallback=host)


def extract_post_title(page: Page) -> str:
    try:
        title = page.evaluate(
            """() => {
                const selectors = [
                    '[class*="post"][class*="title" i]',
                    '[class*="title" i]',
                    'h1',
                    'meta[property="og:title"]',
                    'meta[name="twitter:title"]'
                ];
                for (const selector of selectors) {
                    for (const el of document.querySelectorAll(selector)) {
                        const value = (el.content || el.innerText || el.textContent || '').trim();
                        if (!value) continue;
                        if (/^(AFDIAN|Home|Explore|App|New Post|Dashboard|Setting)$/i.test(value)) continue;
                        if (/爱发电App|下载爱发电/.test(value)) continue;
                        return value;
                    }
                }
                return document.title || '';
            }"""
        )
    except Exception:
        try:
            title = page.title().strip()
        except Exception:
            title = ""
    return clean_post_title(title)


def is_login_page(page: Page) -> bool:
    parsed = urlparse(page.url)
    if "/login" in parsed.path.lower():
        return True
    try:
        title = page.title().strip()
    except Exception:
        title = ""
    return "登录爱发电" in title or title == "登录"


def auto_scroll(page: Page, rounds: int, delay_ms: int) -> None:
    for _ in range(max(0, rounds)):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(delay_ms)


def extract_dom_candidates(page: Page, include_images: bool) -> list[Candidate]:
    payload = page.evaluate(
        """() => {
            const items = [];
            const selectors = [
                'a[href]',
                'video[src]',
                'audio[src]',
                'source[src]',
                '[download]',
                '[data-src]',
                '[data-url]',
                '[data-href]'
            ];
            for (const el of document.querySelectorAll(selectors.join(','))) {
                const attrs = ['href', 'src', 'data-src', 'data-url', 'data-href'];
                for (const attr of attrs) {
                    const value = el.getAttribute(attr);
                    if (value) {
                        items.push({
                            url: value,
                            text: (el.innerText || el.getAttribute('title') || el.getAttribute('aria-label') || '').trim(),
                            source: `dom:${attr}`
                        });
                    }
                }
            }
            return {
                items,
                html: document.documentElement.innerHTML
            };
        }"""
    )

    candidates: list[Candidate] = []
    for item in payload.get("items", []):
        url = normalize_url(item.get("url", ""), page.url)
        if not url:
            continue
        if is_obvious_non_file_link(url):
            continue
        if looks_like_download_url(url, include_images) or TEXT_BUTTON_RE.search(item.get("text", "")):
            candidates.append(
                Candidate(
                    url=url,
                    text=item.get("text", ""),
                    source=item.get("source", "dom"),
                    referer=page.url,
                )
            )

    html = payload.get("html", "")
    for match in URL_RE.finditer(html):
        raw = match.group(0).replace("\\/", "/")
        url = normalize_url(raw, page.url)
        if not url:
            continue
        if is_obvious_non_file_link(url):
            continue
        if has_attachment_host(url) and looks_like_download_url(url, include_images):
            candidates.append(Candidate(url=url, source="html-regex", referer=page.url))

    return dedupe_candidates(candidates)


def extract_crawl_links(page: Page) -> list[str]:
    links = page.evaluate(
        """() => Array.from(document.querySelectorAll('a[href]'))
            .map(a => a.href)
            .filter(Boolean)"""
    )
    result: list[str] = []
    for link in links:
        parsed = urlparse(link)
        if not is_afdian_host(link):
            continue
        if re.search(r"/(p|album|a)/", parsed.path) or "/item/" in parsed.path:
            result.append(link.split("#", 1)[0])
    return sorted(set(result))


def probe_download_clicks(page: Page, captured: list[Candidate]) -> None:
    elements = page.locator("a, button, [role=button]").filter(has_text=TEXT_BUTTON_RE)
    try:
        count = min(elements.count(), 30)
    except Exception:
        return

    for index in range(count):
        element = elements.nth(index)
        try:
            if not element.is_visible():
                continue
            label = (element.inner_text(timeout=1000) or "").strip()
            with page.expect_download(timeout=3000) as download_info:
                element.click(timeout=3000, force=False)
            download = download_info.value
            captured.append(Candidate(url=download.url, source="click-download", text=label, referer=page.url))
            try:
                download.cancel()
            except Exception:
                pass
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue


def collect_page_candidates(
    context: BrowserContext,
    page_url: str,
    include_images: bool,
    scroll_rounds: int,
    probe_clicks: bool,
) -> tuple[list[Candidate], list[str], str]:
    page = context.new_page()
    page.set_default_timeout(DEFAULT_TIMEOUT_MS)
    network_candidates: list[Candidate] = []

    def on_response(response) -> None:
        try:
            headers = response.headers
            url = response.url
            if looks_like_download_url(url, include_images, headers):
                network_candidates.append(Candidate(url=url, source="network", referer=page.url))
        except Exception:
            return

    page.on("response", on_response)
    eprint(f"Opening {page_url}")
    page.goto(page_url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except PlaywrightTimeoutError:
        pass

    if is_login_page(page):
        eprint(f"Login required for {page_url}; browser ended at {page.url}")
        title = page_title_slug(page, page_url)
        page.close()
        return [], [], title

    auto_scroll(page, rounds=scroll_rounds, delay_ms=700)
    dom_candidates = extract_dom_candidates(page, include_images)
    click_candidates: list[Candidate] = []
    if probe_clicks:
        probe_download_clicks(page, click_candidates)

    post_title = extract_post_title(page)
    title = page_title_slug(page, page_url)
    crawl_links = extract_crawl_links(page)
    page.close()

    all_candidates = dedupe_candidates(
        with_filename_hint([*network_candidates, *dom_candidates, *click_candidates], post_title or title)
    )
    return all_candidates, crawl_links, title


def command_login(args: argparse.Namespace) -> int:
    profile = Path(args.profile)
    urls = [args.url] if args.url else list(AFDIAN_LOGIN_URLS)
    for url in AFDIAN_LOGIN_URLS:
        if url not in urls:
            urls.append(url)
    with require_playwright()() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            accept_downloads=True,
            user_agent=DEFAULT_USER_AGENT,
        )
        page = context.pages[0] if context.pages else context.new_page()
        opened_url = goto_first_available(page, urls)
        if opened_url:
            print(f"已打开登录入口: {opened_url}")
        else:
            print("未能自动打开爱发电入口。请在浏览器地址栏手动输入 https://ifdian.net/ 后登录。")
        print("浏览器已打开。请完成登录，然后回到终端按 Enter 保存会话。")
        input()
        try:
            context.close()
        except Exception:
            pass
    print(f"登录会话已保存到: {profile.resolve()}")
    return 0


def iter_input_urls(args: argparse.Namespace) -> list[str]:
    urls: list[str] = []
    for url in args.url or []:
        if url.strip():
            urls.append(url.strip())
    if args.url_file:
        path = Path(args.url_file)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    return sorted(set(urls))


def command_download(args: argparse.Namespace) -> int:
    cookie_session = session_from_cookie_args(args)
    if args.api:
        if cookie_session is not None:
            return download_post_api_urls(args, cookie_session)
        profile = Path(args.profile)
        with require_playwright()() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=not args.show_browser,
                accept_downloads=True,
                user_agent=DEFAULT_USER_AGENT,
            )
            session = cookies_to_session(context)
            try:
                return download_post_api_urls(args, session)
            finally:
                context.close()

    if cookie_session is not None:
        raise SystemExit("download 的网页解析模式需要 Playwright 浏览器。若想使用 Cookie 直连接口，请加 --api。")

    seed_urls = iter_input_urls(args)
    if not seed_urls:
        raise SystemExit("请通过 --url 或 --url-file 提供至少一个爱发电页面地址。")

    profile = Path(args.profile)
    output_root = Path(args.out)
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "manifest.jsonl"

    queue: list[tuple[str, int]] = [(url, 0) for url in seed_urls]
    visited_pages: set[str] = set()
    all_records: list[dict[str, object]] = []
    downloaded_count = 0

    with require_playwright()() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=not args.show_browser,
            accept_downloads=True,
            user_agent=DEFAULT_USER_AGENT,
        )
        session = cookies_to_session(context)

        while queue:
            page_url, depth = queue.pop(0)
            if page_url in visited_pages:
                continue
            visited_pages.add(page_url)

            candidates, crawl_links, title = collect_page_candidates(
                context=context,
                page_url=page_url,
                include_images=args.include_images,
                scroll_rounds=args.scroll_rounds,
                probe_clicks=args.probe_clicks,
            )
            page_dir = output_root / title
            page_dir.mkdir(parents=True, exist_ok=True)
            print(f"Found {len(candidates)} candidate links on {page_url}")

            for candidate in candidates:
                if args.limit and downloaded_count >= args.limit:
                    break
                record = download_candidate(
                    session=session,
                    candidate=candidate,
                    output_dir=page_dir,
                    include_images=args.include_images,
                    overwrite=args.overwrite,
                    dry_run=args.dry_run,
                    timeout=args.timeout,
                )
                all_records.append(record)
                if record.get("status") in {"downloaded", "dry-run"}:
                    downloaded_count += 1

            if depth < args.crawl_depth:
                for link in crawl_links:
                    if link not in visited_pages:
                        queue.append((link, depth + 1))

        context.close()

    with manifest_path.open("a", encoding="utf-8") as manifest:
        for record in all_records:
            manifest.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Done. Records written to {manifest_path.resolve()}")
    return 0


def download_post_api_urls(args: argparse.Namespace, session: requests.Session) -> int:
    seed_urls = iter_input_urls(args)
    if not seed_urls:
        raise SystemExit("请通过 --url 或 --url-file 提供至少一个爱发电帖子地址。")

    output_root = Path(args.out)
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "manifest.jsonl"
    all_records: list[dict[str, object]] = []
    downloaded_count = 0

    for url in seed_urls:
        post_id = post_id_from_url(url)
        if not post_id:
            all_records.append({"status": "skipped", "url": url, "reason": "not a post URL; --api only supports /p/<post_id>"})
            continue
        try:
            detail = fetch_post_detail_api(session, post_id)
            title = post_title_from_api(detail)
            candidates = candidates_from_post_detail(detail, include_images=args.include_images)
        except Exception as exc:
            all_records.append({"status": "detail-failed", "url": url, "post_id": post_id, "error": str(exc)})
            continue

        post_dir = output_root / slug_from_text(title, fallback=post_id[:12] or "post")
        post_dir.mkdir(parents=True, exist_ok=True)
        print(f"Found {len(candidates)} API candidate links on {url}")

        if not candidates:
            all_records.append({"status": "no-files", "url": url, "post_id": post_id, "title": title})
            continue

        for candidate in candidates:
            if args.limit and downloaded_count >= args.limit:
                break
            record = download_candidate(
                session=session,
                candidate=candidate,
                output_dir=post_dir,
                include_images=args.include_images,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
                timeout=args.timeout,
            )
            record.update({"post_id": post_id, "post_title": title, "post_url": normalize_ifdian_url(url)})
            all_records.append(record)
            if record.get("status") in {"downloaded", "dry-run"}:
                downloaded_count += 1

    with manifest_path.open("a", encoding="utf-8") as manifest:
        for record in all_records:
            manifest.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Done. Records written to {manifest_path.resolve()}")
    return 0


def command_feed(args: argparse.Namespace) -> int:
    feed_urls = iter_input_urls(args)
    if not feed_urls:
        raise SystemExit("请通过 --url 或 --url-file 提供至少一个创作者动态页，例如 https://www.ifdian.net/a/520Labula_?tab=feed。")

    since_ts = parse_date_boundary(args.since, end_of_day=False)
    until_ts = parse_date_boundary(args.until, end_of_day=True)
    profile = Path(args.profile)
    output_root = Path(args.out)
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "manifest.jsonl"
    all_records: list[dict[str, object]] = []
    downloaded_count = 0

    cookie_session = session_from_cookie_args(args)
    if cookie_session is not None:
        return run_feed_with_session(args, cookie_session, feed_urls, since_ts, until_ts, output_root, manifest_path)

    with require_playwright()() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=not args.show_browser,
            accept_downloads=True,
            user_agent=DEFAULT_USER_AGENT,
        )
        session = cookies_to_session(context)
        try:
            return run_feed_with_session(args, session, feed_urls, since_ts, until_ts, output_root, manifest_path, context)
        finally:
            context.close()

def run_feed_with_session(
    args: argparse.Namespace,
    session: requests.Session,
    feed_urls: list[str],
    since_ts: int | None,
    until_ts: int | None,
    output_root: Path,
    manifest_path: Path,
    context: BrowserContext | None = None,
) -> int:
    all_records: list[dict[str, object]] = []
    downloaded_count = 0

    for feed_url in feed_urls:
            normalized_feed_url = normalize_ifdian_url(feed_url)
            creator = get_creator_profile(session, normalized_feed_url)
            creator_id = str(creator["user_id"])
            creator_name = sanitize_filename(str(creator.get("name") or creator.get("url_slug") or creator_id), fallback=creator_id)
            creator_dir = output_root / creator_name
            creator_dir.mkdir(parents=True, exist_ok=True)

            posts = iter_feed_posts_api(
                session=session,
                creator_user_id=creator_id,
                max_posts=args.max_posts,
                since_ts=since_ts,
                until_ts=until_ts,
                stop_post_id=args.stop_post_id or "",
                per_page=args.per_page,
            )
            print(f"Found {len(posts)} posts from {creator_name} ({normalized_feed_url})")

            for post in posts:
                if args.limit and downloaded_count >= args.limit:
                    break

                post_dir_name = slug_from_text(
                    f"{format_publish_date(post.publish_time)} {post.title}",
                    fallback=post.post_id[:12] or "post",
                )
                post_dir = creator_dir / post_dir_name
                post_dir.mkdir(parents=True, exist_ok=True)

                try:
                    detail = fetch_post_detail_api(session, post.post_id)
                    title = post_title_from_api(detail)
                    candidates = candidates_from_post_detail(detail, include_images=args.include_images)
                    has_right = detail.get("has_right")
                    if has_right in {0, False}:
                        all_records.append(
                            {
                                "status": "no-right",
                                "post_id": post.post_id,
                                "title": title,
                                "url": post.url,
                                "error": detail.get("has_right_errMsg") or "current account has no right to this post",
                            }
                        )
                        continue
                except Exception as exc:
                    all_records.append(
                        {
                            "status": "detail-failed",
                            "post_id": post.post_id,
                            "title": post.title,
                            "url": post.url,
                            "error": str(exc),
                        }
                    )
                    continue

                if not candidates and args.fallback_browser:
                    if context is None:
                        all_records.append(
                            {
                                "status": "fallback-unavailable",
                                "post_id": post.post_id,
                                "title": post.title,
                                "url": post.url,
                                "error": "--fallback-browser requires profile/Playwright mode; it is unavailable with --cookie/--cookie-file",
                            }
                        )
                        continue
                    candidates, _, _ = collect_page_candidates(
                        context=context,
                        page_url=post.url,
                        include_images=args.include_images,
                        scroll_rounds=args.scroll_rounds,
                        probe_clicks=args.probe_clicks,
                    )

                if not candidates:
                    all_records.append(
                        {
                            "status": "no-files",
                            "post_id": post.post_id,
                            "title": post.title,
                            "url": post.url,
                        }
                    )
                    continue

                print(f"[post] {format_publish_date(post.publish_time)} {post.title}: {len(candidates)} file candidate(s)")
                for candidate in candidates:
                    if args.limit and downloaded_count >= args.limit:
                        break
                    record = download_candidate(
                        session=session,
                        candidate=candidate,
                        output_dir=post_dir,
                        include_images=args.include_images,
                        overwrite=args.overwrite,
                        dry_run=args.dry_run,
                        timeout=args.timeout,
                    )
                    record.update(
                        {
                            "post_id": post.post_id,
                            "post_title": post.title,
                            "post_url": post.url,
                            "publish_time": post.publish_time,
                            "publish_date": format_publish_date(post.publish_time),
                        }
                    )
                    all_records.append(record)
                    if record.get("status") in {"downloaded", "dry-run"}:
                        downloaded_count += 1

    with manifest_path.open("a", encoding="utf-8") as manifest:
        for record in all_records:
            manifest.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Done. Records written to {manifest_path.resolve()}")
    return 0


def afdian_sign(token: str, payload: dict[str, object]) -> dict[str, object]:
    user_id = str(payload["user_id"])
    params = str(payload["params"])
    ts = str(payload["ts"])
    source = f"{token}params{params}ts{ts}user_id{user_id}"
    payload["sign"] = hashlib.md5(source.encode("utf-8")).hexdigest()
    return payload


def call_open_api(api_name: str, user_id: str, token: str, params: dict[str, object]) -> dict[str, object]:
    params_json = json.dumps(params, ensure_ascii=False, separators=(",", ":"))
    payload = afdian_sign(
        token,
        {
            "user_id": user_id,
            "params": params_json,
            "ts": int(time.time()),
        },
    )
    response = requests.post(f"https://afdian.net/api/open/{api_name}", data=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if data.get("ec") != 200:
        raise RuntimeError(f"Afdian API error {data.get('ec')}: {data.get('em')}")
    return data


def command_api(args: argparse.Namespace) -> int:
    user_id = args.user_id or os.getenv("AFDIAN_USER_ID")
    token = args.token or os.getenv("AFDIAN_TOKEN")
    if not user_id or not token:
        raise SystemExit("请通过 --user-id/--token 或 AFDIAN_USER_ID/AFDIAN_TOKEN 环境变量提供开发者凭证。")

    params: dict[str, object] = {"page": args.page}
    if args.per_page is not None:
        params["per_page"] = args.per_page
    if args.raw_params:
        params.update(json.loads(args.raw_params))

    result = call_open_api(args.api_name, user_id=user_id, token=token, params=params)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download files visible to your authenticated Afdian account.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="Open a browser and save an authenticated Afdian session.")
    login.add_argument("--profile", default=str(DEFAULT_PROFILE), help="Persistent browser profile directory.")
    login.add_argument("--url", default=None, help="Login/start URL. Defaults to ifdian.net with Afdian fallbacks.")
    login.set_defaults(func=command_login)

    download = subparsers.add_parser("download", help="Download files from Afdian pages using the saved session.")
    download.add_argument("--url", action="append", help="Afdian page URL. Can be repeated.")
    download.add_argument("--url-file", help="Text file with one URL per line.")
    download.add_argument("--profile", default=str(DEFAULT_PROFILE), help="Persistent browser profile directory.")
    download.add_argument("--cookie", action="append", help="Cookie string, JSON cookie object/list, or can be repeated.")
    download.add_argument("--cookie-file", help="Cookie file. Supports browser Cookie header, JSON, or Netscape cookies.txt.")
    download.add_argument("--api", action="store_true", help="For /p/<post_id> URLs, use Ifdian JSON API and cookies instead of browser page parsing.")
    download.add_argument("--out", default=str(DEFAULT_DOWNLOAD_DIR), help="Output directory.")
    download.add_argument("--include-images", action="store_true", help="Also download image files.")
    download.add_argument("--probe-clicks", action="store_true", help="Try clicking visible download/attachment buttons.")
    download.add_argument("--crawl-depth", type=int, default=0, help="Follow Afdian post/album links up to this depth.")
    download.add_argument("--scroll-rounds", type=int, default=4, help="Scroll rounds for lazy-loaded content.")
    download.add_argument("--timeout", type=float, default=60.0, help="Download request timeout in seconds.")
    download.add_argument("--limit", type=int, default=0, help="Maximum number of files to download. 0 means no limit.")
    download.add_argument("--overwrite", action="store_true", help="Overwrite existing files instead of creating unique names.")
    download.add_argument("--dry-run", action="store_true", help="Print candidate URLs without writing files.")
    download.add_argument("--show-browser", action="store_true", help="Run Chromium visibly while downloading.")
    download.set_defaults(func=command_download)

    feed = subparsers.add_parser(
        "feed",
        help="Use Ifdian's logged-in JSON APIs to crawl a creator feed and download accessible post files.",
    )
    feed.add_argument("--url", action="append", help="Creator feed URL. Can be repeated, e.g. https://www.ifdian.net/a/name?tab=feed.")
    feed.add_argument("--url-file", help="Text file with one creator feed URL per line.")
    feed.add_argument("--profile", default=str(DEFAULT_PROFILE), help="Persistent browser profile directory.")
    feed.add_argument("--cookie", action="append", help="Cookie string, JSON cookie object/list, or can be repeated. Skips Playwright when set.")
    feed.add_argument("--cookie-file", help="Cookie file. Supports browser Cookie header, JSON, or Netscape cookies.txt. Skips Playwright when set.")
    feed.add_argument("--out", default=str(DEFAULT_DOWNLOAD_DIR), help="Output directory.")
    feed.add_argument("--max-posts", type=int, default=0, help="Maximum posts to inspect per creator. 0 means all available.")
    feed.add_argument("--since", help="Only include posts on or after this date, YYYY-MM-DD.")
    feed.add_argument("--until", help="Only include posts on or before this date, YYYY-MM-DD.")
    feed.add_argument("--stop-post-id", help="Stop crawling when this post id is reached. The boundary post is not downloaded.")
    feed.add_argument("--per-page", type=int, default=10, help="Feed API page size.")
    feed.add_argument("--include-images", action="store_true", help="Also download images from post details.")
    feed.add_argument("--timeout", type=float, default=60.0, help="Download request timeout in seconds.")
    feed.add_argument("--limit", type=int, default=0, help="Maximum number of files to download. 0 means no limit.")
    feed.add_argument("--overwrite", action="store_true", help="Overwrite existing files instead of creating unique names.")
    feed.add_argument("--dry-run", action="store_true", help="Print candidate URLs without writing files.")
    feed.add_argument("--fallback-browser", action="store_true", help="Open the post page only when the API detail has no file candidates.")
    feed.add_argument("--probe-clicks", action="store_true", help="With --fallback-browser, try clicking visible download/attachment buttons.")
    feed.add_argument("--scroll-rounds", type=int, default=4, help="With --fallback-browser, scroll rounds for lazy-loaded content.")
    feed.add_argument("--show-browser", action="store_true", help="Run Chromium visibly. Usually not needed for API mode.")
    feed.set_defaults(func=command_feed)

    api = subparsers.add_parser("api", help="Call Afdian creator OpenAPI endpoints.")
    api_subparsers = api.add_subparsers(dest="api_name", required=True)
    for api_name in ("query-order", "query-sponsor"):
        child = api_subparsers.add_parser(api_name, help=f"Call {api_name}.")
        child.add_argument("--user-id", help="Afdian developer user_id. Defaults to AFDIAN_USER_ID.")
        child.add_argument("--token", help="Afdian developer token. Defaults to AFDIAN_TOKEN.")
        child.add_argument("--page", type=int, default=1, help="Page number.")
        child.add_argument("--per-page", type=int, help="Page size if supported by the endpoint.")
        child.add_argument("--raw-params", help="Extra JSON object merged into params.")
        child.set_defaults(func=command_api)

    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

