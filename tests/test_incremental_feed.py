from __future__ import annotations

import unittest
from unittest.mock import patch

import afdian_downloader
from afdian_downloader import iter_feed_posts_api, scan_feed_posts_api


def feed_post(post_id: str, publish_sn: str, publish_time: int) -> dict[str, object]:
    return {
        "post_id": post_id,
        "publish_sn": publish_sn,
        "publish_time": publish_time,
        "title": post_id,
    }


def feed_page(posts: list[dict[str, object]], has_more: bool) -> dict[str, object]:
    return {"ec": 200, "data": {"list": posts, "has_more": int(has_more)}}


class IncrementalFeedTests(unittest.TestCase):
    def test_known_boundary_stops_after_configured_post_lookback(self) -> None:
        pages = {
            "": feed_page(
                [
                    feed_post("new-2", "sn-new-2", 500),
                    feed_post("new-1", "sn-new-1", 400),
                    feed_post("known", "sn-known", 300),
                ],
                has_more=True,
            ),
            "sn-known": feed_page(
                [
                    feed_post("old-1", "sn-old-1", 200),
                    feed_post("old-2", "sn-old-2", 100),
                    feed_post("old-3", "sn-old-3", 50),
                ],
                has_more=True,
            ),
            "sn-old-3": feed_page(
                [feed_post("must-not-fetch", "sn-final", 1)],
                has_more=False,
            ),
        }
        requested_cursors: list[str] = []

        def fake_api_get_json(_session, _path, params):
            cursor = str(params["publish_sn"])
            requested_cursors.append(cursor)
            return pages[cursor]

        with patch.object(afdian_downloader, "api_get_json", side_effect=fake_api_get_json):
            result = scan_feed_posts_api(
                session=None,
                creator_user_id="creator-1",
                max_posts=0,
                since_ts=None,
                until_ts=None,
                stop_post_id="",
                per_page=3,
                known_post_ids={"known"},
                incremental_lookback=2,
            )

        self.assertEqual(["", "sn-known"], requested_cursors)
        self.assertEqual(
            ["new-2", "new-1", "known", "old-1", "old-2"],
            [post.post_id for post in result.posts],
        )
        self.assertTrue(result.incremental_boundary_reached)
        self.assertTrue(result.checkpoint_safe)
        self.assertEqual("incremental-lookback-complete", result.stop_reason)

    def test_repeated_nonempty_cursor_raises_instead_of_looping(self) -> None:
        requested_cursors: list[str] = []
        repeated_page = feed_page(
            [feed_post("post-1", "sn-stuck", 100)],
            has_more=True,
        )

        class SafetyStop(Exception):
            pass

        def fake_api_get_json(_session, _path, params):
            requested_cursors.append(str(params["publish_sn"]))
            if len(requested_cursors) > 3:
                raise SafetyStop("test guard: pagination failed to stop")
            return repeated_page

        with patch.object(afdian_downloader, "api_get_json", side_effect=fake_api_get_json):
            with self.assertRaisesRegex(RuntimeError, r"(?i)(pagination|cursor).*(stalled|progress|advance)"):
                iter_feed_posts_api(
                    session=None,
                    creator_user_id="creator-1",
                    max_posts=0,
                    since_ts=None,
                    until_ts=None,
                    stop_post_id="",
                    per_page=1,
                )

        self.assertLessEqual(len(requested_cursors), 2)

    def test_pinned_posts_do_not_establish_or_consume_incremental_boundary(self) -> None:
        page = feed_page(
            [
                {**feed_post("known-pinned", "sn-pinned", 900), "user_top": True},
                feed_post("new", "sn-new", 500),
                feed_post("known", "sn-known", 400),
                {**feed_post("old-pinned", "sn-old-pinned", 50), "user_top": True},
                feed_post("old-1", "sn-old-1", 300),
                feed_post("old-2", "sn-old-2", 200),
                feed_post("must-not-include", "sn-final", 100),
            ],
            has_more=False,
        )

        with patch.object(afdian_downloader, "api_get_json", return_value=page):
            result = scan_feed_posts_api(
                session=None,
                creator_user_id="creator-1",
                max_posts=0,
                since_ts=None,
                until_ts=None,
                stop_post_id="",
                per_page=10,
                known_post_ids={"known-pinned", "known"},
                incremental_lookback=2,
            )

        self.assertEqual(
            ["known-pinned", "new", "known", "old-pinned", "old-1", "old-2"],
            [post.post_id for post in result.posts],
        )
        self.assertNotIn("known-pinned", result.checkpoint_post_ids)
        self.assertNotIn("old-pinned", result.checkpoint_post_ids)
        self.assertEqual("incremental-lookback-complete", result.stop_reason)

    def test_unique_posts_with_unchanged_cursor_raise_cursor_error(self) -> None:
        pages = [
            feed_page([feed_post("post-1", "sn-stuck", 200)], has_more=True),
            feed_page([feed_post("post-2", "sn-stuck", 100)], has_more=True),
        ]

        with patch.object(afdian_downloader, "api_get_json", side_effect=pages):
            with self.assertRaisesRegex(RuntimeError, "cursor did not advance"):
                scan_feed_posts_api(
                    session=None,
                    creator_user_id="creator-1",
                    max_posts=0,
                    since_ts=None,
                    until_ts=None,
                    stop_post_id="",
                    per_page=1,
                )


if __name__ == "__main__":
    unittest.main()
