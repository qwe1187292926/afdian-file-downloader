from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse, urlunparse
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
    save_post_sidecar,
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


def ifdian_vod_candidate(sign: str, timestamp: str, user_signature: str) -> Candidate:
    return Candidate(
        url=(
            "https://vod.afdiancdn.com/video/asset.mp4"
            f"?sign={sign}&t={timestamp}&us={user_signature}"
        ),
        source="api:video",
        filename_hint="video",
        asset_locator="api:video",
    )


def pre_fix_v2_key(post_id: str, candidate: Candidate) -> str:
    parsed = urlparse(candidate.url)
    old_canonical_url = urlunparse(
        parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            fragment="",
        )
    )
    identity = f"locator:{candidate.asset_locator}|url:{old_canonical_url}"
    source = "\0".join(["v2", post_id, identity])
    return "v2:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


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


class DynamicV2IdentityMigrationTests(unittest.TestCase):
    def test_signature_rotation_downloads_once_across_two_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "post"
            state = DownloadState(root / "state.json")
            first = ifdian_vod_candidate("first", "100", "user-a")
            refreshed = ifdian_vod_candidate("second", "200", "user-b")

            def fake_download_candidate(**kwargs):
                target = kwargs["output_dir"] / "video.mp4"
                target.write_bytes(b"video")
                return {
                    "status": "downloaded",
                    "url": kwargs["candidate"].url,
                    "path": str(target),
                    "bytes": target.stat().st_size,
                }

            with patch(
                "afdian_config_common.download_candidate",
                side_effect=fake_download_candidate,
            ) as mocked_download:
                first_records = download_candidates_for_post(
                    session=None,
                    candidates=[first],
                    output_dir=output_dir,
                    state=state,
                    config={"skip_existing": True},
                    post_meta=post_meta(),
                )
                second_records = download_candidates_for_post(
                    session=None,
                    candidates=[refreshed],
                    output_dir=output_dir,
                    state=state,
                    config={"skip_existing": True},
                    post_meta=post_meta(),
                )

            mocked_download.assert_called_once()
            self.assertEqual("downloaded", first_records[0]["status"])
            self.assertEqual("already-downloaded", second_records[0]["status"])
            self.assertEqual(download_key("post-1", first), download_key("post-1", refreshed))

    def test_old_dynamic_v2_entries_migrate_to_the_stable_key_and_prefer_base_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "post"
            output_dir.mkdir()
            state_path = root / "state.json"
            state = DownloadState(state_path)
            old_candidates = [
                ifdian_vod_candidate(f"sign-{index}", str(100 + index), f"user-{index}")
                for index in range(4)
            ]
            old_files = [
                output_dir / ("video.mp4" if index == 0 else f"video-{index}.mp4")
                for index in range(4)
            ]
            for index, (candidate, path) in enumerate(zip(old_candidates, old_files)):
                path.write_bytes(bytes([index]))
                old_key = pre_fix_v2_key("post-1", candidate)
                state.data["downloads"][old_key] = {
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "post_id": "post-1",
                    "post_title": "Post",
                    "url": candidate.url,
                    "identity_version": 2,
                    "asset_key": old_key,
                    "asset_locator": candidate.asset_locator,
                    "downloaded_at": f"2026-07-18T00:00:0{index}+08:00",
                }
            state.save()
            refreshed = ifdian_vod_candidate("fresh", "999", "fresh-user")

            with patch("afdian_config_common.download_candidate") as mocked_download:
                records = download_candidates_for_post(
                    session=None,
                    candidates=[refreshed],
                    output_dir=output_dir,
                    state=state,
                    config={"skip_existing": True},
                    post_meta=post_meta(),
                )

            mocked_download.assert_not_called()
            self.assertEqual("already-downloaded", records[0]["status"])
            self.assertEqual(old_files[0].resolve(), Path(records[0]["path"]).resolve())
            migrated = DownloadState(state_path).get_existing_entry(download_key("post-1", refreshed))
            self.assertIsNotNone(migrated)
            self.assertEqual(old_files[0].resolve(), Path(str(migrated["path"])).resolve())
            self.assertTrue(all(path.is_file() for path in old_files))
            self.assertEqual(
                [bytes([index]) for index in range(4)],
                [path.read_bytes() for path in old_files],
            )

    def test_existing_base_file_beats_only_suffixed_dynamic_v2_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "post"
            sidecar = claim_post_directory(output_dir, post_meta(), dry_run=False)
            base_file = output_dir / "video.mp4"
            base_file.write_bytes(b"original")
            duplicate_files: list[Path] = []
            state_path = root / "state.json"
            state = DownloadState(state_path)

            for index in range(1, 4):
                old_candidate = ifdian_vod_candidate(
                    f"sign-{index}",
                    str(100 + index),
                    f"user-{index}",
                )
                old_key = pre_fix_v2_key("post-1", old_candidate)
                duplicate_file = output_dir / f"video-{index}.mp4"
                duplicate_file.write_bytes(bytes([index]))
                duplicate_files.append(duplicate_file)
                state.data["downloads"][old_key] = {
                    "path": str(duplicate_file),
                    "bytes": duplicate_file.stat().st_size,
                    "post_id": "post-1",
                    "post_title": "Post",
                    "url": old_candidate.url,
                    "identity_version": 2,
                    "asset_key": old_key,
                    "asset_locator": old_candidate.asset_locator,
                }
                sidecar["assets"][old_key] = {
                    "identity_url": old_candidate.url,
                    "asset_locator": old_candidate.asset_locator,
                    "source": old_candidate.source,
                    "filename_hint": old_candidate.filename_hint,
                    "file": duplicate_file.name,
                    "bytes": duplicate_file.stat().st_size,
                }

            state.save()
            save_post_sidecar(output_dir, sidecar)
            refreshed = ifdian_vod_candidate("fresh", "999", "fresh-user")

            with patch("afdian_config_common.download_candidate") as mocked_download:
                records = download_candidates_for_post(
                    session=None,
                    candidates=[refreshed],
                    output_dir=output_dir,
                    state=state,
                    config={"skip_existing": True},
                    post_meta=post_meta(),
                )

            mocked_download.assert_not_called()
            self.assertEqual("already-downloaded", records[0]["status"])
            self.assertEqual(base_file.resolve(), Path(records[0]["path"]).resolve())
            self.assertTrue(all(path.is_file() for path in [base_file, *duplicate_files]))
            migrated = DownloadState(state_path).get_existing_entry(download_key("post-1", refreshed))
            self.assertIsNotNone(migrated)
            self.assertEqual(base_file.resolve(), Path(str(migrated["path"])).resolve())

    def test_dynamic_v2_state_migration_rejects_nonmatching_identity_fields(self) -> None:
        refreshed = ifdian_vod_candidate("fresh", "999", "fresh-user")
        cases = {
            "different-post": {"post_id": "post-2"},
            "empty-locator": {"asset_locator": ""},
            "different-locator": {"asset_locator": "api:audio"},
            "different-path": {
                "url": (
                    "https://vod.afdiancdn.com/video/replacement.mp4"
                    "?sign=old&t=100&us=old-user"
                )
            },
            "functional-query": {
                "url": (
                    "https://vod.afdiancdn.com/video/asset.mp4"
                    "?quality=720p&sign=old&t=100&us=old-user"
                )
            },
            "missing-url": {"url": ""},
            "legacy-version": {"identity_version": 1},
        }

        for label, overrides in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                old_file = root / "old.mp4"
                old_file.write_bytes(b"old")
                old_candidate = ifdian_vod_candidate("old", "100", "old-user")
                old_key = pre_fix_v2_key("post-1", old_candidate)
                entry = {
                    "path": str(old_file),
                    "bytes": old_file.stat().st_size,
                    "post_id": "post-1",
                    "url": old_candidate.url,
                    "identity_version": 2,
                    "asset_key": old_key,
                    "asset_locator": old_candidate.asset_locator,
                }
                entry.update(overrides)
                state = DownloadState(root / "state.json")
                state.data["downloads"][old_key] = entry

                compatible = state.find_compatible_v2_entry(
                    "post-1",
                    refreshed,
                    download_key("post-1", refreshed),
                    claimed_paths=set(),
                    preferred_paths=[],
                )

                self.assertIsNone(compatible)

        with self.subTest(label="both-empty-locator"), tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_file = root / "old.mp4"
            old_file.write_bytes(b"old")
            old_candidate = ifdian_vod_candidate("old", "100", "old-user")
            current_without_locator = Candidate(
                url=refreshed.url,
                source=refreshed.source,
                filename_hint=refreshed.filename_hint,
                asset_locator="",
            )
            old_key = pre_fix_v2_key("post-1", old_candidate)
            state = DownloadState(root / "state.json")
            state.data["downloads"][old_key] = {
                "path": str(old_file),
                "bytes": old_file.stat().st_size,
                "post_id": "post-1",
                "url": old_candidate.url,
                "identity_version": 2,
                "asset_key": old_key,
                "asset_locator": "",
            }

            compatible = state.find_compatible_v2_entry(
                "post-1",
                current_without_locator,
                download_key("post-1", current_without_locator),
                claimed_paths=set(),
                preferred_paths=[],
            )

            self.assertIsNone(compatible)

    def test_missing_sidecar_filename_fallback_rejects_a_replaced_v2_asset(self) -> None:
        old_candidate = ifdian_vod_candidate("old", "100", "old-user")
        replacements = {
            "different-path": Candidate(
                url=(
                    "https://vod.afdiancdn.com/video/replacement.mp4"
                    "?sign=new&t=200&us=new-user"
                ),
                source=old_candidate.source,
                filename_hint=old_candidate.filename_hint,
                asset_locator=old_candidate.asset_locator,
            ),
            "functional-query": Candidate(
                url=(
                    "https://vod.afdiancdn.com/video/asset.mp4"
                    "?quality=original&sign=new&t=200&us=new-user"
                ),
                source=old_candidate.source,
                filename_hint=old_candidate.filename_hint,
                asset_locator=old_candidate.asset_locator,
            ),
        }

        for label, replacement in replacements.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                output_dir = root / "post"
                output_dir.mkdir()
                old_file = output_dir / "video.mp4"
                old_file.write_bytes(b"old")
                old_key = pre_fix_v2_key("post-1", old_candidate)
                state = DownloadState(root / "state.json")
                state.data["downloads"][old_key] = {
                    "path": str(old_file),
                    "bytes": old_file.stat().st_size,
                    "post_id": "post-1",
                    "url": old_candidate.url,
                    "identity_version": 2,
                    "asset_key": old_key,
                    "asset_locator": old_candidate.asset_locator,
                }

                def fake_download_candidate(**kwargs):
                    target = kwargs["output_dir"] / "video-1.mp4"
                    target.write_bytes(b"new")
                    return {
                        "status": "downloaded",
                        "url": kwargs["candidate"].url,
                        "path": str(target),
                        "bytes": target.stat().st_size,
                    }

                with patch(
                    "afdian_config_common.download_candidate",
                    side_effect=fake_download_candidate,
                ) as mocked_download:
                    records = download_candidates_for_post(
                        session=None,
                        candidates=[replacement],
                        output_dir=output_dir,
                        state=state,
                        config={"skip_existing": True},
                        post_meta=post_meta(),
                    )

                mocked_download.assert_called_once()
                self.assertEqual("downloaded", records[0]["status"])
                self.assertEqual(b"old", old_file.read_bytes())
                self.assertNotEqual(old_file.resolve(), Path(records[0]["path"]).resolve())

    def test_old_dynamic_sidecar_entry_is_reused_when_state_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "post"
            sidecar = claim_post_directory(output_dir, post_meta(), dry_run=False)
            old_candidate = ifdian_vod_candidate("old", "100", "old-user")
            old_key = pre_fix_v2_key("post-1", old_candidate)
            old_file = output_dir / "video.mp4"
            old_file.write_bytes(b"video")
            sidecar["assets"][old_key] = {
                "identity_url": old_candidate.url,
                "asset_locator": old_candidate.asset_locator,
                "source": old_candidate.source,
                "filename_hint": old_candidate.filename_hint,
                "file": old_file.name,
                "bytes": old_file.stat().st_size,
            }
            save_post_sidecar(output_dir, sidecar)
            state_path = root / "state.json"
            state = DownloadState(state_path)
            refreshed = ifdian_vod_candidate("new", "200", "new-user")

            with patch("afdian_config_common.download_candidate") as mocked_download:
                records = download_candidates_for_post(
                    session=None,
                    candidates=[refreshed],
                    output_dir=output_dir,
                    state=state,
                    config={"skip_existing": True},
                    post_meta=post_meta(),
                )

            mocked_download.assert_not_called()
            self.assertEqual("already-downloaded", records[0]["status"])
            self.assertEqual(old_file.resolve(), Path(records[0]["path"]).resolve())
            migrated = DownloadState(state_path).get_existing_entry(download_key("post-1", refreshed))
            self.assertIsNotNone(migrated)
            self.assertEqual(old_file.resolve(), Path(str(migrated["path"])).resolve())


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

    def test_exact_query_blind_v1_entry_is_reused_to_avoid_a_legacy_redownload(self) -> None:
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

            with patch("afdian_config_common.download_candidate") as mocked_download:
                records = download_candidates_for_post(
                    session=None,
                    candidates=[replacement],
                    output_dir=root / "new-layout",
                    state=state,
                    config={"skip_existing": True},
                    post_meta=post_meta(),
                )

            mocked_download.assert_not_called()
            self.assertEqual("already-downloaded", records[0]["status"])
            self.assertEqual(old_file.resolve(), Path(records[0]["path"]).resolve())
            self.assertIn(download_key("post-1", replacement), state.data["downloads"])

    def test_query_bearing_candidate_does_not_use_url_only_fuzzy_legacy_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = DownloadState(root / "state.json")
            old_file = root / "old.bin"
            old_file.write_bytes(b"old")
            state.data["downloads"]["unrelated-legacy-key"] = {
                "path": str(old_file),
                "bytes": 3,
                "post_id": "post-1",
                "post_title": "archive",
                "url": "https://cdn.example/download",
            }
            candidate = Candidate(
                url="https://cdn.example/download?id=new",
                source="api:attachment[1].url",
                filename_hint="archive",
                asset_locator="api:attachment[1]",
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
                    candidates=[candidate],
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

    def test_live_v2_owner_prevents_another_post_from_claiming_the_same_legacy_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state = DownloadState(root / "state.json")
            shared_file = root / "shared.bin"
            shared_file.write_bytes(b"old")
            candidate = Candidate(
                url="https://cdn.example/shared.bin",
                source="api:attachment[1].url",
                filename_hint="shared",
                asset_locator="api:attachment[1]",
            )
            legacy_key = download_key_v1("post-1", candidate)
            state.data["downloads"][legacy_key] = {
                "path": str(shared_file),
                "bytes": 3,
                "post_id": "post-1",
                "post_title": "Post",
                "url": candidate.url,
            }
            state.data["downloads"]["v2:existing-owner"] = {
                "path": str(shared_file),
                "bytes": 3,
                "post_id": "different-post",
                "post_title": "Different",
                "identity_version": 2,
                "asset_key": "v2:existing-owner",
                "asset_locator": "api:attachment[1]",
            }

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
                    candidates=[candidate],
                    output_dir=root / "post-1-output",
                    state=state,
                    config={"skip_existing": True},
                    post_meta=post_meta(),
                )

            mocked_download.assert_called_once()
            self.assertEqual("downloaded", records[0]["status"])
            self.assertNotEqual(shared_file.resolve(), Path(records[0]["path"]).resolve())

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
