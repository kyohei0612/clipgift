"""support_center.pii_masker の単体テスト"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support_center.pii_masker import (
    mask_log,
    mask_email,
    license_tail,
)


class TestMaskLog:
    def test_windows_path_user_replaced(self):
        text = r"C:\Users\kyohei\AppData\Local\ClipGift\error.log"
        masked = mask_log(text)
        assert "<USER>" in masked
        assert "kyohei" not in masked

    def test_unix_home_path_user_replaced(self):
        text = "/home/alice/projects/clipgift/main.py"
        masked = mask_log(text)
        assert "<USER>" in masked
        assert "alice" not in masked

    def test_macos_users_path_replaced(self):
        text = "/Users/bob/Documents/clipgift.log"
        masked = mask_log(text)
        assert "<USER>" in masked
        assert "bob" not in masked

    def test_license_key_masked(self):
        text = "Activated key: CGFT-STD-ABCD-1234-WXYZ"
        masked = mask_log(text)
        assert "ABCD" not in masked
        assert "1234" not in masked
        assert "WXYZ" in masked  # 末尾 4 桁は残る
        assert "****" in masked

    def test_license_key_lowercase_input(self):
        text = "Activated key: cgft-ext-abcd-1234-wxyz"
        masked = mask_log(text)
        assert "abcd" not in masked
        assert "wxyz" in masked.lower() or "WXYZ" in masked

    def test_ipv4_masked(self):
        text = "Connection from 192.168.1.42 / 10.0.0.1"
        masked = mask_log(text)
        assert "192.168.1.42" not in masked
        assert "10.0.0.1" not in masked
        assert "<IP>" in masked

    def test_video_filename_masked(self):
        text = r"Loading C:\Users\test\Downloads\my_special_stream.mp4 ..."
        masked = mask_log(text)
        assert "my_special_stream.mp4" not in masked
        assert "<VIDEO_FILE>" in masked

    def test_empty_input(self):
        assert mask_log("") == ""
        assert mask_log(None) is None  # noqa

    def test_preserves_newlines(self):
        text = "line1\nline2\nline3"
        masked = mask_log(text)
        assert masked.count("\n") == 2


class TestMaskEmail:
    def test_normal(self):
        assert mask_email("user@example.com") == "u***@example.com"

    def test_short_local_part(self):
        assert mask_email("a@example.com") == "*@example.com"

    def test_no_at(self):
        assert mask_email("invalid") == "<EMAIL>"

    def test_empty(self):
        assert mask_email("") == "<EMAIL>"


class TestLicenseTail:
    def test_normal_key(self):
        assert license_tail("CGFT-STD-ABCD-1234-WXYZ") == "WXYZ"

    def test_lowercase_normalized(self):
        assert license_tail("cgft-std-abcd-1234-wxyz") == "WXYZ"

    def test_too_few_parts(self):
        assert license_tail("CGFT-STD-ABCD") == ""

    def test_empty(self):
        assert license_tail("") == ""
