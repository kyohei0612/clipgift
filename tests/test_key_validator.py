"""licensing/key_validator の単体テスト"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from licensing.key_validator import (
    validate_key_format,
    normalize_key,
    is_well_formed,
    mask_key,
)


# === normalize_key ===


class TestNormalizeKey:
    def test_uppercase(self):
        assert normalize_key("cgft-std-abcd-1234-wxyz") == "CGFT-STD-ABCD-1234-WXYZ"

    def test_strip_spaces(self):
        assert normalize_key("  CGFT-STD-ABCD-1234-WXYZ  ") == "CGFT-STD-ABCD-1234-WXYZ"

    def test_remove_internal_space(self):
        assert normalize_key("CGFT STD ABCD 1234 WXYZ") == "CGFTSTDABCD1234WXYZ"

    def test_zenkaku_hyphen(self):
        # 実装が変換する全角ハイフン: ー (U+30FC) / ― (U+2015) / － (U+FF0D)
        assert "-" in normalize_key("CGFTーSTDーABCD")
        assert "-" in normalize_key("CGFT―STD―ABCD")
        assert "-" in normalize_key("CGFT－STD－ABCD")

    def test_empty(self):
        assert normalize_key("") == ""

    def test_non_string(self):
        assert normalize_key(None) == ""


# === validate_key_format ===


class TestValidateKeyFormat:
    def test_valid_std_key(self):
        # STD コードが Phase 1 のメイン形式
        assert validate_key_format("CGFT-STD-ABCD-EFGH-JKLM") == "single"

    def test_legacy_lite_key(self):
        # 後方互換: 旧 LITE キーも single として受理
        assert validate_key_format("CGFT-LITE-ABCD-EFGH-JKLM") == "single"

    def test_legacy_ext_key(self):
        # 後方互換: 旧 EXT キーも single として受理
        assert validate_key_format("CGFT-EXT-ABCD-EFGH-JKLM") == "single"

    def test_wrong_prefix(self):
        assert validate_key_format("XXX-STD-ABCD-EFGH-JKLM") is None

    def test_invalid_plan_code(self):
        assert validate_key_format("CGFT-XXX-ABCD-EFGH-JKLM") is None

    def test_short_group(self):
        assert validate_key_format("CGFT-STD-ABC-EFGH-JKLM") is None  # g1 が 3 文字

    def test_invalid_chars(self):
        # ALPHABET に含まれない文字（O や 0 は除外されてる）
        assert validate_key_format("CGFT-STD-ABCO-EFGH-JKLM") is None

    def test_empty(self):
        assert validate_key_format("") is None

    def test_too_few_parts(self):
        assert validate_key_format("CGFT-STD-ABCD") is None


# === is_well_formed ===


class TestIsWellFormed:
    def test_valid(self):
        assert is_well_formed("CGFT-STD-ABCD-EFGH-JKLM") is True

    def test_invalid(self):
        assert is_well_formed("invalid key") is False


# === mask_key ===


class TestMaskKey:
    def test_normal_key(self):
        # ログ出力時のマスク（中央 2 グループを *** に）
        result = mask_key("CGFT-STD-ABCD-EFGH-JKLM")
        assert result == "CGFT-STD-***-***-JKLM"

    def test_empty(self):
        assert mask_key("") == "(empty)"

    def test_invalid_format(self):
        assert "INVALID" in mask_key("not-a-key")

    def test_signature_preserved(self):
        # 末尾シグネチャは残る（同一キー判別用）
        result = mask_key("CGFT-EXT-AAAA-BBBB-XXXX")
        assert result.endswith("-XXXX")
