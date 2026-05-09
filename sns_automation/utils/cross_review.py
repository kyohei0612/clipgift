"""SNS 投稿クロスレビュー（品質ちゃん + 整合ちゃん相当）。

ルールベース判定の軽量版。Claude API は使わず、AI 臭ワード辞書 +
NG 表現リスト + 必須要素チェックで投稿可否を判定する。

将来的に Claude API 経由に拡張可能（CrossReviewClaude クラスを別途作成）。

設計書: marketing/sns/2026-05-09-ai-rinse-research.md §1.3 / §5.1
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# 品質ちゃんの判定ルール
# ────────────────────────────────────────────────────────────

# 致命 NG ワード（即否決）
# 一つでも含まれていたら投稿スキップ。kyohei さんの方針 + 5/9 リサーチ結論。
FATAL_NG_WORDS = [
    # 営業臭
    "業務道具として",
    "最後のチャンス",
    "ぜひお試しください",
    "ぜひご覧ください",
    "お見逃しなく",
    "必見",
    "今だけ",
    "今がチャンス",
    "チャンスは今",
    # 情報商材色（過大広告 / 教材臭）
    "楽して稼げる",
    "楽して稼ぐ",
    "ほったらかしで",
    "魔法のツール",
    "未経験でも",
    "未経験から",
    "初心者でも",
    "初心者から",
    "これさえあれば",
    "これだけで稼げる",
    "誰でも稼げる",
    "絶対稼げる",
    "確実に稼げる",
    "必ず儲かる",
    "絶対に儲かる",
    "副業で人生変わ",
    "人生が変わる",
    # AI 量産記事のテンプレ
    "いかがでしょうか",
    "いかがでしたでしょうか",
    "最後まで読んでいただき",
    "お役に立てば幸いです",
    "お読みいただきありがとう",
    # 過剰 enthusiasm
    "画期的",
    "革新的",
    "素晴らしい",
    "最強の",
    "神ツール",
    "神アプリ",
    "神機能",
    # AI 臭枕詞
    "現代社会において",
    "昨今では",
    "いま、",
    # X / Threads / Bluesky 共通の SLACK BOT 臭見出し
    "【公開】",
    "【告知】",
    "【お知らせ】",
    "【新発売】",
    "【期間限定】",
    "【先着】",
    "【速報】",
    # ★ 公式アカウント運用上 NG な汚い言葉（kyohei さん指示 2026-05-09 夜）
    # 個人 X では OK でも、ClipGift 公式アバター運用では避ける
    "クソすぎ",
    "クソゲー",
    "クソ",
    "ゴミすぎ",
    "ゴミ",
    "ムカつく",
    "うざい",
    "ウザい",
    "キモい",
    "きもい",
    "死ね",
    "殺す",
    "バカ",
    "アホ",
    "クズ",
    "ks",  # 略語スラング
]


# AI 臭ワード（要警告、削除候補）
AI_AROMA_WORDS = [
    "ようやく",
    "やっと",
    "結局",
    "もちろん",
    "実は、",
    "ちなみに、",
    "とにかく",
]


# ペルソナ語彙（最低 1 個含まれていることが望ましい、人間味の指標）
PERSONA_WORDS = [
    # 動詞語尾（開発者・切り抜き師目線）
    "やってる",
    "作ってる",
    "使ってる",
    "してる",
    "やった",
    "作った",
    "使った",
    "ハマって",
    "ハマった",
    # 家族・コラボ
    "うちの妻",
    "妻",
    # 弱音・感情
    "やばい",
    "しんどい",
    "むずい",
    "だるい",
    "笑う",
    "笑った",
    # 共感誘発語尾
    "なんですよね",
    "じゃないですか",
    "気がする",
    "だと思う",
    "かも",
    # 配信ライト視聴者層向け（カテゴリ F、2026-05-09 夜追加）
    "リアタイ",
    "配信観て",
    "配信見て",
    "見返す",
    "見返して",
    "あのシーン",
    "覚えてる",
    "コメント欄",
    "アーカイブ",
    "うあああ",
]


# ────────────────────────────────────────────────────────────
# 整合ちゃんの判定ルール
# ────────────────────────────────────────────────────────────

# 媒体特有チェック
MEDIA_SPECIFIC_CHECKS = {
    "bluesky": {
        # Bluesky で致命的な広告認定リスク
        "fatal_extra": [
            "業務道具として",  # 既に FATAL に入ってるが、Bluesky では特に致命
            "○○向けに作りました",
            "向けに作りました",
        ],
    },
    "threads": {
        # Threads は文末問い必須（別途 require_question で確認）
    },
    "x": {
        # X は既存ルール継承
    },
}


@dataclass
class ReviewResult:
    """クロスレビュー結果。

    - issues_quality / issues_strategy: 致命的（投稿不可）
    - warnings: 警告のみ（投稿は通す、後で見直す材料）
    """

    passed: bool
    issues_quality: list[str] = field(default_factory=list)
    issues_strategy: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def all_issues(self) -> list[str]:
        return self.issues_quality + self.issues_strategy


def has_number_or_proper_noun(text: str) -> bool:
    """数字または固有名詞（カタカナ連）を含むか。

    5/9 リサーチ「数字 / 固有モノが本文に 1 つ以上」必須要件のチェック。
    """
    if re.search(r"\d", text):
        return True
    # カタカナ 3 字以上連続 = 固有名詞らしさ
    if re.search(r"[゠-ヿ]{3,}", text):
        return True
    return False


def has_persona_voice(text: str) -> bool:
    """ペルソナ語彙を 1 つ以上含むか。"""
    return any(w in text for w in PERSONA_WORDS)


def has_question_at_end(text: str, last_n_lines: int = 3) -> bool:
    """末尾付近に「？」を含む文があるか（Threads 用）。"""
    lines = text.rstrip().split("\n")[-last_n_lines:]
    return any("？" in line or "?" in line for line in lines)


def review_quality(text: str, platform: str) -> tuple[list[str], list[str]]:
    """品質ちゃん観点のレビュー。

    Returns:
        (issues, warnings)
        issues: 致命的（投稿不可）/ warnings: 注意のみ（投稿は通す）
    """
    issues: list[str] = []
    warnings: list[str] = []

    # === 致命（投稿不可） ===
    # 致命 NG ワード
    for ng in FATAL_NG_WORDS:
        if ng in text:
            issues.append(f"致命 NG ワード検出: 「{ng}」")

    # === 警告のみ（投稿は通す） ===
    # AI 臭ワード（2 個以上で警告）
    aroma_found = [w for w in AI_AROMA_WORDS if w in text]
    if len(aroma_found) >= 2:
        warnings.append(f"AI 臭ワード複数検出（{len(aroma_found)}個）: {aroma_found}")

    # 数字 / 固有モノなし
    if not has_number_or_proper_noun(text):
        warnings.append("数字 / 固有モノが 1 つもない（5/9 リサーチ推奨要件）")

    # ペルソナ語彙なし（参考シグナルのみ。リストに無い人間語もあるので警告のみ）
    if not has_persona_voice(text):
        warnings.append(
            "ペルソナ語彙リストにマッチなし（参考シグナル。普通に人間っぽい表現でもリスト外なら出る）"
        )

    # 句点 3 連打（4 回以上で警告）
    sentences_ending_des = re.findall(r"です。", text)
    if len(sentences_ending_des) >= 4:
        warnings.append(f"「〜です。」が {len(sentences_ending_des)} 回連打（AI 臭の可能性）")

    # 鉤括弧「」の使用頻度（2 個以上 = ペアで 2 ヶ所以上使用 = AI コマンド臭）
    # 人間は SNS で「」をほぼ使わない（kyohei さん指摘 2026-05-09）
    bracket_count = text.count("「")
    if bracket_count >= 2:
        warnings.append(
            f"鉤括弧「」が {bracket_count} ヶ所（人間は SNS で「」をほぼ使わない、AI コマンド臭の可能性）"
        )

    # 【】の使用は致命 NG として既に検出済み（FATAL_NG_WORDS の【公開】等）
    # ここでは追加で「単独【】」も警告
    if "【" in text and "】" in text:
        # 致命 NG リストにマッチしてる場合は既に issues に入ってるので、ここはスキップ
        pass  # 致命検出は FATAL_NG_WORDS で対応済

    return issues, warnings


def review_strategy(text: str, platform: str) -> tuple[list[str], list[str]]:
    """整合ちゃん観点のレビュー。

    Returns: (issues, warnings)
    """
    issues: list[str] = []
    warnings: list[str] = []

    # === 致命 ===
    # 媒体特有 NG
    media_check = MEDIA_SPECIFIC_CHECKS.get(platform, {})
    for ng in media_check.get("fatal_extra", []):
        if ng in text:
            issues.append(f"[{platform}] 媒体特有 NG: 「{ng}」")

    # === 警告のみ ===
    # ★ Threads 文末問いを「警告」から外した（kyohei さん指示 2026-05-09 夜）
    #   毎回問いをすると逆に AI 臭。バランスは投稿履歴レベルで管理する別問題。
    #   ここでは単発投稿の判定だけにとどめる。

    return issues, warnings


def review(text: str, platform: str) -> ReviewResult:
    """品質ちゃん + 整合ちゃんによるクロスレビュー。

    issues（致命的）があれば passed=False。warnings は投稿を妨げず、ログのみ。
    """
    quality_issues, quality_warnings = review_quality(text, platform)
    strategy_issues, strategy_warnings = review_strategy(text, platform)

    passed = len(quality_issues) == 0 and len(strategy_issues) == 0
    all_warnings = quality_warnings + strategy_warnings

    if not passed:
        logger.warning(
            "[%s] クロスレビュー NG (致命 品質: %d / 整合: %d)",
            platform,
            len(quality_issues),
            len(strategy_issues),
        )
    else:
        if all_warnings:
            logger.info(
                "[%s] クロスレビュー OK（警告 %d 件あり）",
                platform,
                len(all_warnings),
            )
        else:
            logger.info("[%s] クロスレビュー OK", platform)

    return ReviewResult(
        passed=passed,
        issues_quality=quality_issues,
        issues_strategy=strategy_issues,
        warnings=all_warnings,
    )
