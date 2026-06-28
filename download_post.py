from __future__ import annotations

import sys

from afdian_config_common import configure_stdio, load_config, run_single_post


def main() -> int:
    configure_stdio()
    config, config_dir = load_config("config.json")
    posts = sys.argv[1:] or config.get("single_posts") or []
    if isinstance(posts, str):
        posts = [posts]
    if not isinstance(posts, list) or not posts:
        raise SystemExit("请通过启动参数传入帖子 URL，例如: python download_post.py https://www.ifdian.net/p/xxxx")

    print(f"[config] config_dir={config_dir}")
    print(f"[posts] configured={len(posts)}")
    for index, post_url in enumerate(posts, start=1):
        print(f"[post-config] #{index} url={post_url}")

    total_downloaded = 0
    total_records = 0
    for post_url in posts:
        if not isinstance(post_url, str) or not post_url.strip():
            continue
        records = run_single_post(config, config_dir, post_url.strip())
        total_records += len(records)
        total_downloaded += sum(1 for record in records if record.get("status") == "downloaded")

    print(f"[done] records={total_records}, downloaded={total_downloaded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
