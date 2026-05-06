"""
チャットコメントの共通フィルタ

YouTube / Twitch 両方のチャット取得で同じロジックでフィルタする。

スキップ対象:
- 空コメント（スタンプ / エモートだけだったコメント等）
- 日本語を含まず、3 文字以上の英単語（"Kappa" "Hello" "lol" "PogChamp" 等）
- Bot ユーザー（Nightbot, StreamElements 等）
- システムメッセージ（サブスク通知 / Bits / ウォッチストリーク / レイド等）

残す:
- 日本語を含むコメント（"草" "面白い" "感動した" 等）
- 短い英数字（"ww" "ok" "GG" 等の 2 文字以下）
- 1 文字の繰り返し（"wwww" "88888" 等のチャットスラング）
"""

import re

# 絵文字 / ピクトグラム範囲（BIZ UDゴシック等の通常フォントでは描画できず "□" になる）
# 日本語文字（ひらがな・カタカナ・漢字 BMP）は含まない
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F700-\U0001F77F"  # alchemical symbols
    "\U0001F780-\U0001F7FF"  # geometric extended
    "\U0001F800-\U0001F8FF"  # supplemental arrows-C
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess
    "\U0001FA70-\U0001FAFF"  # extended pictographs A
    "\U00002600-\U000026FF"  # miscellaneous symbols (☀ ☁ ★ ♡ 等)
    "\U00002700-\U000027BF"  # dingbats (❤ ❌ ✓)
    "\U0001F1E6-\U0001F1FF"  # regional indicator (flags)
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0001F018-\U0001F270"
    "]+"
)


def strip_emojis(text: str) -> str:
    """絵文字 / ピクトグラム / 記号を取り除いて返す（日本語・英数字は残す）"""
    return _EMOJI_RE.sub("", text)


# 既知の Bot ユーザー（このユーザーの発言は全部スキップ）
KNOWN_BOTS = {
    "Nightbot",
    "StreamElements",
    "Moobot",
    "Streamlabs",
    "Fossabot",
    "Wizebot",
    "DeepBot",
    "PhantomBot",
}

# システムメッセージのパターン（Twitch / YouTube 両対応）
# サブスク通知、Bits、ウォッチストリーク、レイド、ホスト等
_SYSTEM_PATTERNS = [
    # Twitch サブスク
    re.compile(r"subscribed with Prime", re.IGNORECASE),
    re.compile(r"subscribed for \d+ months?", re.IGNORECASE),
    re.compile(r"resubscribed", re.IGNORECASE),
    re.compile(r"\bmonth streak\b", re.IGNORECASE),
    # ウォッチストリーク
    re.compile(r"watched \d+ consecutive streams", re.IGNORECASE),
    re.compile(r"sparked a watch streak", re.IGNORECASE),
    # ギフトサブ
    re.compile(r"is gifting \d+", re.IGNORECASE),
    re.compile(r"gifted a Tier \d+ sub", re.IGNORECASE),
    re.compile(r"\d+ Tier \d+ Subs", re.IGNORECASE),
    # Bits
    re.compile(r"cheered \d+ Bits?", re.IGNORECASE),
    re.compile(r"\bCheer\d+\b"),  # "Cheer100", "Cheer1000" 等
    re.compile(r"\bbitsbadgetier\b", re.IGNORECASE),
    # レイド / ホスト
    re.compile(r"raided this channel", re.IGNORECASE),
    re.compile(r"is now hosting", re.IGNORECASE),
    re.compile(r"\d+ viewers from", re.IGNORECASE),
    # YouTube システム（メンバーシップ・スパチャ通知系）
    re.compile(r"became a member"),
    re.compile(r"new member"),
    re.compile(r"upgraded their membership"),
]


def is_known_bot(user: str) -> bool:
    """既知の Bot ユーザーかどうか"""
    return user.strip() in KNOWN_BOTS


def is_system_message(text: str) -> bool:
    """サブスク / Bits / レイド等のシステムメッセージか"""
    for pat in _SYSTEM_PATTERNS:
        if pat.search(text):
            return True
    return False


def has_japanese_char(text: str) -> bool:
    """日本語文字（ひらがな / カタカナ / 漢字 / 全角文字）を含むか"""
    for c in text:
        # ひらがな
        if "぀" <= c <= "ゟ":
            return True
        # カタカナ
        if "゠" <= c <= "ヿ":
            return True
        # CJK 統合漢字
        if "一" <= c <= "鿿":
            return True
        # CJK 拡張 A
        if "㐀" <= c <= "䶿":
            return True
        # 全角形（全角ｗ・全角数字・全角記号等）
        if "＀" <= c <= "￯":
            return True
    return False


def should_skip_comment(text: str, user: str = "") -> bool:
    """
    このコメントをフィルタアウトすべきか判定

    Args:
        text: コメント本文
        user: 発言者名（Bot 検出用、省略可）

    Returns:
        True なら CSV に書き出さない
    """
    # Bot ユーザー（Nightbot 等）→ 全スキップ
    if user and is_known_bot(user):
        return True

    text = text.strip()
    if not text:
        return True  # 空 → スキップ（スタンプ / エモートだけ等）

    # システムメッセージ（サブスク / Bits / ウォッチストリーク / レイド）→ スキップ
    if is_system_message(text):
        return True

    if has_japanese_char(text):
        return False  # 日本語あり → 残す
    if len(text) <= 2:
        return False  # 短すぎ → 残す（ww, ok 等）
    chars_no_space = text.replace(" ", "")
    if len(set(chars_no_space)) <= 1:
        return False  # 1 種類の繰り返し → 残す（wwww, 8888 等のチャットスラング）
    return True  # 英単語っぽい（Kappa, Hello, lol, etc.）→ スキップ
