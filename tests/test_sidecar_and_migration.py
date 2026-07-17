from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from afdian_config_common import (
    DownloadState,
    claim_post_directory,
    download_candidates_for_post,
    download_key,
    download_key_v1,
    existing_candidate_file,
    record_sidecar_asset,
    safe_sidecar_asset_path,
)
from afdian_downloader import Candidate


def post_meta() -> dict[str, object]:
    return {
        "creator_id": "creator-1",
        "creator_name": "Creator",
        "post_id": "post-1",
        "post_title": "Post",
        "post_url": "https://www.ifdian.net/p/post-1",
        "publish_time": 100,
        "publish_date": "1970-01-01",
    }


class PostSidecarTests(unittest.TestCase):
    def test_sidecar_records_and_resolves_one_asset_inside_the_post_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "post"
            sidecar = claim_post_directory(output_dir, post_meta(), dry_run=False)
            candidate = Candidate(
                url="https://cdn.example/archive.zip",
                source="api:attachment[1].url",
                filename_hint="archive.zip",
                asset_locator="api:attachment[1]",
            )
            key = download_key("post-1", candidate)
            downloaded_file = output_dir / "archive.zip"
            downloaded_file.write_bytes(b"data")
            record = {"path": str(downloaded_file), "bytes": 4}

            record_sidecar_asset(output_dir, sidecar, key, candidate, record)
            reloaded = claim_post_directory(output_dir, post_meta(), dry_run=True)

            self.assertEqual(downloaded_file.resolve(), safe_sidecar_asset_path(output_dir, reloaded, key))
            self.assertEqual(
                downloaded_file.resolve(),
                existing_candidate_file(output_dir, candidate, 0, key, reloaded).resolve(),
            )

    def test_nonempty_directory_without_sidecar_is_not_silently_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "post"
            output_dir.mkdir()
            (output_dir / "unknown.zip").write_bytes(b"unknown")

            with self.assertRaisesRegex(RuntimeError, "non-empty post directory"):
                claim_post_directory(output_dir, post_meta(), dry_run=False)

    def test_sidecar_asset_path_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "post"
            output_dir.mkdir()
            outside = Path(temp_dir) / "outside.zip"
            outside.write_bytes(b"outside")
            sidecar = {"assets": {"v2:key": {"file": "../outside.zip"}}}

            self.assertIsNone(safe_sidecar_asset_path(output_dir, sidecar, "v2:key"))

    def test_sidecar_rejects_post_or_creator_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "post"
            claim_post_directory(output_dir, post_meta(), dry_run=False)

            mismatched_post = {**post_meta(), "post_id": "post-2"}
            with self.assertRaisesRegex(RuntimeError, "Post sidecar ID mismatch"):
                claim_post_directory(output_dir, mismatched_post, dry_run=True)

            mismatched_creator = {**post_meta(), "creator_id": "creator-2"}
            with self.assertRaisesRegex(RuntimeError, "creator ID mismatch"):
                claim_post_directory(output_dir, mismatched_creator, dry_run=True)

    def test_filename_fallback_does_not_hide_a_replaced_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "post"
            sidecar = claim_post_directory(output_dir, post_meta(), dry_run=False)
            old_candidate = Candidate(
                url="https://cdn.example/archive.zip?version=old",
                source="api:attachment[1].url",
                filename_hint="archive.zip",
                asset_locator="api:attachment[1]",
            )
            old_file = output_dir / "archive.zip"
            old_file.write_bytes(b"old")
            record_sidecar_asset(
                output_dir,
                sidecar,
                download_key("post-1", old_candidate),
                old_candidate,
                {"path": str(old_file), "bytes": 3},
            )
            replacement = Candidate(
                url="https://cdn.example/archive.zip?version=new",
                source=old_candidate.source,
                filename_hint=old_candidate.filename_hint,
                asset_locator=old_candidate.asset_locator,
            )

            def fake_download_candidate(**kwargs):
                target = kwargs["output_dir"] / "archive-1.zip"
                target.write_bytes(b"new")
                return {
                    "status": "downloaded",
                    "url": kwargs["candidate"].url,
                    "path": str(target),
                    "bytes": 3,
                }

            with patch(
                "afdian_config_common.download_candidate",
                side_effect=fake_download_candidate,
            ) as mocked_download:
                records = download_candidates_for_post(
                    session=None,
                    candidates=[replacement],
                    output_dir=output_dir,
                    state=DownloadState(root / "state.json"),
                    config={"skip_existing": True},
                    post_meta=post_meta(),
                )

            mocked_download.assert_called_once()
            self.assertEqual("downloaded", records[0]["status"])
            self.assertNotEqual(old_file.resolve(), Path(records[0]["path"]).resolve())


class LegacyStateMigrationTests(unittest.TestCase):
    def test_unique_v1_entry_is_aliased_after_a_title_change_without_redownload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = DownloadState(root / "state.json")
            old_candidate = Candidate(
                url="https://cdn.example/archive.zip",
                source="api:attachment[1].url",
                filename_hint="Old title",
                asset_locator="api:attachment[1]",
            )
            old_key = download_key_v1("post-1", old_candidate)
            old_file = root / "old-layout.zip"
            old_file.write_bytes(b"old")
            state.data["downloads"][old_key] = {
                "path": str(old_file),
                "bytes": 3,
                "post_id": "post-1",
                "post_title": "Old title",
                "url": "https://cdn.example/archive.zip",
            }
            state.save()
            renamed_candidate = Candidate(
                url=old_candidate.url,
                source=old_candidate.source,
                filename_hint="New title",
                asset_locator=old_candidate.asset_locator,
            )

            with patch("afdian_config_common.download_candidate") as mocked_download:
                records = download_candidates_for_post(
                    session=None,
                    candidates=[renamed_candidate],
                    output_dir=root / "new-layout",
                    state=state,
                    config={"skip_existing": True},
                    post_meta=post_meta(),
                )

            mocked_download.assert_not_called()
            self.assertEqual("already-downloaded", records[0]["status"])
            self.assertEqual(str(old_file), records[0]["path"])
            self.assertIn(download_key("post-1", renamed_candidate), state.data["downloads"])

    def test_complete_signed_url_can_migrate_when_no_functional_query_remains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = DownloadState(root / "state.json")

            def signed_url(credential: str, signature: str) -> str:
                return (
                    "https://cdn.example/archive.zip?"
                    "X-Amz-Algorithm=AWS4-HMAC-SHA256"
                    f"&X-Amz-Credential={credential}"
                    "&X-Amz-Date=20260718T000000Z"
                    "&X-Amz-Expires=60"
                    "&X-Amz-SignedHeaders=host"
                    f"&X-Amz-Signature={signature}"
                )

            old_candidate = Candidate(
                url=signed_url("old", "old-signature"),
                source="api:attachment[1].url",
                filename_hint="archive",
                asset_locator="api:attachment[1]",
            )
            old_file = root / "old.zip"
            old_file.write_bytes(b"old")
            state.data["downloads"][download_key_v1("post-1", old_candidate)] = {
                "path": str(old_file),
                "bytes": 3,
                "post_id": "post-1",
                "post_title": "archive",
                "url": "https://cdn.example/archive.zip",
            }
            refreshed = Candidate(
                url=signed_url("new", "new-signature"),
                source=old_candidate.source,
                filename_hint=old_candidate.filename_hint,
                asset_locator=old_candidate.asset_locator,
            )

            with patch("afdian_config_common.download_candidate") as mocked_download:
                records = download_candidates_for_post(
                    session=None,
                    candidates=[refreshed],
                    output_dir=root / "new-layout",
                    state=state,
                    config={"skip_existing": True},
                    post_meta=post_meta(),
                )

            mocked_download.assert_not_called()
            self.assertEqual("already-downloaded", records[0]["status"])
            self.assertEqual(old_file.resolve(), Path(records[0]["path"]).resolve())

    def test_functional_query_change_is_not_migrated_from_query_blind_v1_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = DownloadState(root / "state.json")
            old_candidate = Candidate(
                url="https://cdn.example/download?id=old",
                source="api:attachment[1].url",
                filename_hint="archive",
                asset_locator="api:attachment[1]",
            )
            old_file = root / "old.bin"
            old_file.write_bytes(b"old")
            state.data["downloads"][download_key_v1("post-1", old_candidate)] = {
                "path": str(old_file),
                "bytes": 3,
                "post_id": "post-1",
                "post_title": "archive",
                "url": "https://cdn.example/download",
            }
            replacement = Candidate(
                url="https://cdn.example/download?id=new",
                source=old_candidate.source,
                filename_hint=old_candidate.filename_hint,
                asset_locator=old_candidate.asset_locator,
            )

            def fake_download_candidate(**kwargs):
                target = kwargs["output_dir"] / "new.bin"
                target.write_bytes(b"new")
                return {
                    "status": "downloaded",
                    "url": kwargs["candidate"].url,
                    "path": str(target),
                    "bytes": 3,
                }

            with patch(
                "afdian_config_common.download_candidate",
                side_effect=fake_download_candidate,
            ) as mocked_download:
                records = download_candidates_for_post(
                    session=None,
                    candidates=[replacement],
                    output_dir=root / "new-layout",
                    state=state,
                    config={"skip_existing": True},
                    post_meta=post_meta(),
                )

            mocked_download.assert_called_once()
            self.assertEqual("downloaded", records[0]["status"])
            self.assertNotEqual(old_file.resolve(), Path(records[0]["path"]).resolve())

    def test_one_physical_legacy_file_cannot_be_claimed_through_two_path_spellings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = DownloadState(root / "state.json")
            shared_dir = root / "shared"
            shared_dir.mkdir()
            shared_file = shared_dir / "asset.bin"
            shared_file.write_bytes(b"old")
            candidates = [
                Candidate(
                    url=f"https://cdn.example/asset-{index}.bin",
                    source=f"api:attachment[{index}].url",
                    filename_hint=f"asset-{index}",
                    asset_locator=f"api:attachment[{index}]",
                )
                for index in (1, 2)
            ]
            path_spellings = [
                str(shared_file),
                str(shared_dir / ".." / "shared" / "asset.bin"),
            ]
            for candidate, path_value in zip(candidates, path_spellings):
                state.data["downloads"][download_key_v1("post-1", candidate)] = {
                    "path": path_value,
                    "bytes": 3,
                    "post_id": "post-1",
                    "post_title": candidate.filename_hint,
                    "url": candidate.url,
                }

            def fake_download_candidate(**kwargs):
                target = kwargs["output_dir"] / "second.bin"
                target.write_bytes(b"new")
                return {
                    "status": "downloaded",
                    "url": kwargs["candidate"].url,
                    "path": str(target),
                    "bytes": 3,
                }

            with patch(
                "afdian_config_common.download_candidate",
                side_effect=fake_download_candidate,
            ) as mocked_download:
                records = download_candidates_for_post(
                    session=None,
                    candidates=candidates,
                    output_dir=root / "new-layout",
                    state=state,
                    config={"skip_existing": True},
                    post_meta=post_meta(),
                )

            mocked_download.assert_called_once()
            self.assertEqual(
                ["already-downloaded", "downloaded"],
                [record["status"] for record in records],
            )
            resolved_paths = [Path(record["path"]).resolve() for record in records]
            self.assertEqual(1, resolved_paths.count(shared_file.resolve()))

    def test_ambiguous_v1_entry_is_not_claimed_by_two_functional_query_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = DownloadState(root / "state.json")
            candidates = [
                Candidate(
                    url=f"https://cdn.example/download?id={index}",
                    source=f"api:attachment[{index}].url",
                    filename_hint="archive",
                    asset_locator=f"api:attachment[{index}]",
                )
                for index in (1, 2)
            ]
            ambiguous_key = download_key_v1("post-1", candidates[0])
            old_file = root / "ambiguous-old-file.bin"
            old_file.write_bytes(b"old")
            state.data["downloads"][ambiguous_key] = {
                "path": str(old_file),
                "bytes": 3,
                "post_id": "post-1",
                "post_title": "archive",
                "url": "https://cdn.example/download",
            }
            output_dir = root / "new-layout"
            download_index = 0

            def fake_download_candidate(**kwargs):
                nonlocal download_index
                download_index += 1
                target = kwargs["output_dir"] / f"archive-{download_index}.bin"
                target.write_bytes(bytes([download_index]))
                return {
                    "status": "downloaded",
                    "url": kwargs["candidate"].url,
                    "path": str(target),
                    "bytes": 1,
                }

            with patch("afdian_config_common.download_candidate", side_effect=fake_download_candidate) as mocked_download:
                records = download_candidates_for_post(
                    session=None,
                    candidates=candidates,
                    output_dir=output_dir,
                    state=state,
                    config={"skip_existing": True},
                    post_meta=post_meta(),
                )

            self.assertEqual(2, mocked_download.call_count)
            self.assertEqual(["downloaded", "downloaded"], [record["status"] for record in records])
            self.assertNotIn(str(old_file), {record["path"] for record in records})


if __name__ == "__main__":
    unittest.main()
