"""
タスクスケジューラから 1 時間ごとに呼ばれるエントリスクリプト

【動作モード（A 案: Discord Bot 流）】
1. INBOX を見て、新着の【ClipGift エラー報告】メールを state 登録
   → Phase: received（待機。Claude は起動しない）
2. Sent フォルダで kyohei さん返信を検知
   - Re: 【ClipGift エラー報告】... → Phase: analyzing
       → Claude 起動（解析・修正・案作成）→ 確認依頼メール送信 → Phase: awaiting_approval
   - Re: 【ClipGift 確認依頼】<hash> → Phase: executing
       → Claude 起動（push + ユーザー返信送信）→ Phase: done

ログ: support_center/incoming/_watcher.log
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from support_center import claude_runner  # noqa: E402
from support_center.config import SupportConfig  # noqa: E402
from support_center.mail_watcher import fetch_triggers  # noqa: E402
from support_center.notify_kyohei import notify_review_request  # noqa: E402


def _setup_logging() -> None:
    SupportConfig.INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    log_path = SupportConfig.INCOMING_DIR / "_watcher.log"
    handlers: list[logging.Handler] = [
        logging.FileHandler(str(log_path), encoding="utf-8"),
    ]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=handlers,
        force=True,
    )


def main() -> int:
    _setup_logging()
    logger = logging.getLogger("watch_support_mail")
    logger.info("==== サポート対話 watcher 開始 ====")

    errors = SupportConfig.validate_local()
    if errors:
        logger.warning("ローカル設定不備（部分動作）: %s", ", ".join(errors))

    triggers = fetch_triggers()
    logger.info("検知トリガー数: %d", len(triggers))

    if not triggers:
        logger.info("処理対象なし、終了")
        return 0

    success = 0
    failure = 0

    for trig in triggers:
        logger.info(
            "--- トリガー処理開始 phase=%s hash=%s ---",
            trig.phase,
            trig.error_hash,
        )

        try:
            if trig.phase == "analyzing":
                # Claude 解析 → 修正 → 案作成
                incident = claude_runner.run_analyze(
                    trig.error_hash,
                    trig.reply_body,
                )
                if incident is None or incident.state != "awaiting_approval":
                    logger.warning("解析失敗 hash=%s", trig.error_hash)
                    failure += 1
                    continue

                # 確認依頼メール送信
                if not notify_review_request(incident):
                    logger.warning("確認依頼メール送信失敗 hash=%s", trig.error_hash)
                    failure += 1
                    continue

                logger.info("解析+確認依頼送信完了 hash=%s", trig.error_hash)
                success += 1

            elif trig.phase == "executing":
                # push + ユーザー送信
                incident = claude_runner.run_execute(
                    trig.error_hash,
                    trig.reply_body,
                )
                if incident is None or incident.state != "done":
                    logger.warning("実行失敗 hash=%s", trig.error_hash)
                    failure += 1
                    continue

                logger.info("実行（push+ユーザー送信）完了 hash=%s", trig.error_hash)
                success += 1

            else:
                logger.warning("未対応フェーズ: %s", trig.phase)
                failure += 1
        except Exception as e:
            logger.error(
                "トリガー処理例外 hash=%s phase=%s err=%s",
                trig.error_hash,
                trig.phase,
                e,
            )
            failure += 1

    logger.info(
        "==== watcher 終了 (成功 %d / 失敗 %d) ====",
        success,
        failure,
    )
    return 0 if failure == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
