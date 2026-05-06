"""URL 解析の単体テスト（実 DL は行わない）

kyohei さんから提供されたテスト URL も検証に使う。
実機 E2E は別途。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from twitch_chat import is_twitch_url, extract_twitch_video_id


YOUTUBE_TEST_URL = "https://www.youtube.com/watch?v=vSDplq4s4YU"
TWITCH_TEST_URL = "https://www.twitch.tv/videos/2752635021?filter=archives&sort=time"


# === is_twitch_url ===


class TestIsTwitchUrl:
    def test_basic_twitch(self):
        assert is_twitch_url("https://www.twitch.tv/videos/123") is True

    def test_provided_test_url(self):
        # kyohei さん提供のテスト URL
        assert is_twitch_url(TWITCH_TEST_URL) is True

    def test_no_www(self):
        assert is_twitch_url("https://twitch.tv/videos/123") is True

    def test_mobile(self):
        assert is_twitch_url("https://m.twitch.tv/videos/123") is True

    def test_youtube_url(self):
        assert is_twitch_url(YOUTUBE_TEST_URL) is False

    def test_youtube_short(self):
        assert is_twitch_url("https://youtu.be/vSDplq4s4YU") is False

    def test_empty(self):
        assert is_twitch_url("") is False


# === extract_twitch_video_id ===


class TestExtractTwitchVideoId:
    def test_provided_test_url(self):
        # kyohei さん提供のテスト URL から video_id を抽出できる
        assert extract_twitch_video_id(TWITCH_TEST_URL) == "2752635021"

    def test_basic(self):
        assert (
            extract_twitch_video_id("https://www.twitch.tv/videos/123456") == "123456"
        )

    def test_no_www(self):
        assert extract_twitch_video_id("https://twitch.tv/videos/789") == "789"

    def test_legacy_v_format(self):
        assert extract_twitch_video_id("https://www.twitch.tv/user/v/12345") == "12345"

    def test_youtube_returns_none(self):
        assert extract_twitch_video_id(YOUTUBE_TEST_URL) is None

    def test_invalid(self):
        assert extract_twitch_video_id("https://example.com") is None
