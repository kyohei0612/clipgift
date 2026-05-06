"""
ライセンスキーの形式検証 + 入力正規化

検証ロジック:
- アプリ側では「キー形式の妥当性」のみチェック（UI 入力時の早期リジェクト用）
- HMAC 署名検証はサーバー側 (license_server) でのみ実施（HMAC_SECRET はサーバー側のみが保有）
- credential の改ざん検知は credential_store.py の AES-256-GCM タグで行う
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# サーバー側 keys.ts と一致させる
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PRODUCT_PREFIX = "CGFT"
VALID_PLAN_CODES = {"LITE", "STD", "EXT"}
PLAN_CODE_TO_NAME = {"LITE": "lite", "STD": "std", "EXT": "ext"}


def validate_key_format(key: str) -> Optional[str]:
    """
    キー形式の妥当性チェック

    HMAC 署名検証はサーバー側で実施するため、ここではフォーマットのみ確認。
    UI 入力時の早期リジェクト用。

    Returns:
        プラン名 (lite/std/ext) または None（不正形式）
    """
    if not isinstance(key, str):
        return None
    parts = key.split("-")
    if len(parts) != 5:
        return None
    pfx, plan_code, g1, g2, sig = parts
    if pfx != PRODUCT_PREFIX:
        return None
    if plan_code not in VALID_PLAN_CODES:
        return None
    if len(g1) != 4 or len(g2) != 4 or len(sig) != 4:
        return None
    # 全文字が ALPHABET に含まれているか
    for ch in f"{g1}{g2}{sig}":
        if ch not in ALPHABET:
            return None
    return PLAN_CODE_TO_NAME[plan_code]


def normalize_key(raw: str) -> str:
    """
    ユーザーが入力したキー文字列の正規化

    - 前後空白除去
    - 大文字化
    - 内部空白除去
    - 全角ハイフン → 半角ハイフン
    """
    if not isinstance(raw, str):
        return ""
    cleaned = raw.strip().upper().replace(" ", "")
    cleaned = cleaned.replace("ー", "-").replace("―", "-").replace("－", "-")
    return cleaned


def is_well_formed(key: str) -> bool:
    """形式が正しいかどうかの bool 判定（UI のフォーム検証用）"""
    return validate_key_format(key) is not None
