"""
ユーザー返信送信 CLI（kyohei さん手動実行用）

使い方:
    # 1. 修正案レビュー後、build_and_push.bat を実行済みの状態で呼ぶ
    python scripts/reply_user.py --hash 1234abcd5678 \\
        --to user@example.com \\
        --brief "コメント長さ判定の不具合を修正しました（chat_filter.py）"

    # 2. dry-run で内容確認（送信しない）
    python scripts/reply_user.py --hash 1234abcd5678 \\
        --to user@example.com \\
        --brief "..." --dry-run

設計:
- 自動返信は禁止、必ず kyohei さんが手動でこのスクリプトを叩く
- --dry-run で送信前に内容を目視確認できる
- 送信ログは support_center/incoming/{hash}.replied.txt に保存
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from support_center.reply_user import build_reply, send_reply  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="ユーザー返信メール送信")
    parser.add_argument(
        "--hash",
        required=True,
        help="エラーハッシュ（incoming/{date}-{hash}.eml の hash 部分）",
    )
    parser.add_argument(
        "--to",
        required=True,
        help="返信先メールアドレス",
    )
    parser.add_argument(
        "--brief",
        default="",
        help="今回の修正内容を 1-2 行で要約（ユーザー向け）",
    )
    parser.add_argument(
        "--subject",
        default="",
        help="件名（省略時は標準テンプレ）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="送信せず、本文を確認するだけ",
    )
    args = parser.parse_args()

    plan = build_reply(
        to_email=args.to,
        error_hash=args.hash,
        repair_brief=args.brief,
        subject=args.subject,
    )

    print("=" * 60)
    print("送信内容プレビュー:")
    print("=" * 60)
    print(plan.preview())
    print("=" * 60)

    if args.dry_run:
        print("[dry-run] 送信はスキップしました。--dry-run を外すと実送信します。")
        return 0

    confirm = input("この内容で送信しますか？ (yes/no): ").strip().lower()
    if confirm not in ("yes", "y"):
        print("中止しました")
        return 10

    if send_reply(plan):
        print("✅ ユーザー返信を送信しました")
        return 0

    print("❌ 送信失敗（ログを確認してください）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
