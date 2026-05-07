"""
ClipGift サポートセンター連携モジュール

エンドユーザーのアプリ内エラー報告を Cloudflare Workers 経由で受信し、
Claude Code が自動解析・修正案を作成、kyohei さんが手動 push する半自動サポートフロー。

設計書: .company/engineering/docs/support-center.md
"""

from support_center.config import SupportConfig

__all__ = ["SupportConfig"]
