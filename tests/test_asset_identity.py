from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from afdian_config_common import (
    download_key,
    expected_filename_for_candidate,
    post_output_dir,
)
from afdian_downloader import Candidate, dedupe_candidates, filename_from_url


class ReadableOutputPathTests(unittest.TestCase):
    def test_preferred_path_keeps_the_pre_ffed164_readable_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = post_output_dir(
                root,
                "辣不辣Hyo",
                1_752_787_200,
                "KU100 1v1轻语助眠天鹅",
                "85f0bbb0821111f1a73852540025c377",
                creator_id="ff8b0492c95811ecb44252540025c377",
            )

            self.assertEqual(
                root / "辣不辣Hyo" / "2025-07-18-KU100-1v1轻语助眠天鹅",
                output_dir,
            )


class CandidateIdentityTests(unittest.TestCase):
    def test_download_key_is_independent_of_mutable_title_hint(self) -> None:
        before_rename = Candidate(
            url="https://cdn.example/files/archive.zip",
            source="api:attachment",
            filename_hint="Old Post Title",
        )
        after_rename = Candidate(
            url=before_rename.url,
            source=before_rename.source,
            filename_hint="New Post Title",
        )

        self.assertEqual(
            download_key("post-1", before_rename),
            download_key("post-1", after_rename),
        )

    def test_functional_query_parameters_keep_distinct_assets(self) -> None:
        first = Candidate(
            url="https://cdn.example/download?file=asset-1&quality=original",
            source="api:attachment",
            filename_hint="Archive",
        )
        second = Candidate(
            url="https://cdn.example/download?file=asset-2&quality=original",
            source="api:attachment",
            filename_hint="Archive",
        )

        with self.subTest("download key"):
            self.assertNotEqual(download_key("post-1", first), download_key("post-1", second))
        with self.subTest("candidate dedupe"):
            self.assertEqual([first.url, second.url], [item.url for item in dedupe_candidates([first, second])])

    def test_unknown_sign_and_token_parameters_remain_distinct(self) -> None:
        first = Candidate(
            url="https://cdn.example/files/archive.zip?sign=old&token=one",
            source="api:attachment",
            filename_hint="Archive",
        )
        refreshed = Candidate(
            url="https://cdn.example/files/archive.zip?sign=new&token=two",
            source="api:attachment",
            filename_hint="Archive",
        )

        with self.subTest("download key"):
            self.assertNotEqual(download_key("post-1", first), download_key("post-1", refreshed))
        with self.subTest("candidate dedupe"):
            self.assertEqual(2, len(dedupe_candidates([first, refreshed])))

    def test_complete_amz_signature_family_is_ignored_but_functional_query_is_kept(self) -> None:
        first = Candidate(
            url=(
                "https://cdn.example/files/archive.zip?file=asset-42"
                "&X-Amz-Algorithm=AWS4-HMAC-SHA256"
                "&X-Amz-Credential=old"
                "&X-Amz-Date=20260718T000000Z"
                "&X-Amz-Expires=60"
                "&X-Amz-SignedHeaders=host"
                "&X-Amz-Signature=old-signature"
            ),
            source="api:attachment",
            filename_hint="Archive",
        )
        refreshed = Candidate(
            url=(
                "https://cdn.example/files/archive.zip?file=asset-42"
                "&X-Amz-Algorithm=AWS4-HMAC-SHA256"
                "&X-Amz-Credential=new"
                "&X-Amz-Date=20260718T010000Z"
                "&X-Amz-Expires=120"
                "&X-Amz-SignedHeaders=host"
                "&X-Amz-Signature=new-signature"
            ),
            source="api:attachment",
            filename_hint="Archive",
        )
        different_file = Candidate(
            url=refreshed.url.replace("file=asset-42", "file=asset-43"),
            source="api:attachment",
            filename_hint="Archive",
        )

        with self.subTest("signature rotation keeps the download key"):
            self.assertEqual(download_key("post-1", first), download_key("post-1", refreshed))
        with self.subTest("functional query changes the download key"):
            self.assertNotEqual(download_key("post-1", first), download_key("post-1", different_file))
        with self.subTest("candidate dedupe"):
            self.assertEqual(1, len(dedupe_candidates([first, refreshed])))

    def test_incomplete_signature_families_keep_their_query_identity(self) -> None:
        incomplete_pairs = {
            "aws": (
                "X-Amz-Signature=first",
                "X-Amz-Signature=second",
            ),
            "google": (
                "X-Goog-Signature=first",
                "X-Goog-Signature=second",
            ),
            "tencent-cos": (
                "q-sign-time=1%3B2&q-signature=first",
                "q-sign-time=3%3B4&q-signature=second",
            ),
            "alibaba-oss": (
                "OSSAccessKeyId=key&Signature=first",
                "OSSAccessKeyId=key&Signature=second",
            ),
            "cloudfront": (
                "Policy=policy&Signature=first",
                "Policy=policy&Signature=second",
            ),
        }

        for family, (first_query, second_query) in incomplete_pairs.items():
            with self.subTest(family=family):
                first = Candidate(
                    url=f"https://cdn.example/archive.zip?{first_query}",
                    source="api:attachment[1].url",
                    asset_locator="api:attachment[1]",
                )
                second = Candidate(
                    url=f"https://cdn.example/archive.zip?{second_query}",
                    source="api:attachment[1].url",
                    asset_locator="api:attachment[1]",
                )
                self.assertNotEqual(download_key("post-1", first), download_key("post-1", second))

    def test_complete_oss_signature_rotation_keeps_functional_processing_query(self) -> None:
        first = Candidate(
            url=(
                "https://cdn.example/archive.zip?x-oss-process=style%2Fpreview"
                "&OSSAccessKeyId=old&Expires=100&Signature=old-signature"
            ),
            source="api:attachment[1].url",
            asset_locator="api:attachment[1]",
        )
        refreshed = Candidate(
            url=(
                "https://cdn.example/archive.zip?x-oss-process=style%2Fpreview"
                "&OSSAccessKeyId=new&Expires=200&Signature=new-signature"
            ),
            source=first.source,
            asset_locator=first.asset_locator,
        )
        different_processing = Candidate(
            url=refreshed.url.replace("style%2Fpreview", "style%2Foriginal"),
            source=first.source,
            asset_locator=first.asset_locator,
        )

        self.assertEqual(download_key("post-1", first), download_key("post-1", refreshed))
        self.assertNotEqual(
            download_key("post-1", first),
            download_key("post-1", different_processing),
        )

    def test_asset_locator_disambiguates_slots_without_hiding_url_replacements(self) -> None:
        url_candidate = Candidate(
            url="https://old-cdn.example/download?file=42",
            source="api:attachment[1].url",
            filename_hint="File",
            asset_locator="attachment[1]",
        )
        download_url_candidate = Candidate(
            url=url_candidate.url,
            source="api:attachment[1].download_url",
            filename_hint="Renamed File",
            asset_locator="attachment[1]",
        )
        replaced_url_candidate = Candidate(
            url="https://new-cdn.example/object/new-path?file=42",
            source="api:attachment[1].download_url",
            filename_hint="Renamed File",
            asset_locator="attachment[1]",
        )
        other_locator = Candidate(
            url=url_candidate.url,
            source="api:attachment[2].url",
            filename_hint=url_candidate.filename_hint,
            asset_locator="attachment[2]",
        )

        with self.subTest("same locator keeps the download key"):
            self.assertEqual(
                download_key("post-1", url_candidate),
                download_key("post-1", download_url_candidate),
            )
        with self.subTest("different locator changes the download key"):
            self.assertNotEqual(
                download_key("post-1", url_candidate),
                download_key("post-1", other_locator),
            )
        with self.subTest("same locator with a replacement URL changes the download key"):
            self.assertNotEqual(
                download_key("post-1", url_candidate),
                download_key("post-1", replaced_url_candidate),
            )

        result = dedupe_candidates([url_candidate, download_url_candidate, other_locator])

        with self.subTest("dedupe matches by locator"):
            self.assertEqual(
                ["attachment[1]", "attachment[2]"],
                [item.asset_locator for item in result],
            )


class FilenameContractTests(unittest.TestCase):
    def test_download_filename_does_not_duplicate_existing_suffix(self) -> None:
        filename = filename_from_url(
            "https://cdn.example/files/archive.zip",
            {"content-disposition": 'attachment; filename="archive.zip"'},
            preferred_stem="archive.zip",
        )

        self.assertEqual("archive.zip", filename)

    def test_fallback_filename_does_not_duplicate_existing_suffix(self) -> None:
        candidate = Candidate(
            url="https://cdn.example/files/archive.zip",
            source="api:attachment",
            filename_hint="archive.zip",
        )

        with self.subTest("first candidate"):
            self.assertEqual("archive.zip", expected_filename_for_candidate(candidate, 0))
        with self.subTest("same-name duplicate"):
            self.assertEqual("archive-1.zip", expected_filename_for_candidate(candidate, 1))


if __name__ == "__main__":
    unittest.main()
