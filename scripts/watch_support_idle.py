"""
常駐モード watcher（Discord Bot 流リアルタイム反応）

Python が常駐して 30 秒ごとに Sent フォルダを確認、
kyohei さんの返信を検知したら即 Claude を起動する。

【動作仕様】
- 30 秒ポーリング（IMAP 接続を毎回張り直すシンプル構成）
- 待機中は CPU/メモリ消費極小、Claude API 消費ゼロ
- Ctrl+C / プロセス終了で正常停止
- 無限ループ実行（タスクスケジューラから「ログオン時起動」で常駐）

【起動方法】
  pythonw.exe C:\\Users\\kyohei\\ClipGift\\scripts\\watch_support_idle.py

  ログオン時自動起動（管理者 PowerShell）:
    Register-ScheduledTask 等で設定（README 参照）

ログ: support_center/incoming/_idle_watcher.log
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import sys
import time
from pathlib import Path

# プロジェクトルートを sys.path に追加
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from support_center import claude_runner  # noqa: E402
from support_center.config import SupportConfig  # noqa: E402
from support_center.mail_watcher import (  # noqa: E402
    fetch_triggers,
    get_boot_time,
    initialize_processed_ids_if_missing,
    mark_sent_processed,
)
from support_center.notify_kyohei import notify_review_request, notify_invalid_command  # noqa: E402


# ────────────────────────────────────────────────────────────
# 多重起動防止: ロックファイル + 既存プロセス takeover
# ────────────────────────────────────────────────────────────

_LOCK_FILE = SupportConfig.INCOMING_DIR / "_idle_watcher.lock"


def _acquire_lock_with_takeover() -> None:
    """ロック取得。既存 watcher プロセスがあれば kill して上書き起動。"""
    SupportConfig.INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    if _LOCK_FILE.exists():
        try:
            existing_pid_str = _LOCK_FILE.read_text(encoding="utf-8").strip()
            existing_pid = int(existing_pid_str)
            if existing_pid != os.getpid():
                # 既存を kill（同 PID なら自分自身なのでスキップ）
                try:
                    os.kill(existing_pid, signal.SIGTERM)
                    time.sleep(1)
                    # まだ生きてるか確認 → 強制 kill
                    try:
                        os.kill(existing_pid, 0)
                        os.kill(existing_pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        pass
                except (OSError, ProcessLookupError):
                    pass  # 既に終了済
        except (ValueError, OSError):
            pass
    # 自分の PID で書き換え
    try:
        _LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def _release_lock() -> None:
    try:
        if _LOCK_FILE.exists():
            content = _LOCK_FILE.read_text(encoding="utf-8").strip()
            if content == str(os.getpid()):
                _LOCK_FILE.unlink()
    except OSError:
        pass


atexit.register(_release_lock)


POLL_INTERVAL_SECONDS = 30
ERROR_BACKOFF_SECONDS = 60   # 連続エラー時のバックオフ
MAX_CONSECUTIVE_ERRORS = 5    # この回数連続エラーで一時退避


_should_stop = False


def _signal_handler(signum, frame):
    global _should_stop
    _should_stop = True


def _setup_logging() -> None:
    SupportConfig.INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    log_path = SupportConfig.INCOMING_DIR / "_idle_watcher.log"
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


def _process_triggers(logger: logging.Logger) -> int:
    """1 回分のトリガー処理。検知件数を返す。"""
    triggers = fetch_triggers()
    if not triggers:
        return 0

    logger.info("検知トリガー数: %d", len(triggers))
    processed = 0

    for trig in triggers:
        try:
            logger.info(
                "--- 処理開始 phase=%s hash=%s type=%s ---",
                trig.phase,
                trig.error_hash,
                trig.report_type,
            )

            if trig.phase == "analyzing":
                # ユーザー報告（type=error のみ自動）→ 解析・修正・push
                incident = claude_runner.run_analyze_and_push(trig.error_hash)
                if incident is None or incident.state != "awaiting_approval":
                    logger.warning("解析失敗 hash=%s", trig.error_hash)
                    continue
                if not notify_review_request(incident):
                    logger.warning("確認依頼メール送信失敗 hash=%s", trig.error_hash)
                    continue
                logger.info("✓ 解析+push+確認依頼完了 hash=%s", trig.error_hash)
                processed += 1

            elif trig.phase == "dialog":
                # kyohei 返信を高速判定（Claude 介さず承認 / 拒否 / 無効を判定）
                incident, action = claude_runner.run_dialog(
                    trig.error_hash,
                    trig.reply_body,
                    report_type=trig.report_type,
                )
                if incident is None:
                    logger.warning("dialog 失敗 hash=%s", trig.error_hash)
                    mark_sent_processed(trig.sent_message_id)
                    continue

                if action == "approve":
                    # ユーザー送信実行
                    incident = claude_runner.run_send_to_user(trig.error_hash)
                    if incident and incident.state == "done":
                        logger.info("✓ ユーザー送信完了 hash=%s", trig.error_hash)
                    else:
                        logger.warning("ユーザー送信失敗 hash=%s", trig.error_hash)

                elif action == "abort":
                    logger.info("✓ 中止確認 hash=%s", trig.error_hash)

                else:  # action == "invalid"
                    # 「認識されませんでした」を kyohei さんに通知
                    if notify_invalid_command(incident, trig.reply_body):
                        logger.info("✓ コマンド認識エラー通知送信 hash=%s", trig.error_hash)
                    else:
                        logger.warning("コマンド認識エラー通知失敗 hash=%s", trig.error_hash)

                mark_sent_processed(trig.sent_message_id)
                processed += 1

            else:
                logger.warning("未対応フェーズ: %s", trig.phase)

        except Exception as e:
            logger.error(
                "トリガー処理例外 hash=%s phase=%s err=%s",
                trig.error_hash,
                trig.phase,
                e,
            )
            # 無限再 trigger 防止: 例外時は state=error に遷移
            try:
                from support_center import state_machine
                inc = state_machine.load(trig.error_hash)
                if inc and inc.state in ("received", "analyzing"):
                    state_machine.transition(trig.error_hash, "error")
                    logger.info("state=error に遷移（無限ループ防止）hash=%s", trig.error_hash)
            except Exception:
                pass

    return processed


def main() -> int:
    _setup_logging()
    logger = logging.getLogger("idle_watcher")

    # 多重起動防止 → 既存があれば kill して上書き
    _acquire_lock_with_takeover()

    logger.info(
        "==== 常駐 watcher 起動（%d 秒ポーリング, PID=%d）====",
        POLL_INTERVAL_SECONDS,
        os.getpid(),
    )

    # 初回起動 or クリーンアップ後の Sent 履歴初期化
    # これで watcher 再起動時に過去の kyohei 返信を再処理しない
    try:
        initialize_processed_ids_if_missing()
    except Exception as e:
        logger.warning("Sent 履歴初期化失敗（続行）: %s", e)

    # boot_time の確認 / 初期化（INBOX の処理境界）
    try:
        boot = get_boot_time()
        logger.info("メール処理境界（boot_time）: %s", boot.isoformat(timespec="seconds"))
    except Exception as e:
        logger.warning("boot_time 初期化失敗: %s", e)

    # シグナルハンドラ登録（Ctrl+C / kill で正常終了）
    try:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, AttributeError):
        # スレッド非メイン or プラットフォーム非対応の場合はスキップ
        pass

    consecutive_errors = 0
    last_log_alive_at = 0.0

    while not _should_stop:
        try:
            processed = _process_triggers(logger)
            consecutive_errors = 0  # 成功すればリセット

            # 1 時間に 1 回「生きてるよ」ログ（過剰ログ抑制）
            now = time.time()
            if now - last_log_alive_at > 3600:
                logger.info("[alive] 待機中、%d 件処理済み", processed)
                last_log_alive_at = now

        except Exception as e:
            consecutive_errors += 1
            logger.error(
                "ポーリングエラー (%d/%d): %s",
                consecutive_errors,
                MAX_CONSECUTIVE_ERRORS,
                e,
            )

            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                logger.error(
                    "連続エラー %d 回、%d 秒のバックオフへ",
                    consecutive_errors,
                    ERROR_BACKOFF_SECONDS,
                )
                _sleep_with_check(ERROR_BACKOFF_SECONDS)
                consecutive_errors = 0
                continue

        _sleep_with_check(POLL_INTERVAL_SECONDS)

    logger.info("==== 常駐 watcher 終了 ====")
    return 0


def _sleep_with_check(seconds: int) -> None:
    """should_stop を見ながら細かく sleep する（Ctrl+C 反応性のため）。"""
    end = time.time() + seconds
    while time.time() < end and not _should_stop:
        time.sleep(min(1.0, end - time.time()))


if __name__ == "__main__":
    sys.exit(main())
