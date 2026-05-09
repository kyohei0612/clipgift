"""字余り自動修正ロジック。

安全上限を超えたテキストを段階的に削減する。優先順位:
1. AI フィラーワード削除（「ようやく」「実は」等、削っても意味が大きく変わらない）
2. 動詞短縮（「書いていきます」→「書きます」等）
3. 接続節短縮
4. ハッシュタグ数削減（最終手段、訴求弱まるので最後）
5. それでも収まらなければ手戻り（None 返す）

設計書: marketing/sns/2026-05-09-ai-rinse-research.md §1.3
"""

from __future__ import annotations

import logging

from .char_counter import SAFE_LIMITS, count

logger = logging.getLogger(__name__)


# 優先順位 1: AI フィラーワード削除
# 削っても文意が大きく変わらない、AI 臭の典型ブースター
AI_FILLERS = [
    "ようやく",
    "やっと",
    "結局のところ",
    "結局",
    "もちろん",
    "実は",
    "とにかく",
    "ちなみに",
    "なお、",
    "なお ",
    "また、",
    "さらに、",
    "そして、",
    "そこで、",
]

# 優先順位 2: 動詞短縮ルール（敬体 → 口語化、意味は同じ）
# ★注意: ペルソナ語彙として意図的に使われてる可能性のある表現は短縮しない
#   - 「ハマってた」→「ハマった」: ニュアンス変わる、ペルソナ崩れ → 除外
#   - 「やってる」「作ってる」「使ってる」: ペルソナ語彙そのもの → 除外
VERB_SHORTENINGS = [
    ("書いていきます", "書きます"),
    ("やっていきます", "やります"),
    ("作っていきます", "作ります"),
    ("していきます", "します"),
    ("頑張っていきます", "頑張ります"),
    ("やっています", "やってます"),
    ("作っています", "作ってます"),
    ("使っています", "使ってます"),
    ("ハマっていた", "ハマってた"),
    # 「ハマってた」→「ハマった」は削除（ペルソナ語彙として意図的に使われてる場合に壊れる）
]

# 優先順位 3: 冗長な接続節
REDUNDANT_PHRASES = [
    ("ところで、", ""),
    ("ところで ", ""),
    ("まずは ", ""),
    ("まずは、", ""),
    ("ということで、", ""),
    ("という感じで、", ""),
]


def trim(
    text: str,
    platform: str,
    max_iterations: int = 20,
) -> tuple[str | None, list[str]]:
    """テキストを安全上限内に収めるため段階的削減を試みる。

    Args:
        text: 元テキスト
        platform: "x" / "threads" / "bluesky"
        max_iterations: 最大反復回数（無限ループ防止）

    Returns:
        (修正後テキスト, 適用アクションのログ)
        修正不能の場合は (None, [...])
    """
    actions: list[str] = []
    limit = SAFE_LIMITS[platform]
    current = count(text, platform)

    if current <= limit:
        return text, actions

    actions.append(
        f"開始: {current} 字（{platform} 安全上限 {limit} 超過 +{current - limit}）"
    )

    for _ in range(max_iterations):
        current = count(text, platform)
        if current <= limit:
            actions.append(f"完了: {current} 字に収まった")
            return text, actions

        # ルール 1: AI フィラー削除
        result = _try_replace(text, AI_FILLERS, mark="AI フィラー削除")
        if result is not None:
            text, action = result
            actions.append(action)
            continue

        # ルール 2: 動詞短縮
        result = _try_replace_pairs(text, VERB_SHORTENINGS, mark="動詞短縮")
        if result is not None:
            text, action = result
            actions.append(action)
            continue

        # ルール 3: 冗長フレーズ削除
        result = _try_replace_pairs(text, REDUNDANT_PHRASES, mark="冗長フレーズ削除")
        if result is not None:
            text, action = result
            actions.append(action)
            continue

        # ルール 4: ハッシュタグ削減（最終手段）
        new_text = _trim_one_hashtag(text)
        if new_text is not None:
            text = new_text
            actions.append("ハッシュタグ 1 個削除")
            continue

        # 全ルール尽きた → 手戻り
        actions.append(
            f"修正不能: {current} 字（あと {current - limit} 字オーバー）。手動修正必要"
        )
        return None, actions

    actions.append("最大反復回数到達")
    return None, actions


def _try_replace(
    text: str, words: list[str], mark: str
) -> tuple[str, str] | None:
    """words の中で text に最初に見つかったものを 1 つだけ削除する。

    削除後に句読点ゴミが残らないようクリーンアップ:
    - 「結局のところ、」を削除すると「、」が残る → 直前の単語と「、」も一緒に消す
    - 連続スペースを単一に
    - 行頭の句読点を消す

    Returns: (新 text, アクション説明) / None（該当なし）
    """
    import re

    for word in words:
        if word in text:
            new_text = text.replace(word, "", 1)
            # 削除後のクリーンアップ
            # 1. 行頭の「、」「。」を削除
            new_text = re.sub(r"(\n|^)[、。]\s*", r"\1", new_text)
            # 2. 連続「、、」「、 、」「  」を単一化
            new_text = re.sub(r"、\s*、", "、", new_text)
            new_text = re.sub(r"  +", " ", new_text)
            # 3. 行末の単独「、」を削除
            new_text = re.sub(r"、\s*(\n|$)", r"\1", new_text)
            return new_text, f"{mark}: 「{word}」"
    return None


def _try_replace_pairs(
    text: str, pairs: list[tuple[str, str]], mark: str
) -> tuple[str, str] | None:
    """pairs の中で最初に見つかった置換を 1 つだけ実行する。

    Returns: (新 text, アクション説明) / None（該当なし）
    """
    for src, dst in pairs:
        if src in text:
            new_text = text.replace(src, dst, 1)
            return new_text, f"{mark}: 「{src}」→「{dst}」"
    return None


def _trim_one_hashtag(text: str) -> str | None:
    """末尾のハッシュタグを 1 個削除する。

    `#xxx #yyy #zzz` のような末尾連の最後の 1 個を削るのが安全。
    Returns: 新 text / None（ハッシュタグなし）
    """
    import re

    # 末尾ハッシュタグを検出（直前にスペースまたは行頭）
    m = re.search(r"\s+#\S+\s*$", text)
    if not m:
        m = re.search(r"^#\S+\s*$", text)
    if m:
        return text[: m.start()].rstrip()
    return None
