"""mail_watcher ヘルパー関数の単体テスト"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support_center.mail_watcher import _extract_email, _extract_form_user_email


class TestExtractEmail:
    def test_simple_email(self):
        assert _extract_email("user@example.com") == "user@example.com"

    def test_with_name(self):
        assert _extract_email("Name <user@example.com>") == "user@example.com"

    def test_empty(self):
        assert _extract_email("") == ""


class TestExtractFormUserEmail:
    def test_basic_extraction(self):
        body = "- ユーザー返信先: user@example.com\n"
        assert _extract_form_user_email(body) == "user@example.com"

    def test_zenkaku_colon(self):
        body = "- ユーザー返信先：user@example.com\n"
        assert _extract_form_user_email(body) == "user@example.com"

    def test_in_full_email_body(self):
        body = """ClipGift からエラー報告を受信しました。

【アプリ情報】
- バージョン: 1.0.63
- OS: Windows 11
- ライセンス末尾: なし
- ユーザー返信先: 0716a.y.a@gmail.com
- スクリーンショット: 1 枚 添付

【ユーザーコメント】
エラーが出ました
"""
        assert _extract_form_user_email(body) == "0716a.y.a@gmail.com"

    def test_no_match_returns_empty(self):
        body = "メアドの記載なし"
        assert _extract_form_user_email(body) == ""

    def test_empty_body(self):
        assert _extract_form_user_email("") == ""

    def test_dot_plus_in_local_part(self):
        body = "- ユーザー返信先: foo.bar+test@example.co.jp\n"
        assert _extract_form_user_email(body) == "foo.bar+test@example.co.jp"
