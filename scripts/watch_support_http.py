"""
Cloudflare Email Worker 連携のローカル HTTP poller（v4 アーキテクチャ）

【動作モード】
1. 5 秒ごとに Workers `GET /support/pending` を叩く
2. 未処理トリガーがあれば種別に応じて分岐:
   - incoming_error    → state.json 登録（state=received）。Claude 起動はしない
   - incoming_request  → .company/support/requests/YYYY-MM-DD-<hash>.md に保管
   - reply_investigate → kyohei「調査」返信 → claude_runner.run_analyze_and_push 起動
   - reply_approve     → kyohei「OK」返信 → run_dialog → run_send_to_user
   - reply_abort       → state を error に
3. 処理完了したら `POST /support/processed/<id>` でトリガーマーカー削除

【起動方法】
  pythonw.exe C:\\Users\\kyohei\\ClipGift\\scripts\\watch_support_http.py

【設計】
  メール受信側は完全に Cloudflare Email Worker に移行（IMAP 廃止）。
  Worker が件名分類して KV に書き、こちらは HTTP poll するだけの軽量設計。

ログ: support_center/incoming/_http_watcher.log
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# プロジェクトルートを sys.path に追加
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import requests  # noqa: E402

from support_center import claude_runner, state_machine  # noqa: E402
from support_center.config import SupportConfig  # noqa: E402
from support_center.notify_kyohei import notify_review_request  # noqa: E402

logger = logging.getLogger("watch_support_http")

POLL_INTERVAL_SECONDS = 5
PENDING_URL_PATH = "/support/pending"
PROCESSED_URL_PATH_TEMPLATE = "/support/processed/{trigger_id}"

# 取得失敗が続いたときのバックオフ（Workers 障害時に 5 秒間隔で叩き続けない）
FAILURES_BEFORE_BACKOFF = 5
BACKOFF_SECONDS = 30

# 同じトリガーを何回まで処理し直すか。
# 再試行しないと失敗が取り残される（下の main 参照）が、無限に再試行すると
# 直らないトリガー 1 件で Claude を 5 秒おきに起動し続けることになる。
MAX_TRIGGER_ATTEMPTS = 3


# ────────────────────────────────────────────────────────────
# 終了ハンドリング
# ────────────────────────────────────────────────────────────

_shutdown_requested = False


def _request_shutdown(signum: int, frame: Any) -> None:  # noqa: ARG001
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("シャットダウン要求受信 (signal=%s)", signum)


# ────────────────────────────────────────────────────────────
# トリガー処理
# ────────────────────────────────────────────────────────────


def _hash_for(subject: str, message_id: str, body: str) -> str:
    """エラー報告固有のハッシュ生成（mail_watcher と同じロジック）。"""
    return hashlib.sha256(
        f"{subject}|{message_id}|{body[:300]}".encode("utf-8")
    ).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _extract_form_user_email(body: str) -> str:
    """メール本文から「ユーザー返信先: xxx@xxx」のメアドを抽出。"""
    if not body:
        return ""
    m = re.search(
        r"ユーザー返信先[:：]\s*([\w\.\-+]+@[\w\.\-]+)",
        body,
    )
    return m.group(1).strip() if m else ""


def _find_incident_by_in_reply_to(
    in_reply_to: str,
) -> Optional[state_machine.IncidentState]:
    """In-Reply-To から既存 incident を検索。"""
    if not in_reply_to:
        return None
    for path in SupportConfig.INCOMING_DIR.glob("*.state.json"):
        try:
            error_hash = path.stem.replace(".state", "")
            inc = state_machine.load(error_hash)
            if inc and inc.original_message_id == in_reply_to:
                return inc
        except Exception:
            continue
    return None


def _find_incident_by_subject(
    subject: str,
) -> Optional[state_machine.IncidentState]:
    """件名（"Re: " 除去後）から既存 incident を検索。

    同じ件名の incident が複数ある場合は **state=received/analyzing の最新** を優先。
    過去の state=done エントリには引っかけない（同じ subject の旧テストとの混同防止）。
    """
    cleaned = re.sub(r"^\s*(Re|RE|Fwd|Fw|FW):\s*", "", subject).strip()
    if not cleaned:
        return None

    open_match: Optional[state_machine.IncidentState] = None
    for path in SupportConfig.INCOMING_DIR.glob("*.state.json"):
        try:
            error_hash = path.stem.replace(".state", "")
            inc = state_machine.load(error_hash)
            if inc is None or inc.original_subject.strip() != cleaned:
                continue
            # 進行中の incident のみ対象（done / error / awaiting_approval は別扱い）
            if inc.state in ("received", "analyzing"):
                if open_match is None or inc.updated_at > open_match.updated_at:
                    open_match = inc
        except Exception:
            continue
    return open_match


def _find_incident_for_reply(
    in_reply_to: str, subject: str
) -> Optional[state_machine.IncidentState]:
    return _find_incident_by_in_reply_to(in_reply_to) or _find_incident_by_subject(
        subject
    )


def _save_request_record(trigger: dict[str, Any]) -> None:
    """要望（incoming_request）を .company/support/requests/ に markdown 保存。"""
    SupportConfig.REQUESTS_DIR.mkdir(parents=True, exist_ok=True)

    subject = trigger.get("subject", "")
    body = trigger.get("body", "")
    message_id = trigger.get("message_id", "")
    received_at = trigger.get("received_at", _now_iso())

    today = datetime.now(timezone.utc).date().isoformat()
    req_hash = _hash_for(subject, message_id, body)
    file_path = SupportConfig.REQUESTS_DIR / f"{today}-{req_hash}.md"
    if file_path.exists():
        logger.info("要望ファイル既存（スキップ）: %s", file_path.name)
        return

    user_email = _extract_form_user_email(body)
    content = (
        "---\n"
        f'date: "{today}"\n'
        f'request_hash: "{req_hash}"\n'
        f'user_email: "{user_email}"\n'
        f'subject: "{subject}"\n'
        f'received_at: "{received_at}"\n'
        f'message_id: "{message_id}"\n'
        'status: "pending"  # pending / replied / closed\n'
        "---\n"
        "\n"
        f"# {subject}\n"
        "\n"
        "## 概要\n"
        "\n"
        "ユーザー連絡先（あれば）: "
        f"`{user_email or '（なし）'}`  \n"
        f"受信時刻: `{received_at}`\n"
        "\n"
        "## メール本文\n"
        "\n"
        "```\n"
        + body
        + "\n```\n"
        "\n"
        "## 対応メモ\n"
        "\n"
        "（kyohei さんが clipgift.dev@gmail.com 側から返信したらここに記録）\n"
    )
    file_path.write_text(content, encoding="utf-8")
    logger.info("要望保管完了: %s", file_path.name)


def _register_incoming_error(trigger: dict[str, Any]) -> Optional[str]:
    """incoming_error トリガーから state.json を新規登録（または既存に再 trigger）。

    Returns: error_hash（成功時）/ None（失敗時）
    """
    subject = trigger.get("subject", "").strip()
    body = trigger.get("body", "")
    message_id = trigger.get("message_id", "")
    error_hash = _hash_for(subject, message_id, body)

    existing = state_machine.load(error_hash)
    if existing is not None:
        logger.info(
            "既存 state（%s）あり、再登録スキップ hash=%s",
            existing.state,
            error_hash,
        )
        return error_hash

    user_email = _extract_form_user_email(body)
    state_machine.transition(
        error_hash,
        "received",
        original_subject=subject,
        original_message_id=message_id,
        original_body_excerpt=body[:500],
        user_email=user_email,
    )
    logger.info("新規 error 登録 hash=%s subject=%r", error_hash, subject[:60])
    return error_hash


def _process_incoming_request_auto_analyze(trigger: dict[str, Any]) -> bool:
    """v5.2: 要望（incoming_request）を Claude が分析、kyohei に確認依頼メール送信。

    エラー報告と違い、Claude はコード修正しない。返信案ドラフトだけ作る。
    kyohei さん「OK」返信で初めてユーザーに送信される。
    """
    error_hash = _register_incoming_error(trigger)  # 同じ state.json 機構を流用
    if error_hash is None:
        return False

    incident = state_machine.load(error_hash)
    if incident is None:
        return False

    if incident.state not in ("received", "analyzing"):
        logger.info(
            "状態 %s では request auto-analyze スキップ hash=%s",
            incident.state,
            error_hash,
        )
        return True

    logger.info("Claude 要望分析開始 hash=%s", error_hash)
    result = claude_runner.run_analyze_and_push(
        error_hash, type_hint="プルダウン: ご要望"
    )
    if result is None or result.state != "awaiting_approval":
        logger.warning(
            "request auto-analyze 失敗 hash=%s state=%s",
            error_hash,
            result.state if result else "None",
        )
        return False

    if not notify_review_request(result):
        logger.warning("確認依頼メール送信失敗 hash=%s", error_hash)
        return False

    logger.info(
        "✓ 要望 auto-analyze + 確認依頼完了 hash=%s session=%s",
        error_hash,
        result.claude_session_id[:8] if result.claude_session_id else "None",
    )
    return True


def _process_incoming_error_auto_analyze(trigger: dict[str, Any]) -> bool:
    """v5: incoming_error 受信 → state 登録後、即 Claude 自動解析を起動。

    v3 の挙動に近いが、kyohei「調査」ゲートはなく、自動で Claude が走る。
    """
    error_hash = _register_incoming_error(trigger)
    if error_hash is None:
        return False

    incident = state_machine.load(error_hash)
    if incident is None:
        return False

    # 既に進行済みならスキップ
    if incident.state not in ("received", "analyzing"):
        logger.info(
            "状態 %s では auto-analyze スキップ hash=%s",
            incident.state,
            error_hash,
        )
        return True

    logger.info("Claude 自動解析開始 hash=%s", error_hash)
    result = claude_runner.run_analyze_and_push(
        error_hash, type_hint="プルダウン: エラー報告"
    )
    if result is None or result.state != "awaiting_approval":
        logger.warning(
            "auto-analyze 失敗 hash=%s state=%s",
            error_hash,
            result.state if result else "None",
        )
        return False

    if not notify_review_request(result):
        logger.warning("確認依頼メール送信失敗 hash=%s", error_hash)
        return False

    logger.info("✓ auto-analyze + 確認依頼完了 hash=%s session=%s", error_hash, result.claude_session_id[:8] if result.claude_session_id else "None")
    return True


def _process_reply_to_secretary(trigger: dict[str, Any]) -> bool:
    """kyohei が確認依頼/エラー報告メールに返信 → run_dialog（キーワード判定）で対応決定。

    判定結果に応じて action を実行:
        approve → push + ユーザー返信送信
        abort   → state を error 化（中止確認ログのみ）
        invalid → 認識不能コマンド or 二重発火、state 維持（kyohei さん手動対応）

    2026-05-10: run_dialog_with_resume（Claude セッション resume 方式）廃止。
    REVISE / DEFER / AMBIGUOUS の判定は不要となり、複雑な要望は kyohei さんが
    手動でサポートメアドから返信する運用に切り替え。
    """
    # error_hash 解決: trigger に含まれていればそれを優先、なければ subject/in_reply_to から検索
    error_hash = trigger.get("error_hash")
    if not error_hash:
        incident = _find_incident_for_reply(
            trigger.get("in_reply_to", ""), trigger.get("subject", "")
        )
        if incident is None:
            logger.warning(
                "対応する incident が見つからない（reply_to_secretary）: subject=%r",
                trigger.get("subject", "")[:60],
            )
            return False
        error_hash = incident.error_hash
    else:
        incident = state_machine.load(error_hash)
        if incident is None:
            logger.warning("error_hash=%s に対応する state が無い", error_hash)
            return False

    body = trigger.get("body", "")
    incident_after, action = claude_runner.run_dialog(
        error_hash, body, report_type="error"
    )

    if incident_after is None:
        logger.warning("run_dialog 失敗 hash=%s", error_hash)
        return False

    # 二重発火 / 認識不能コマンド: state 維持、kyohei さんが手動対応
    if action == "invalid":
        logger.info(
            "二重発火回避 / 認識不能コマンド hash=%s state=%s（kyohei 手動対応待ち）",
            error_hash,
            incident_after.state,
        )
        return True

    if action == "approve":
        # push + ユーザー返信送信
        sent = claude_runner.run_push_and_send_to_user(error_hash)
        if sent and sent.state == "done":
            logger.info("✓ push + ユーザー送信完了 hash=%s", error_hash)
            return True
        logger.warning(
            "push or ユーザー送信失敗 hash=%s state=%s",
            error_hash,
            sent.state if sent else "None",
        )
        return False

    if action == "abort":
        logger.info("✓ 中止確認 hash=%s", error_hash)
        return True

    return True


def _process_trigger(trigger: dict[str, Any]) -> bool:
    """1 件のトリガーを種別に応じてディスパッチ。"""
    trigger_type = trigger.get("trigger_type", "ignore")
    trigger_id = trigger.get("id", "?")
    logger.info(
        "--- トリガー処理開始 type=%s id=%s subject=%r ---",
        trigger_type,
        trigger_id,
        trigger.get("subject", "")[:50],
    )

    try:
        # v5: incoming_error → 即 Claude 自動解析（受信 → 待機の中間ゲート無し）
        if trigger_type == "incoming_error":
            return _process_incoming_error_auto_analyze(trigger)

        # v5.2: 要望も Claude が AI ドラフト返信案を作成
        # （保管 + 自動分析 + kyohei に確認依頼メール）
        if trigger_type == "incoming_request":
            _save_request_record(trigger)
            return _process_incoming_request_auto_analyze(trigger)

        # kyohei 返信は Claude セッション resume で文意判定
        if trigger_type == "reply_to_secretary":
            return _process_reply_to_secretary(trigger)

        # v4 旧 trigger 名（後方互換、当面残す）
        if trigger_type in ("reply_investigate", "reply_approve", "reply_abort"):
            logger.info(
                "v4 旧 trigger を v5 reply_to_secretary 経路にリダイレクト: %s",
                trigger_type,
            )
            return _process_reply_to_secretary(trigger)

        logger.info("未対応 type、スキップ: %s", trigger_type)
        return True

    except Exception as e:
        logger.exception("トリガー処理例外 id=%s err=%s", trigger_id, e)
        return False


# ────────────────────────────────────────────────────────────
# HTTP ポーリング
# ────────────────────────────────────────────────────────────


def _fetch_pending(since: str = "") -> tuple[list[dict[str, Any]], str, bool]:
    """Workers から未処理トリガー一覧を取得。

    since パラメータで前回の queue_marker を渡すことで、Workers 側が
    marker 一致ならスキップ（KV LIST せず空配列返す）= KV 操作を節約。

    Returns: (triggers, marker, ok)
        ok=False は「取得に失敗した」。**「新着なし」と区別できるようにするため**に
        返す（旧実装は両方 ([], since) を返しており、呼び出し側が失敗を検知できず
        バックオフが一度も働かなかった）。
    """
    url = SupportConfig.WORKERS_ENDPOINT.rstrip("/") + PENDING_URL_PATH
    if since:
        from urllib.parse import quote

        url += f"?since={quote(since)}"
    headers = {"Authorization": f"Bearer {SupportConfig.ADMIN_TOKEN}"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning("pending 取得失敗: %s", e)
        return [], since, False
    except ValueError as e:
        # resp.json() のデコード失敗。2xx でも Cloudflare が HTML を返すことがある。
        # ここを捕まえないと main ループごと例外で落ちる = watcher が黙って死ぬ
        #（タスクは logon/boot 起動のみなので、落ちたまま朝まで気付けない）
        logger.warning("pending 応答が JSON ではありません: %s", e)
        return [], since, False

    if not isinstance(data, dict):
        logger.warning("pending 応答の形式が不正です: %r", str(data)[:120])
        return [], since, False
    triggers = data.get("triggers") or []
    if not isinstance(triggers, list):
        logger.warning("triggers が配列ではありません: %r", str(triggers)[:120])
        return [], since, False
    return triggers, data.get("marker", ""), True


def _mark_processed(trigger_id: str) -> bool:
    """Workers にトリガー処理完了を通知（pending マーカー削除）。"""
    url = (
        SupportConfig.WORKERS_ENDPOINT.rstrip("/")
        + PROCESSED_URL_PATH_TEMPLATE.format(trigger_id=trigger_id)
    )
    headers = {"Authorization": f"Bearer {SupportConfig.ADMIN_TOKEN}"}
    try:
        resp = requests.post(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.warning("processed マーク失敗 id=%s: %s", trigger_id, e)
        return False


# ────────────────────────────────────────────────────────────
# メインループ
# ────────────────────────────────────────────────────────────


def _setup_logging() -> None:
    SupportConfig.INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    log_path = SupportConfig.INCOMING_DIR / "_http_watcher.log"
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
    logger.info("==== HTTP watcher 起動 ====")

    if not SupportConfig.ADMIN_TOKEN:
        logger.error("SUPPORT_ADMIN_TOKEN が未設定です。.env を確認してください。")
        return 2

    signal.signal(signal.SIGINT, _request_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _request_shutdown)

    # 初期値 "__init__" は marker（ISO 8601 timestamp）と必ず不一致 →
    # 起動直後 1 回は Workers 側で LIST 経路を通り、既存 pending を回収できる。
    last_marker = "__init__"
    consecutive_failures = 0
    # trigger_id → 失敗回数。上限に達したら諦めて processed にする（下記参照）
    failure_counts: dict[str, int] = {}

    while not _shutdown_requested:
        sleep_seconds = POLL_INTERVAL_SECONDS
        try:
            triggers, marker, fetch_ok = _fetch_pending(since=last_marker)

            if not fetch_ok:
                consecutive_failures += 1
                if consecutive_failures > FAILURES_BEFORE_BACKOFF:
                    sleep_seconds = BACKOFF_SECONDS
                    logger.warning(
                        "取得失敗が %d 回続いています。%d 秒間隔に落とします",
                        consecutive_failures,
                        sleep_seconds,
                    )
            else:
                consecutive_failures = 0

                # ⚠️ last_marker は「全件片付いたときだけ」進める。
                # 旧実装は取得直後に無条件で last_marker = marker としていたため、
                # 処理に失敗した（= processed を送っていない）トリガーが残っていても
                # 次回 poll では since == marker となり Workers が cheap path で
                # 0 件を返す。結果、**失敗したトリガーは次の受信が来るまで
                # 永久に取り残されていた**。
                all_done = True
                if triggers:
                    logger.info("検知トリガー数: %d", len(triggers))
                    for trig in triggers:
                        tid = trig.get("id", "")
                        if not tid:
                            continue
                        ok = _process_trigger(trig)
                        if ok and _mark_processed(tid):
                            failure_counts.pop(tid, None)
                            continue

                        # 失敗した。次の poll で再取得されるよう marker は進めない。
                        # ただし永久リトライは危険（1 件ごとに Claude を起動するため、
                        # 直らない trigger があると 5 秒おきに Claude を叩き続ける）。
                        # 上限回数で諦めて processed にし、state.json は手動対応用に残す。
                        n = failure_counts.get(tid, 0) + 1
                        failure_counts[tid] = n
                        if n >= MAX_TRIGGER_ATTEMPTS:
                            logger.error(
                                "トリガー id=%s が %d 回失敗したため諦めます"
                                "（processed 化。state.json を見て手動対応してください）",
                                tid,
                                n,
                            )
                            _mark_processed(tid)
                            failure_counts.pop(tid, None)
                        else:
                            logger.warning(
                                "トリガー id=%s 失敗（%d/%d）。次回 poll で再試行します",
                                tid,
                                n,
                                MAX_TRIGGER_ATTEMPTS,
                            )
                            all_done = False

                if all_done:
                    last_marker = marker

        except Exception:
            # ここで飲まないと watcher プロセスが落ちる。
            # スケジュールタスクは logon / boot 起動のみなので、
            # 落ちたら次のログオンまで復帰しない。ループは絶対に維持する。
            logger.exception("poll ループで予期せぬ例外（継続します）")
            consecutive_failures += 1

        # シャットダウン要求対応のため細かく寝る
        for _ in range(sleep_seconds):
            if _shutdown_requested:
                break
            time.sleep(1)

    logger.info("==== HTTP watcher 終了 ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
