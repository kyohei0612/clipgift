"""claude_runner._parse_analyze_output の単体テスト

Claude CLI の出力からサマリ・返信案・影響ファイルを抽出するパーサ。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support_center.claude_runner import _parse_analyze_output


SAMPLE_VALID = """
解析結果を以下にまとめます。

## 修正サマリ
- 影響ファイル: chat_filter.py, downloader.py
- 既知バグだったか: yes（ISSUES.md B-3 に類似）
- 変更内容:
  - コメント長判定の境界値修正
  - 空文字列の扱いを 0 として処理
- テスト結果: pytest 全 PASS
- リスク: なし

## ユーザー返信案
user@example.com 様

このたびは ClipGift のエラー報告をいただき、ありがとうございます。
ご報告いただいた問題について修正版を公開しました。

【適用方法】
1. ClipGift を一度終了
2. 再起動で自動更新

ご不便をおかけして申し訳ありませんでした。

---
ClipGift サポート
"""


def test_parses_repair_summary():
    repair, reply, files, cause = _parse_analyze_output(SAMPLE_VALID)
    assert "コメント長判定" in repair
    assert "pytest 全 PASS" in repair


def test_parses_user_reply():
    _, reply, _, _ = _parse_analyze_output(SAMPLE_VALID)
    assert "user@example.com 様" in reply
    assert "ClipGift サポート" in reply


def test_parses_affected_files():
    _, _, files, _ = _parse_analyze_output(SAMPLE_VALID)
    assert "chat_filter.py" in files
    assert "downloader.py" in files


def test_handles_missing_sections():
    repair, reply, files, cause = _parse_analyze_output("出力なし")
    assert repair == ""
    assert reply == ""
    assert files == []
    assert cause == ""


def test_handles_only_summary():
    text = """
## 修正サマリ
- 影響ファイル: app.py
- 変更内容: typo 修正
"""
    repair, reply, files, cause = _parse_analyze_output(text)
    assert "typo 修正" in repair
    assert reply == ""
    assert "app.py" in files


# ── dialog パーサ ──

from support_center.claude_runner import _parse_dialog_output


def test_dialog_send_to_user():
    text = """
## kyohei 返信の解釈
OK 承認

## 実施した対応
変更なし

## ユーザー返信案（更新版）
ユーザー様

修正完了しました。

## 次のアクション
SEND_TO_USER
"""
    action, reply, summary = _parse_dialog_output(text)
    assert action == "SEND_TO_USER"
    assert "修正完了しました" in reply


def test_dialog_revise():
    text = """
## kyohei 返信の解釈
返信文言の修正依頼

## ユーザー返信案
ユーザー様
更新版返信文

## 次のアクション
REVISE_AND_REPLY
"""
    action, reply, summary = _parse_dialog_output(text)
    assert action == "REVISE_AND_REPLY"


def test_dialog_abort():
    text = """
## 次のアクション
ABORT
"""
    action, _, _ = _parse_dialog_output(text)
    assert action == "ABORT"


def test_dialog_default_to_revise():
    text = "アクション指定なし"
    action, _, _ = _parse_dialog_output(text)
    # マーカーなし → 保守的に REVISE_AND_REPLY
    assert action == "REVISE_AND_REPLY"


# ── 承認語ショートカット ──

from support_center.claude_runner import _is_clear_approval, _is_clear_abort


class TestApprovalShortcut:
    def test_yoroshiku(self):
        assert _is_clear_approval("よろしく")
        assert _is_clear_approval("よろしく お願いします")
        assert _is_clear_approval("よろ")

    def test_ok_variants(self):
        assert _is_clear_approval("OK")
        assert _is_clear_approval("ok")
        assert _is_clear_approval("おｋ")
        assert _is_clear_approval("おけ")

    def test_send_phrases(self):
        assert _is_clear_approval("送って")
        assert _is_clear_approval("進めて")

    def test_long_text_not_approval(self):
        # 長文（200 字超）は追加指示の可能性 → 即承認しない
        long_text = "OK " + "あ" * 250
        assert not _is_clear_approval(long_text)

    def test_abort_keywords(self):
        assert _is_clear_abort("やめて")
        assert _is_clear_abort("キャンセル")
        assert _is_clear_abort("中止")

    def test_no_variants(self):
        assert _is_clear_abort("NO")
        assert _is_clear_abort("No")
        assert _is_clear_abort("no")
        assert _is_clear_abort("いいえ")
        assert _is_clear_abort("ダメ")
        assert _is_clear_abort("だめ")
        assert _is_clear_abort("ノー")
        # 短文 + 拒否語のみ → ABORT
        assert _is_clear_abort("NO 送らないで")

    def test_mixed_approval_and_abort(self):
        # 両方混じってたら approve も abort も False
        assert not _is_clear_approval("OK でもやっぱりやめて")
        assert not _is_clear_abort("やめて じゃなくて OK")

    def test_quoted_lines_excluded(self):
        # Gmail の引用（"> ..."）は除外
        text = """よろしく

> 元のメール本文
> 修正サマリ
> やめて  <- 引用部、判定対象外
"""
        assert _is_clear_approval(text)

    def test_empty_input(self):
        assert not _is_clear_approval("")
        assert not _is_clear_abort("")
