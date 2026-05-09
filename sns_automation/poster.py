"""SNS 自動投稿システム メインエントリポイント

使用例:
    # スケジュール（cron トリガー）から呼ばれる用途
    python -m sns_automation.poster --schedule weekday_morning

    # note 投下時の告知（GitHub Actions workflow_dispatch から）
    python -m sns_automation.poster --schedule note_announce \\
        --var title="タイトル" --var summary="要約" --var url="https://note.com/..."

    # ドライラン（実投稿しない、テンプレ展開だけ確認）
    python -m sns_automation.poster --schedule weekday_morning --dry-run

    # 特定プラットフォームだけ
    python -m sns_automation.poster --schedule weekday_morning --only x,threads
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .env_loader import load_env
from .platforms import bluesky_poster, threads_poster, x_poster
from .utils.auto_trim import trim
from .utils.char_counter import is_within_safe_limit
from .utils.cross_review import review
from .utils.logger import setup_logging
from .utils.scheduler import load_schedule, resolve
from .utils.template_engine import VAR_PATTERN, HistoryStore, load_templates, render, select

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
CONFIG_TEMPLATES = ROOT / "config" / "templates.yaml"
CONFIG_SCHEDULE = ROOT / "config" / "schedule.yaml"
HISTORY_PATH = ROOT / ".history.json"  # ローカル履歴ファイル（gitignored）

POSTERS = {
    "x": x_poster,
    "threads": threads_poster,
    "bluesky": bluesky_poster,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SNS 自動投稿")
    parser.add_argument("--schedule", required=True, help="スケジュール名（schedule.yaml の name）")
    parser.add_argument("--dry-run", action="store_true", help="実投稿せずテンプレ展開だけ表示")
    parser.add_argument(
        "--only",
        default=None,
        help="対象プラットフォームをカンマ区切りで限定（例: x,threads）",
    )
    parser.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="テンプレ変数を追加（複数可）。例: --var title=テスト",
    )
    return parser.parse_args()


def parse_vars(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            logger.warning("--var の形式が不正（KEY=VALUE 必須）: %s", item)
            continue
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main() -> int:
    args = parse_args()
    setup_logging()
    load_env()

    variables = parse_vars(args.var)
    only = {p.strip() for p in args.only.split(",")} if args.only else None

    schedules = load_schedule(CONFIG_SCHEDULE)
    slot = resolve(schedules, args.schedule)
    logger.info("スケジュール選択: %s (cron=%s)", slot.name, slot.cron or "(manual)")

    templates = load_templates(CONFIG_TEMPLATES)
    history = HistoryStore(HISTORY_PATH)

    failures: list[str] = []
    for platform, category in slot.platforms.items():
        if only and platform not in only:
            logger.info("[%s] スキップ（--only 指定外）", platform)
            continue
        if platform not in POSTERS:
            logger.error("[%s] 未対応プラットフォーム", platform)
            failures.append(platform)
            continue

        try:
            cat_templates = templates.get(platform, {}).get(category)
            if not cat_templates:
                logger.error("[%s] カテゴリ %s のテンプレが見つからない", platform, category)
                failures.append(platform)
                continue

            template = select(
                cat_templates,
                history.get(platform),
                available_vars=set(variables.keys()),
            )
            text = render(template, variables).strip()

            # 未解決の {{var}} が残ってたら投稿しない（リテラルが SNS に出るのを防ぐ）
            unresolved = VAR_PATTERN.findall(text)
            if unresolved:
                logger.error(
                    "[%s] テンプレ %s に未解決変数 %s → 投稿スキップ",
                    platform, template.id, unresolved,
                )
                failures.append(platform)
                continue

            # ────────────────────────────────────────
            # 字余り自動修正（v2 ルール、2026-05-09 追加）
            # ────────────────────────────────────────
            within, current_count, safe_limit = is_within_safe_limit(text, platform)
            if not within:
                logger.warning(
                    "[%s] 字余り検出: %d / %d 字（安全上限）→ 自動修正試行",
                    platform, current_count, safe_limit,
                )
                trimmed_text, trim_actions = trim(text, platform)
                if trimmed_text is None:
                    logger.error(
                        "[%s] 字余り自動修正不能 → 投稿スキップ。アクション: %s",
                        platform, trim_actions,
                    )
                    failures.append(platform)
                    continue
                logger.info(
                    "[%s] 字余り自動修正成功（%d → %d 字）。アクション: %s",
                    platform,
                    current_count,
                    is_within_safe_limit(trimmed_text, platform)[1],
                    trim_actions,
                )
                text = trimmed_text

            # ────────────────────────────────────────
            # クロスレビュー（品質ちゃん + 整合ちゃん、v2 ルール）
            # ────────────────────────────────────────
            review_result = review(text, platform)
            if not review_result.passed:
                logger.error(
                    "[%s] クロスレビュー NG → 投稿スキップ\n品質: %s\n整合: %s",
                    platform,
                    review_result.issues_quality,
                    review_result.issues_strategy,
                )
                failures.append(platform)
                continue

            logger.info("[%s] テンプレ %s 選択", platform, template.id)
            logger.info("[%s] 投稿本文:\n%s", platform, text)

            if args.dry_run:
                logger.info("[%s] dry-run: 実投稿スキップ", platform)
                continue

            poster_module = POSTERS[platform]
            post_id = poster_module.post(text)
            logger.info("[%s] 投稿成功: id=%s", platform, post_id)

            history.append(platform, template.id)

            # スパム判定回避: 媒体間で 5-15 秒のランダム待機
            import random
            time.sleep(random.uniform(5, 15))
        except Exception as e:
            logger.exception("[%s] 投稿失敗: %s", platform, e)
            failures.append(platform)

    if failures:
        logger.error(
            "★★★ 投稿失敗 ★★★ 対象媒体: %s（%d/%d）",
            ", ".join(failures),
            len(failures),
            len(slot.platforms),
        )
        logger.error("→ ログを遡って該当媒体の詳細エラーを確認してください")
        logger.error(
            "→ 字余り自動修正不能 / 致命 NG ワード検出 / API エラー のいずれか"
        )
        return 1
    logger.info("✅ 全 %d 媒体 投稿完了", len(slot.platforms))
    return 0


if __name__ == "__main__":
    sys.exit(main())
