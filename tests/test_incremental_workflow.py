from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from afdian_config_common import DownloadState, run_creator
from afdian_downloader import Candidate, FeedPost, FeedScanResult


def creator_config(root: Path) -> tuple[dict[str, object], dict[str, object]]:
    config = {
        "download_dir": str(root / "downloads"),
        "state_file": "download_state.json",
        "skip_existing": True,
        "overwrite": False,
        "dry_run": False,
        "include_images": False,
        "incremental_scan": True,
        "incremental_lookback_posts": 2,
        "incremental_full_scan_days": 0,
    }
    return config, {"url": "https://www.ifdian.net/a/creator"}


def feed_post(post_id: str) -> FeedPost:
    return FeedPost(
        post_id=post_id,
        title=post_id,
        publish_time=100,
        publish_sn=f"sn-{post_id}",
        url=f"https://www.ifdian.net/p/{post_id}",
        raw={},
    )


def scan_result(posts: list[FeedPost], checkpoint_ids: list[str]) -> FeedScanResult:
    return FeedScanResult(
        posts=posts,
        checkpoint_post_ids=checkpoint_ids,
        checkpoint_publish_time=max((post.publish_time for post in posts), default=100),
        incremental_boundary_reached=bool(checkpoint_ids),
        checkpoint_safe=True,
        stop_reason="feed-exhausted",
    )


class IncrementalWorkflowTests(unittest.TestCase):
    def common_patches(self):
        return (
            patch("afdian_config_common.create_session_from_config", return_value=object()),
            patch(
                "afdian_config_common.get_creator_profile",
                return_value={"user_id": "creator-1", "name": "Creator"},
            ),
            patch("afdian_config_common.append_manifest"),
            patch("afdian_config_common.bark_notify", return_value=False),
        )

    def test_successful_full_scan_advances_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config, target = creator_config(root)
            post = feed_post("post-new")
            detail = {
                "post_id": post.post_id,
                "title": post.title,
                "publish_time": post.publish_time,
                "has_right": 1,
                "attachment": [],
            }
            session_patch, profile_patch, manifest_patch, bark_patch = self.common_patches()
            with session_patch, profile_patch, manifest_patch, bark_patch, patch(
                "afdian_config_common.scan_feed_posts_api",
                return_value=scan_result([post], [post.post_id]),
            ), patch("afdian_config_common.fetch_post_detail_api", return_value=detail):
                records = run_creator(config, root, target)

            state = DownloadState(root / "downloads" / "download_state.json")
            checkpoint = state.get_creator_scan("creator-1")
            self.assertEqual("no-files", records[0]["status"])
            self.assertEqual("post-new", checkpoint["checkpoint_post_id"])

    def test_regular_feed_download_failure_does_not_advance_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config, target = creator_config(root)
            post = feed_post("post-failed")
            detail = {"post_id": post.post_id, "title": post.title, "publish_time": 100, "has_right": 1}
            candidate = Candidate(
                url="https://cdn.example/file.zip",
                source="api:attachment[1].url",
                asset_locator="api:attachment[1]",
            )
            failed_record = {"status": "failed", "post_id": post.post_id, "error": "network"}
            session_patch, profile_patch, manifest_patch, bark_patch = self.common_patches()
            with session_patch, profile_patch, manifest_patch, bark_patch, patch(
                "afdian_config_common.scan_feed_posts_api",
                return_value=scan_result([post], [post.post_id]),
            ), patch("afdian_config_common.fetch_post_detail_api", return_value=detail), patch(
                "afdian_config_common.candidates_from_post_detail", return_value=[candidate]
            ), patch("afdian_config_common.download_candidates_for_post", return_value=[failed_record]):
                run_creator(config, root, target)

            state = DownloadState(root / "downloads" / "download_state.json")
            self.assertIsNone(state.get_creator_scan("creator-1"))

    def test_inaccessible_retry_is_cleared_only_after_download_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config, target = creator_config(root)
            state_path = root / "downloads" / "download_state.json"
            state = DownloadState(state_path)
            state.mark_inaccessible(
                {
                    "creator_id": "creator-1",
                    "creator_name": "Creator",
                    "post_id": "post-old",
                    "post_title": "Old",
                    "post_url": "https://www.ifdian.net/p/post-old",
                    "publish_time": 50,
                }
            )
            state.mark_creator_scan(
                "creator-1",
                ["post-head"],
                100,
                full_scan=True,
                include_images=False,
            )
            state.save()
            detail = {"post_id": "post-old", "title": "Old", "publish_time": 50, "has_right": 1}
            candidate = Candidate(
                url="https://cdn.example/old.zip",
                source="api:attachment[1].url",
                asset_locator="api:attachment[1]",
            )
            retry_scan = scan_result([], ["post-head"])
            session_patch, profile_patch, manifest_patch, bark_patch = self.common_patches()
            with session_patch, profile_patch, manifest_patch, bark_patch, patch(
                "afdian_config_common.scan_feed_posts_api", return_value=retry_scan
            ), patch("afdian_config_common.fetch_post_detail_api", return_value=detail), patch(
                "afdian_config_common.candidates_from_post_detail", return_value=[candidate]
            ), patch(
                "afdian_config_common.download_candidates_for_post",
                return_value=[{"status": "failed", "post_id": "post-old", "error": "network"}],
            ):
                run_creator(config, root, target)

            failed_state = DownloadState(state_path)
            self.assertIsNotNone(failed_state.get_inaccessible_post("post-old"))

            session_patch, profile_patch, manifest_patch, bark_patch = self.common_patches()
            with session_patch, profile_patch, manifest_patch, bark_patch, patch(
                "afdian_config_common.scan_feed_posts_api", return_value=retry_scan
            ), patch("afdian_config_common.fetch_post_detail_api", return_value=detail), patch(
                "afdian_config_common.candidates_from_post_detail", return_value=[candidate]
            ), patch(
                "afdian_config_common.download_candidates_for_post",
                return_value=[{"status": "already-downloaded", "post_id": "post-old"}],
            ):
                run_creator(config, root, target)

            successful_state = DownloadState(state_path)
            self.assertIsNone(successful_state.get_inaccessible_post("post-old"))

    def test_manual_or_destructive_options_do_not_use_or_advance_checkpoint(self) -> None:
        cases = {
            "since": {"since": "2026-01-01"},
            "until": {"until": "2026-12-31"},
            "max-posts": {"max_posts": 1},
            "stop-post": {"stop_post_id": "stop-here"},
            "skip-existing-false": {"skip_existing": False},
            "overwrite": {"overwrite": True},
            "incremental-disabled": {"incremental_scan": False},
        }
        for label, overrides in cases.items():
            with self.subTest(option=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config, target = creator_config(root)
                config.update(overrides)
                state_path = root / "downloads" / "download_state.json"
                state = DownloadState(state_path)
                state.mark_creator_scan(
                    "creator-1",
                    ["post-old"],
                    100,
                    full_scan=True,
                    include_images=False,
                )
                state.save()
                post = feed_post("post-new")
                detail = {
                    "post_id": post.post_id,
                    "title": post.title,
                    "publish_time": post.publish_time,
                    "has_right": 1,
                    "attachment": [],
                }
                session_patch, profile_patch, manifest_patch, bark_patch = self.common_patches()
                with session_patch, profile_patch, manifest_patch, bark_patch, patch(
                    "afdian_config_common.scan_feed_posts_api",
                    return_value=scan_result([post], [post.post_id]),
                ) as mocked_scan, patch(
                    "afdian_config_common.fetch_post_detail_api",
                    return_value=detail,
                ):
                    run_creator(config, root, target)

                scan_kwargs = mocked_scan.call_args.kwargs
                self.assertEqual(set(), scan_kwargs["known_post_ids"])
                self.assertEqual(0, scan_kwargs["incremental_lookback"])
                checkpoint = DownloadState(state_path).get_creator_scan("creator-1")
                self.assertEqual("post-old", checkpoint["checkpoint_post_id"])

    def test_manifest_failure_preserves_previous_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config, target = creator_config(root)
            state_path = root / "downloads" / "download_state.json"
            state = DownloadState(state_path)
            state.mark_creator_scan(
                "creator-1",
                ["post-old"],
                100,
                full_scan=True,
                include_images=False,
            )
            state.save()
            post = feed_post("post-new")
            detail = {
                "post_id": post.post_id,
                "title": post.title,
                "publish_time": post.publish_time,
                "has_right": 1,
                "attachment": [],
            }
            with patch("afdian_config_common.create_session_from_config", return_value=object()), patch(
                "afdian_config_common.get_creator_profile",
                return_value={"user_id": "creator-1", "name": "Creator"},
            ), patch(
                "afdian_config_common.scan_feed_posts_api",
                return_value=scan_result([post], [post.post_id]),
            ), patch(
                "afdian_config_common.fetch_post_detail_api",
                return_value=detail,
            ), patch(
                "afdian_config_common.append_manifest",
                side_effect=OSError("manifest unavailable"),
            ), patch("afdian_config_common.bark_notify", return_value=False):
                with self.assertRaisesRegex(OSError, "manifest unavailable"):
                    run_creator(config, root, target)

            checkpoint = DownloadState(state_path).get_creator_scan("creator-1")
            self.assertEqual("post-old", checkpoint["checkpoint_post_id"])


if __name__ == "__main__":
    unittest.main()
