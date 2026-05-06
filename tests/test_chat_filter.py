"""chat_filter の単体テスト"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat_filter import (
    strip_emojis,
    is_known_bot,
    is_system_message,
    has_japanese_char,
    should_skip_comment,
)


# === strip_emojis ===


class TestStripEmojis:
    def test_removes_basic_emoji(self):
        assert strip_emojis("草😊") == "草"

    def test_removes_pictographs(self):
        assert strip_emojis("hello ★ world") == "hello  world"

    def test_keeps_japanese(self):
        assert strip_emojis("ありがとう") == "ありがとう"

    def test_keeps_alphanumeric(self):
        assert strip_emojis("ww123") == "ww123"

    def test_empty_string(self):
        assert strip_emojis("") == ""

    def test_only_emoji(self):
        assert strip_emojis("😊🎉🔥") == ""


# === is_known_bot ===


class TestIsKnownBot:
    def test_nightbot(self):
        assert is_known_bot("Nightbot") is True

    def test_streamelements(self):
        assert is_known_bot("StreamElements") is True

    def test_normal_user(self):
        assert is_known_bot("kyohei") is False

    def test_empty(self):
        assert is_known_bot("") is False


# === is_system_message ===


class TestIsSystemMessage:
    def test_subscription_with_prime(self):
        # 実装の SUBSCRIBE 系パターンにマッチする文言
        assert is_system_message("user subscribed with Prime") is True

    def test_resubscribed(self):
        assert is_system_message("user resubscribed for 12 months") is True

    def test_gifted_sub(self):
        assert is_system_message("user gifted a Tier 1 sub") is True

    def test_normal_comment(self):
        assert is_system_message("草") is False

    def test_empty(self):
        assert is_system_message("") is False


# === has_japanese_char ===


class TestHasJapaneseChar:
    def test_hiragana(self):
        assert has_japanese_char("ありがとう") is True

    def test_katakana(self):
        assert has_japanese_char("カタカナ") is True

    def test_kanji(self):
        assert has_japanese_char("漢字") is True

    def test_english_only(self):
        assert has_japanese_char("hello world") is False

    def test_alphanumeric(self):
        assert has_japanese_char("ww123") is False

    def test_mixed(self):
        assert has_japanese_char("hello 草") is True


# === should_skip_comment ===


class TestShouldSkipComment:
    def test_keep_japanese(self):
        assert should_skip_comment("草") is False

    def test_keep_short_alphanumeric(self):
        assert should_skip_comment("ww") is False
        assert should_skip_comment("ok") is False
        assert should_skip_comment("GG") is False

    def test_keep_repeating_char(self):
        assert should_skip_comment("wwww") is False
        assert should_skip_comment("88888") is False

    def test_skip_long_english(self):
        assert should_skip_comment("Hello world") is True
        assert should_skip_comment("PogChamp") is True

    def test_skip_empty(self):
        assert should_skip_comment("") is True

    def test_skip_bot_user(self):
        assert should_skip_comment("normal text", user="Nightbot") is True

    def test_keep_japanese_even_if_long(self):
        assert should_skip_comment("これはとても面白い配信ですね") is False
