"""SNS 投稿の文字数判定。

各媒体の安全上限を定数化。実機検証ベース（2026-05-09）:
- X: 公称 280、カウンタ「0」でも投稿不能ケースあり → SAFE = 278
- Threads: 公称 500 → SAFE = 498
- Bluesky: 公称 300 graphemes → SAFE = 298

設計書: marketing/sns/2026-05-09-ai-rinse-research.md §0
"""

from __future__ import annotations

SAFE_LIMITS = {
    "x": 278,
    "threads": 498,
    "bluesky": 298,
}


def count_x(text: str) -> int:
    """X の重み付き文字数（全角 2 / 半角 1）。

    Twitter Text Library の仕様に基づく簡易実装:
    - ASCII (U+0000-U+007F) = 1
    - 半角カタカナ (U+FF61-U+FF9F) = 1
    - その他全角文字・絵文字 = 2
    - サロゲートペア絵文字（🎬 等の U+10000 以上）も 2 として正しくカウント

    実際の X カウンタは「0」ジャストでも投稿不能ケースあるため、
    SAFE_LIMITS["x"] = 278 で運用する。

    Python 3 の `for ch in text` は code point 単位で iterate するので
    サロゲートペアは正しく 1 文字として扱える（重み 2）。
    """
    count = 0
    for ch in text:
        cp = ord(ch)
        # ASCII or 半角カナ = 1
        if cp <= 0x7F or 0xFF61 <= cp <= 0xFF9F:
            count += 1
        # その他（全角・絵文字・サロゲートペア絵文字含む） = 2
        else:
            count += 2
    return count


def count_threads(text: str) -> int:
    """Threads の文字数。

    厳密な grapheme カウント実装は重いので len() で代用（絵文字 1-2 程度の誤差は安全マージン -2 で吸収）。
    """
    return len(text)


def count_bluesky(text: str) -> int:
    """Bluesky の grapheme 数。

    厳密には grapheme cluster 単位だが、簡易実装で len() 代用。
    絵文字混在時の誤差は SAFE_LIMITS["bluesky"] = 298 で吸収。
    """
    return len(text)


def count(text: str, platform: str) -> int:
    """プラットフォーム別文字数を返す。"""
    if platform == "x":
        return count_x(text)
    if platform == "threads":
        return count_threads(text)
    if platform == "bluesky":
        return count_bluesky(text)
    raise ValueError(f"Unknown platform: {platform}")


def is_within_safe_limit(text: str, platform: str) -> tuple[bool, int, int]:
    """安全上限内か判定。

    Returns:
        (within_limit, current_count, safe_limit)
    """
    current = count(text, platform)
    limit = SAFE_LIMITS[platform]
    return current <= limit, current, limit
