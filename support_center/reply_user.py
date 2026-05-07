"""
ユーザー返信メール送信（Workers /support/reply 経由）

push 後に kyohei さんが scripts/reply_user.py から呼び出すか、
本モジュールを直接 import して使う。

返信は **kyohei さん手動トリガー専用**（自動返信なし）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from support_center.config import SupportConfig

logger = logging.getLogger(__name__)


# 返信テンプレ（日本語、丁寧 / 共感 / 押し売りなし）
DEFAULT_REPLY_TEMPLATE = """{user_email_or_anon} 様

このたびは ClipGift のエラー報告をいただき、ありがとうございました。
ご報告いただいた問題について対応しましたので、ClipGift の再起動をお願いいたします。

【再起動手順】
1. ClipGift を一度終了してください
2. 再起動すると自動更新が走ります（数十秒）
3. 「アップデート完了」のメッセージが出れば反映完了です

ご不便をおかけして申し訳ありませんでした。
今後とも ClipGift をよろしくお願いいたします。

---
ClipGift サポート
"""


@dataclass
class ReplyPlan:
    """返信送信プラン（送信前に内容を確認できるよう dataclass 化）。"""

    to_email: str
    subject: str
    body: str
    error_hash: str

    def preview(self) -> str:
        return (
            f"To:      {self.to_email}\n"
            f"Subject: {self.subject}\n"
            f"---\n{self.body}\n"
        )


def build_reply(
    *,
    to_email: str,
    error_hash: str,
    subject: str = "",
    repair_brief: str = "",  # 後方互換のため残すが使わない（Phase 1 は固定テンプレ）
) -> ReplyPlan:
    """返信プランを組み立てる（送信せず、確認用の dataclass を返す）。

    Phase 1 = 固定テンプレ運用。repair_brief は後方互換のため残置するが本文には反映しない。
    """
    if not to_email:
        raise ValueError("to_email が空です（返信先なし）")

    final_subject = subject or "【ClipGift】ご報告いただいた問題の修正版を公開しました"

    body = DEFAULT_REPLY_TEMPLATE.format(
        user_email_or_anon=_short_email_label(to_email),
    )
    return ReplyPlan(
        to_email=to_email,
        subject=final_subject,
        body=body,
        error_hash=error_hash,
    )


def send_reply(plan: ReplyPlan) -> bool:
    """返信メールを Workers /support/reply 経由で送信。成功なら True。"""
    endpoint = SupportConfig.WORKERS_ENDPOINT.rstrip("/") + "/support/reply"
    if not SupportConfig.ADMIN_TOKEN:
        logger.error("SUPPORT_ADMIN_TOKEN が未設定のため返信送信できません")
        return False

    payload: dict[str, Any] = {
        "to": plan.to_email,
        "subject": plan.subject,
        "body": plan.body,
        "reply_to_hash": plan.error_hash,
    }

    try:
        resp = requests.post(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {SupportConfig.ADMIN_TOKEN}",
            },
            timeout=(5, 15),
        )
    except requests.RequestException as e:
        logger.error("返信送信失敗（接続）: %s", e)
        return False

    if resp.status_code >= 400:
        logger.error(
            "返信送信 HTTP %d: %s",
            resp.status_code,
            resp.text[:300],
        )
        return False

    logger.info(
        "ユーザー返信送信成功 hash=%s to=%s",
        plan.error_hash,
        _short_email_label(plan.to_email),
    )

    # 送信ログを incoming/{hash}.replied.txt に残す
    try:
        marker = SupportConfig.INCOMING_DIR / f"{plan.error_hash}.replied.txt"
        marker.write_text(plan.preview(), encoding="utf-8")
    except OSError as e:
        logger.warning("返信ログ保存失敗: %s", e)

    return True


def _short_email_label(email: str) -> str:
    """メアドの表示用短縮（ログ用、本文には使わない）。"""
    if not email or "@" not in email:
        return email or "（不明）"
    return email
