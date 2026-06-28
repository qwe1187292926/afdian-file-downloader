from __future__ import annotations

from afdian_config_common import configure_stdio, load_config, run_creator


def main() -> int:
    configure_stdio()
    config, config_dir = load_config("config.json")
    creators = config.get("creators") or []
    if not isinstance(creators, list) or not creators:
        raise SystemExit("config.json 里没有配置 creators。")

    print(f"[config] config_dir={config_dir}")
    print(f"[creators] configured={len(creators)}")
    for index, creator in enumerate(creators, start=1):
        if isinstance(creator, dict):
            print(f"[creator-config] #{index} url={creator.get('url', '')}")

    total_downloaded = 0
    total_records = 0
    for creator in creators:
        if not isinstance(creator, dict):
            print(f"[skip] invalid creator config: {creator}")
            continue
        records = run_creator(config, config_dir, creator)
        total_records += len(records)
        total_downloaded += sum(1 for record in records if record.get("status") == "downloaded")

    print(f"[done] records={total_records}, downloaded={total_downloaded}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
