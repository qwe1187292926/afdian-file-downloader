from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from afdian_config_common import (
    POST_SIDECAR_NAME,
    DownloadState,
    claim_post_directory,
    download_candidates_for_post,
    download_key,
    download_key_v1,
    legacy_download_key,
    post_output_dir,
    resolve_post_output_dir,
    run_creator,
)
from afdian_downloader import Candidate, FeedPost, FeedScanResult


PUBLISH_TIME = 1_752_787_200


def make_post_meta(post_id: str = "85f0bbb0821111f1a73852540025c377") -> dict[str, object]:
    return {
        "creator_id": "ff8b0492c95811ecb44252540025c377",
        "creator_name": "辣不辣Hyo",
        "post_id": post_id,
        "post_title": "KU100 1v1轻语助眠天鹅",
        "post_url": f"https://www.ifdian.net/p/{post_id}",
        "publish_time": PUBLISH_TIME,
        "publish_date": "2025-07-18",
    }


def preferred_post_dir(root: Path, post_meta: dict[str, object]) -> Path:
    return post_output_dir(
        root,
        str(post_meta["creator_name"]),
        int(post_meta["publish_time"]),
        str(post_meta["post_title"]),
        str(post_meta["post_id"]),
        creator_id=str(post_meta["creator_id"]),
    )


def archive_candidate() -> Candidate:
    return Candidate(
        url="https://cdn.example/archive.zip",
        source="api:attachment[1].url",
        filename_hint="archive",
        asset_locator="api:attachment[1]",
    )


class ReadablePathCollisionTests(unittest.TestCase):
    def test_same_creator_date_and_title_gets_a_deterministic_readable_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_meta = make_post_meta()
            preferred = preferred_post_dir(root, first_meta)
            claim_post_directory(preferred, first_meta, dry_run=False)

            second_meta = make_post_meta("85c9cfdc7efb11f1b59652540025c377")
            second = resolve_post_output_dir(preferred_post_dir(root, second_meta), second_meta)

            self.assertEqual(preferred, resolve_post_output_dir(preferred, first_meta))
            self.assertEqual(
                root
                / "辣不辣Hyo"
                / "2025-07-18-KU100-1v1轻语助眠天鹅--85c9cfdc7efb-ffd4b566",
                second,
            )
            self.assertEqual(second, resolve_post_output_dir(preferred_post_dir(root, second_meta), second_meta))

    def test_unknown_nonempty_readable_directory_is_preserved_and_new_download_is_suffixed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meta = make_post_meta()
            preferred = preferred_post_dir(root, meta)
            preferred.mkdir(parents=True)
            unknown_file = preferred / "unrelated.bin"
            unknown_file.write_bytes(b"unknown")
            candidate = archive_candidate()
            state = DownloadState(root / "download_state.json")

            def fake_download(**kwargs):
                self.assertNotEqual(preferred, kwargs["output_dir"])
                self.assertIn(str(meta["post_id"])[:12], kwargs["output_dir"].name)
                target = kwargs["output_dir"] / "archive.zip"
                target.write_bytes(b"new")
                return {
                    "status": "downloaded",
                    "url": kwargs["candidate"].url,
                    "path": str(target),
                    "bytes": target.stat().st_size,
                }

            with patch(
                "afdian_config_common.download_candidate",
                side_effect=fake_download,
            ) as mocked_download, patch("builtins.print"):
                records = download_candidates_for_post(
                    session=None,
                    candidates=[candidate],
                    output_dir=preferred,
                    state=state,
                    config={"skip_existing": True},
                    post_meta=meta,
                )

            mocked_download.assert_called_once()
            self.assertEqual("downloaded", records[0]["status"])
            self.assertTrue(unknown_file.is_file())


class PreFfed164DownloadCompatibilityTests(unittest.TestCase):
    def test_creator_run_uses_feed_publish_time_for_the_legacy_readable_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            download_root = root / "downloads"
            meta = make_post_meta()
            output_dir = preferred_post_dir(download_root, meta)
            output_dir.mkdir(parents=True)
            existing_file = output_dir / "archive.zip"
            existing_file.write_bytes(b"already downloaded")
            candidate = archive_candidate()
            post = FeedPost(
                post_id=str(meta["post_id"]),
                title=str(meta["post_title"]),
                publish_time=PUBLISH_TIME,
                publish_sn="feed-sn",
                url=str(meta["post_url"]),
                raw={},
            )
            scan = FeedScanResult(
                posts=[post],
                checkpoint_post_ids=[post.post_id],
                checkpoint_publish_time=post.publish_time,
                incremental_boundary_reached=False,
                checkpoint_safe=True,
                stop_reason="feed-exhausted",
            )
            detail = {
                "post_id": post.post_id,
                "title": post.title,
                "publish_time": PUBLISH_TIME + 86_400,
                "has_right": 1,
            }
            config = {
                "download_dir": str(download_root),
                "state_file": "download_state.json",
                "skip_existing": True,
                "incremental_scan": False,
            }
            target = {"url": "https://www.ifdian.net/a/creator"}

            def unexpected_download(**kwargs):
                target_file = kwargs["output_dir"] / "unexpected.zip"
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_bytes(b"unexpected")
                return {
                    "status": "downloaded",
                    "url": kwargs["candidate"].url,
                    "path": str(target_file),
                    "bytes": target_file.stat().st_size,
                }

            with patch(
                "afdian_config_common.create_session_from_config", return_value=object()
            ), patch(
                "afdian_config_common.get_creator_profile",
                return_value={
                    "user_id": meta["creator_id"],
                    "name": meta["creator_name"],
                },
            ), patch(
                "afdian_config_common.scan_feed_posts_api", return_value=scan
            ), patch(
                "afdian_config_common.fetch_post_detail_api", return_value=detail
            ), patch(
                "afdian_config_common.candidates_from_post_detail", return_value=[candidate]
            ), patch(
                "afdian_config_common.download_candidate", side_effect=unexpected_download
            ) as mocked_download, patch(
                "afdian_config_common.append_manifest"
            ), patch(
                "afdian_config_common.bark_notify", return_value=False
            ), patch(
                "builtins.print"
            ):
                records = run_creator(config, root, target)

            mocked_download.assert_not_called()
            self.assertEqual("already-downloaded", records[0]["status"])
            self.assertEqual(existing_file.resolve(), Path(records[0]["path"]).resolve())

    def test_v1_and_v0_state_reuse_a_file_in_the_exact_readable_layout(self) -> None:
        for version, key_builder in (("v1", download_key_v1), ("v0", legacy_download_key)):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                meta = make_post_meta()
                output_dir = preferred_post_dir(root, meta)
                output_dir.mkdir(parents=True)
                existing_file = output_dir / "archive.zip"
                existing_file.write_bytes(b"already downloaded")
                candidate = archive_candidate()
                old_key = key_builder(str(meta["post_id"]), candidate)
                state_path = root / "download_state.json"
                state_path.write_text(
                    json.dumps(
                        {
                            "downloads": {
                                old_key: {
                                    "path": str(existing_file),
                                    "bytes": existing_file.stat().st_size,
                                    "post_id": meta["post_id"],
                                    "post_title": meta["post_title"],
                                    "url": candidate.url,
                                    "downloaded_at": "2026-07-17T00:00:00+08:00",
                                }
                            },
                            "inaccessible_posts": {},
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                state = DownloadState(state_path)

                with patch("afdian_config_common.download_candidate") as mocked_download, patch(
                    "builtins.print"
                ):
                    records = download_candidates_for_post(
                        session=None,
                        candidates=[candidate],
                        output_dir=output_dir,
                        state=state,
                        config={"skip_existing": True},
                        post_meta=meta,
                    )

                mocked_download.assert_not_called()
                self.assertEqual("already-downloaded", records[0]["status"])
                self.assertEqual(existing_file.resolve(), Path(records[0]["path"]).resolve())
                migrated = DownloadState(state_path).get_existing_entry(
                    download_key(str(meta["post_id"]), candidate)
                )
                self.assertIsNotNone(migrated)
                self.assertEqual(existing_file.resolve(), Path(str(migrated["path"])).resolve())

    def test_missing_state_adopts_the_expected_file_in_an_old_readable_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meta = make_post_meta()
            output_dir = preferred_post_dir(root, meta)
            output_dir.mkdir(parents=True)
            existing_file = output_dir / "archive.zip"
            existing_file.write_bytes(b"already downloaded")
            candidate = archive_candidate()
            state_path = root / "download_state.json"
            state = DownloadState(state_path)

            with patch("afdian_config_common.download_candidate") as mocked_download, patch(
                "builtins.print"
            ):
                records = download_candidates_for_post(
                    session=None,
                    candidates=[candidate],
                    output_dir=output_dir,
                    state=state,
                    config={"skip_existing": True},
                    post_meta=meta,
                )

            mocked_download.assert_not_called()
            self.assertEqual("already-downloaded", records[0]["status"])
            self.assertEqual(existing_file.resolve(), Path(records[0]["path"]).resolve())

            new_key = download_key(str(meta["post_id"]), candidate)
            adopted = DownloadState(state_path).get_existing_entry(new_key)
            self.assertIsNotNone(adopted)
            self.assertEqual(existing_file.resolve(), Path(str(adopted["path"])).resolve())

            sidecar_path = output_dir / POST_SIDECAR_NAME
            self.assertTrue(sidecar_path.is_file())
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(meta["creator_id"], sidecar["creator"]["id"])
            self.assertEqual(meta["post_id"], sidecar["post"]["id"])
            self.assertEqual("archive.zip", sidecar["assets"][new_key]["file"])

    def test_deleted_id_only_duplicate_falls_back_to_the_old_readable_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meta = make_post_meta()
            output_dir = preferred_post_dir(root, meta)
            output_dir.mkdir(parents=True)
            existing_file = output_dir / "archive.zip"
            existing_file.write_bytes(b"pre-upgrade")
            candidate = archive_candidate()
            current_key = download_key(str(meta["post_id"]), candidate)
            state_path = root / "download_state.json"
            state = DownloadState(state_path)
            state.data["downloads"][current_key] = {
                "path": str(root / "creator-opaque" / "post-opaque" / "archive.zip"),
                "bytes": 999,
                "post_id": meta["post_id"],
                "post_title": meta["post_title"],
                "identity_version": 2,
                "asset_key": current_key,
                "asset_locator": candidate.asset_locator,
            }
            state.save()

            with patch("afdian_config_common.download_candidate") as mocked_download, patch(
                "builtins.print"
            ):
                records = download_candidates_for_post(
                    session=None,
                    candidates=[candidate],
                    output_dir=output_dir,
                    state=state,
                    config={"skip_existing": True},
                    post_meta=meta,
                )

            mocked_download.assert_not_called()
            self.assertEqual("already-downloaded", records[0]["status"])
            refreshed = DownloadState(state_path).get_existing_entry(current_key)
            self.assertEqual(existing_file.resolve(), Path(str(refreshed["path"])).resolve())

    def test_existing_id_only_file_is_not_moved_or_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meta = make_post_meta()
            readable_dir = preferred_post_dir(root, meta)
            id_only_dir = root / "creator-opaque" / "post-opaque"
            id_only_dir.mkdir(parents=True)
            id_only_file = id_only_dir / "archive.zip"
            id_only_file.write_bytes(b"temporary duplicate")
            candidate = archive_candidate()
            current_key = download_key(str(meta["post_id"]), candidate)
            state = DownloadState(root / "download_state.json")
            state.data["downloads"][current_key] = {
                "path": str(id_only_file),
                "bytes": id_only_file.stat().st_size,
                "post_id": meta["post_id"],
                "post_title": meta["post_title"],
                "identity_version": 2,
                "asset_key": current_key,
                "asset_locator": candidate.asset_locator,
            }

            with patch("afdian_config_common.download_candidate") as mocked_download, patch(
                "builtins.print"
            ):
                records = download_candidates_for_post(
                    session=None,
                    candidates=[candidate],
                    output_dir=readable_dir,
                    state=state,
                    config={"skip_existing": True},
                    post_meta=meta,
                )

            mocked_download.assert_not_called()
            self.assertEqual("already-downloaded", records[0]["status"])
            self.assertTrue(id_only_file.is_file())
            self.assertFalse(readable_dir.exists())

    def test_missing_state_adopts_pre_ffed164_doubled_extension_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meta = make_post_meta()
            output_dir = preferred_post_dir(root, meta)
            output_dir.mkdir(parents=True)
            candidates = [
                Candidate(
                    url="https://cdn.example/archive.zip",
                    source=f"api:attachment[{index}].url",
                    filename_hint="archive.zip",
                    asset_locator=f"api:attachment[{index}]",
                )
                for index in (1, 2)
            ]
            legacy_files = [output_dir / "archive.zip.zip", output_dir / "archive.zip-1.zip"]
            for index, file_path in enumerate(legacy_files, start=1):
                file_path.write_bytes(bytes([index]))
            state = DownloadState(root / "download_state.json")

            with patch("afdian_config_common.download_candidate") as mocked_download, patch(
                "builtins.print"
            ):
                records = download_candidates_for_post(
                    session=None,
                    candidates=candidates,
                    output_dir=output_dir,
                    state=state,
                    config={"skip_existing": True},
                    post_meta=meta,
                )

            mocked_download.assert_not_called()
            self.assertEqual(
                ["already-downloaded", "already-downloaded"],
                [record["status"] for record in records],
            )
            self.assertEqual(
                [path.resolve() for path in legacy_files],
                [Path(record["path"]).resolve() for record in records],
            )

    def test_dry_run_does_not_alias_old_state_or_write_a_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meta = make_post_meta()
            output_dir = preferred_post_dir(root, meta)
            output_dir.mkdir(parents=True)
            existing_file = output_dir / "archive.zip"
            existing_file.write_bytes(b"already downloaded")
            candidate = archive_candidate()
            old_key = download_key_v1(str(meta["post_id"]), candidate)
            state_path = root / "download_state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "downloads": {
                            old_key: {
                                "path": str(existing_file),
                                "bytes": existing_file.stat().st_size,
                                "post_id": meta["post_id"],
                                "post_title": meta["post_title"],
                                "url": candidate.url,
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            original_state_text = state_path.read_text(encoding="utf-8")
            state = DownloadState(state_path)

            with patch("afdian_config_common.download_candidate") as mocked_download, patch(
                "builtins.print"
            ):
                records = download_candidates_for_post(
                    session=None,
                    candidates=[candidate],
                    output_dir=output_dir,
                    state=state,
                    config={"skip_existing": True, "dry_run": True},
                    post_meta=meta,
                )

            mocked_download.assert_not_called()
            self.assertEqual("already-downloaded", records[0]["status"])
            self.assertNotIn(download_key(str(meta["post_id"]), candidate), state.data["downloads"])
            self.assertEqual(original_state_text, state_path.read_text(encoding="utf-8"))
            self.assertFalse((output_dir / POST_SIDECAR_NAME).exists())

    def test_dry_run_missing_state_fallback_writes_neither_state_nor_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meta = make_post_meta()
            output_dir = preferred_post_dir(root, meta)
            output_dir.mkdir(parents=True)
            existing_file = output_dir / "archive.zip"
            existing_file.write_bytes(b"already downloaded")
            candidate = archive_candidate()
            state_path = root / "download_state.json"
            state = DownloadState(state_path)

            with patch("afdian_config_common.download_candidate") as mocked_download, patch(
                "builtins.print"
            ):
                records = download_candidates_for_post(
                    session=None,
                    candidates=[candidate],
                    output_dir=output_dir,
                    state=state,
                    config={"skip_existing": True, "dry_run": True},
                    post_meta=meta,
                )

            mocked_download.assert_not_called()
            self.assertEqual("already-downloaded", records[0]["status"])
            self.assertFalse(state_path.exists())
            self.assertNotIn(download_key(str(meta["post_id"]), candidate), state.data["downloads"])
            self.assertFalse((output_dir / POST_SIDECAR_NAME).exists())

    def test_only_partial_file_keeps_preferred_directory_and_retries_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meta = make_post_meta()
            output_dir = preferred_post_dir(root, meta)
            output_dir.mkdir(parents=True)
            partial_file = output_dir / "archive.zip.part"
            partial_file.write_bytes(b"partial")
            candidate = archive_candidate()
            state = DownloadState(root / "download_state.json")

            def finish_download(**kwargs):
                self.assertEqual(output_dir, kwargs["output_dir"])
                final_file = kwargs["output_dir"] / "archive.zip"
                final_file.write_bytes(b"complete")
                return {
                    "status": "downloaded",
                    "url": kwargs["candidate"].url,
                    "path": str(final_file),
                    "bytes": final_file.stat().st_size,
                }

            with patch(
                "afdian_config_common.download_candidate", side_effect=finish_download
            ) as mocked_download, patch("builtins.print"):
                records = download_candidates_for_post(
                    session=None,
                    candidates=[candidate],
                    output_dir=output_dir,
                    state=state,
                    config={"skip_existing": True},
                    post_meta=meta,
                )

            mocked_download.assert_called_once()
            self.assertEqual("downloaded", records[0]["status"])
            self.assertEqual(output_dir.resolve(), Path(records[0]["path"]).resolve().parent)


if __name__ == "__main__":
    unittest.main()
