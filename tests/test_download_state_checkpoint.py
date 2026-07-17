from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from afdian_config_common import DownloadState, creator_scan_requires_full_scan


class DownloadStateCheckpointTests(unittest.TestCase):
    def test_checkpoint_is_persisted_and_isolated_by_creator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "download_state.json"
            state = DownloadState(state_path)
            state.mark_creator_scan(
                "creator-1",
                ["post-1"],
                100,
                full_scan=True,
                include_images=False,
            )
            state.mark_creator_scan(
                "creator-2",
                ["post-other"],
                150,
                full_scan=True,
                include_images=False,
            )
            state.mark_creator_scan(
                "creator-1",
                ["post-2", "post-1"],
                200,
                full_scan=False,
                include_images=False,
            )
            state.save()

            reloaded = DownloadState(state_path)

        creator_checkpoint = reloaded.get_creator_scan("creator-1")
        other_checkpoint = reloaded.get_creator_scan("creator-2")

        self.assertEqual("post-2", creator_checkpoint["checkpoint_post_id"])
        self.assertEqual(["post-2", "post-1"], creator_checkpoint["checkpoint_post_ids"])
        self.assertEqual(200, creator_checkpoint["checkpoint_publish_time"])
        self.assertEqual("post-other", other_checkpoint["checkpoint_post_id"])
        self.assertIsNone(reloaded.get_creator_scan("missing-creator"))

    def test_inaccessible_posts_can_be_listed_for_one_creator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = DownloadState(Path(temp_dir) / "download_state.json")
            state.mark_inaccessible(
                {"creator_id": "creator-1", "post_id": "post-1", "post_title": "One"}
            )
            state.mark_inaccessible(
                {"creator_id": "creator-1", "post_id": "post-2", "post_title": "Two"}
            )
            state.mark_inaccessible(
                {"creator_id": "creator-2", "post_id": "post-3", "post_title": "Three"}
            )

            creator_entries = state.list_inaccessible_posts("creator-1")

        self.assertEqual({"post-1", "post-2"}, {entry["post_id"] for entry in creator_entries})
        self.assertTrue(all(entry["creator_id"] == "creator-1" for entry in creator_entries))

    def test_full_scan_compatibility_uses_interval_identity_version_and_image_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = DownloadState(Path(temp_dir) / "download_state.json")
            state.mark_creator_scan(
                "creator-1",
                ["post-1"],
                100,
                full_scan=True,
                include_images=False,
            )
            checkpoint = state.get_creator_scan("creator-1")
            checkpoint["last_full_scan_at"] = "2000-01-01T00:00:00+00:00"

            self.assertFalse(
                creator_scan_requires_full_scan(checkpoint, full_scan_days=0, include_images=False)
            )
            self.assertTrue(
                creator_scan_requires_full_scan(checkpoint, full_scan_days=30, include_images=False)
            )
            self.assertTrue(
                creator_scan_requires_full_scan(checkpoint, full_scan_days=0, include_images=True)
            )

            checkpoint["asset_identity_version"] = 1
            self.assertTrue(
                creator_scan_requires_full_scan(checkpoint, full_scan_days=0, include_images=False)
            )


if __name__ == "__main__":
    unittest.main()
