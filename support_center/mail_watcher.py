"""
IMAP ポーリング: メール対話フローの監視（A 案 = Discord Bot 流）

【動作モード】
1. INBOX を見て、新着の【ClipGift エラー報告】メールを state 登録（Phase: received）
   ※ Claude は起動しない、状態保存のみ
2. Sent フォルダを見て、kyohei さんの返信を検知 → Phase 遷移
   - Re: 【ClipGift エラー報告】... → Phase: analyzing（Claude 解析開始）
   - Re: 【ClipGift 確認依頼】<hash> → Phase: executing（push + ユーザー送信）

件名 + 本文（あれば In-Reply-To）で元エラーと紐付ける。
"""

from __future__ import annotations

import email
import hashlib
import imaplib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path
from typing import Optional

from support_center import state_machine
from support_center.config import SupportConfig

logger = logging.getLogger(__name__)


# Gmail IMAP の Sent フォルダ候補（手動指定する場合のフォールバック用）
GMAIL_SENT_FOLDERS = (
    '"[Gmail]/Sent Mail"',
    "INBOX.Sent",
    "Sent",
)


# ────────────────────────────────────────────────────────────
# watcher 起動時刻（メール処理の時系列境界）
# ────────────────────────────────────────────────────────────

_BOOT_TIME_FILE = "_watcher_boot_time.txt"


def get_boot_time() -> datetime:
    """watcher の起動時刻（処理する/しないの境界）を取得 or 初期化。

    - 起動時刻より新しいメールのみ処理対象
    - これにより、起動時に存在する過去メールは自動的に「無視」される
    - kyohei さんが手動でこのファイルを削除して watcher を再起動すれば、
      その時点からの新着のみ処理対象になる
    """
    SupportConfig.INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    boot_path = SupportConfig.INCOMING_DIR / _BOOT_TIME_FILE
    if not boot_path.exists():
        now = datetime.now(timezone.utc)
        try:
            boot_path.write_text(now.isoformat(timespec="seconds"), encoding="utf-8")
        except OSError as e:
            logger.warning("boot_time 書き込み失敗: %s", e)
        return now
    try:
        text = boot_path.read_text(encoding="utf-8").strip()
        return datetime.fromisoformat(text)
    except (OSError, ValueError) as e:
        logger.warning("boot_time 読み込み失敗（再生成）: %s", e)
        now = datetime.now(timezone.utc)
        try:
            boot_path.write_text(now.isoformat(timespec="seconds"), encoding="utf-8")
        except OSError:
            pass
        return now


def _is_message_older_than_boot(msg: email.message.Message, boot_time: datetime) -> bool:
    """メールの Date ヘッダーが boot_time より古ければ True。"""
    date_str = msg.get("Date")
    if not date_str:
        return False  # Date 不明なら処理対象
    try:
        from email.utils import parsedate_to_datetime
        msg_date = parsedate_to_datetime(date_str)
        if msg_date.tzinfo is None:
            # naive datetime → UTC として扱う
            msg_date = msg_date.replace(tzinfo=timezone.utc)
        return msg_date < boot_time
    except (TypeError, ValueError):
        return False


@dataclass
class TriggerEvent:
    """検知されたトリガー（Phase 遷移指示）。"""

    phase: str               # "analyzing" / "dialog"
    error_hash: str
    sent_message_id: str     # 返信メールの Message-ID（INBOX 起動時は空）
    reply_subject: str
    reply_body: str          # kyohei 返信本文 or 元エラー本文
    in_reply_to: str
    detected_at: str
    report_type: str = "error"   # "error" / "request"


def fetch_triggers() -> list[TriggerEvent]:
    """kyohei さんの返信を Sent フォルダから検知してトリガーを返す。

    1. 新着の【ClipGift エラー報告】メールを INBOX から拾って state 登録（Phase: received）
    2. Sent フォルダから件名 Re: 【ClipGift エラー報告】 → Phase: analyzing
    3. Sent フォルダから件名 Re: 【ClipGift 確認依頼】<hash> → Phase: executing

    すでに対応する state ファイルが進行済みフェーズの場合はスキップ。
    """
    if not SupportConfig.MAIL_USER or not SupportConfig.MAIL_APP_PASSWORD:
        logger.error("IMAP 設定が未完了です。.env を確認してください。")
        return []

    SupportConfig.INCOMING_DIR.mkdir(parents=True, exist_ok=True)

    try:
        mail = imaplib.IMAP4_SSL(
            SupportConfig.IMAP_HOST,
            SupportConfig.IMAP_PORT,
        )
        mail.login(SupportConfig.MAIL_USER, SupportConfig.MAIL_APP_PASSWORD)
    except (imaplib.IMAP4.error, OSError) as e:
        logger.error("IMAP 接続/ログイン失敗: %s", e)
        return []

    triggers: list[TriggerEvent] = []
    try:
        # Step 1: INBOX で新着エラー報告 / ご要望を state 登録 + 自動 trigger 生成
        triggers.extend(_ingest_new_error_reports(mail))

        # Step 2: Sent フォルダで kyohei 返信を検知（壁打ち対話用）
        sent_folder = _find_sent_folder(mail)
        if sent_folder is None:
            logger.warning("Sent フォルダが見つかりません")
        else:
            triggers.extend(_scan_sent_folder(mail, sent_folder))
    finally:
        try:
            mail.close()
            mail.logout()
        except Exception:
            pass

    return triggers


# ────────────────────────────────────────────────────────────
# INBOX: 新着エラー報告メールを state 登録（Phase: received）
# ────────────────────────────────────────────────────────────


def _ingest_new_error_reports(mail: imaplib.IMAP4_SSL) -> list[TriggerEvent]:
    """新着の【ClipGift エラー報告】/ ご要望を state 登録し、エラー報告は自動 analyzing トリガー返却。"""
    try:
        mail.select("INBOX")
    except imaplib.IMAP4.error as e:
        logger.warning("INBOX 選択失敗: %s", e)
        return []

    status, data = mail.search(None, "ALL")
    if status != "OK":
        return []

    ids = data[0].split() if data and data[0] else []
    triggers: list[TriggerEvent] = []
    # 直近 50 件
    for msg_id in ids[-50:]:
        try:
            t = _try_register_inbox_message(mail, msg_id)
            if t is not None:
                triggers.append(t)
        except Exception as e:
            logger.warning("INBOX エントリ処理失敗 (id=%s): %s", msg_id, e)
    return triggers


def _try_register_inbox_message(
    mail: imaplib.IMAP4_SSL, msg_id: bytes
) -> Optional[TriggerEvent]:
    """1 件の INBOX メッセージを判定して state 登録 + エラー報告は trigger 返却。"""
    msg = _fetch_message(mail, msg_id)
    if msg is None:
        return None

    # watcher 起動時刻より古いメールはスキップ（過去メールを再処理しない）
    boot_time = get_boot_time()
    if _is_message_older_than_boot(msg, boot_time):
        return None

    subject = _decode_header(msg.get("Subject", ""))
    subject_stripped = subject.strip()

    # 自社送信メール（確認依頼・修正案レビュー依頼）は登録しない
    # ＝ Claude が kyohei さん宛に送ったメール（INBOX に入る）
    SELF_SENT_MARKERS = (
        "【ClipGift 修正案レビュー依頼】",
        "【ClipGift 確認依頼】",
    )
    for marker in SELF_SENT_MARKERS:
        if marker in subject_stripped:
            return None

    # 件名「で始まる」で厳格判定（部分一致だと自社送信メールも誤検知するため）
    if subject_stripped.startswith(SupportConfig.SUBJECT_FILTER):
        report_type = "error"
    elif subject_stripped.startswith(SupportConfig.SUBJECT_FILTER_REQUEST):
        report_type = "request"
    else:
        return None  # 関係ないメール

    # Re: / RE: / Fwd: / FW: 等の返信・転送は新規ではない
    if re.match(r"^\s*(Re|RE|Fwd|Fw|FW):\s*", subject):
        return None

    # ClipGift サポート（Resend）以外からの送信元なら無視
    from_addr = _decode_header(msg.get("From", ""))
    if "onboarding@resend.dev" not in from_addr and "support@clipgift" not in from_addr:
        return None

    message_id = (msg.get("Message-ID") or "").strip("<>").strip()

    # Message-ID 単位で処理済み判定（state ファイル削除後でも再処理されない）
    if _inbox_already_processed(message_id):
        return None

    body = _extract_text_body(msg)
    error_hash = _hash_for(subject, message_id, body)

    # 既に state ファイルがある場合の判定:
    # - state が received / analyzing のまま → 前回 処理が中断 → 再 trigger
    # - state が awaiting_approval / done / error → スキップ（kyohei レビュー中 or 完了 or 失敗確定）
    existing = state_machine.load(error_hash)
    if existing is not None:
        if report_type == "error" and existing.state in ("received", "analyzing"):
            logger.info(
                "未処理 state（%s）を再 analyzing trigger: hash=%s",
                existing.state,
                error_hash,
            )
            return TriggerEvent(
                phase="analyzing",
                error_hash=error_hash,
                sent_message_id="",
                reply_subject="",
                reply_body="（再 trigger: 前回処理が中断）",
                in_reply_to="",
                detected_at=_now_iso(),
                report_type="error",
            )
        return None

    # ユーザーがフォームで入力した返信先メアドを最優先で本文から抽出
    user_email = _extract_form_user_email(body)
    if not user_email:
        user_email = _extract_email(msg.get("Reply-To") or msg.get("From") or "")

    # state 登録（received）
    state_machine.transition(
        error_hash,
        "received",
        original_subject=subject,
        original_message_id=message_id,
        original_body_excerpt=body[:500],
        user_email=user_email,
    )

    # 元メール .eml を保存
    try:
        eml_path = SupportConfig.INCOMING_DIR / f"{error_hash}.original.eml"
        status, raw_data = mail.fetch(msg_id, "(RFC822)")
        if status == "OK" and raw_data and raw_data[0]:
            raw_bytes = raw_data[0][1] if isinstance(raw_data[0], tuple) else b""
            if raw_bytes:
                eml_path.write_bytes(raw_bytes)
    except Exception as e:
        logger.warning("元メール .eml 保存失敗 hash=%s: %s", error_hash, e)

    logger.info(
        "新規%s 登録: hash=%s subject=%r",
        report_type,
        error_hash,
        subject,
    )

    # 重複処理防止: この Message-ID を「INBOX 処理済み」として記録
    # → state ファイル削除しても再処理されない
    mark_inbox_processed(message_id)

    # エラー報告のみ → 即 analyzing トリガー（Claude 自動起動）
    # ご要望は trigger 返さない（kyohei が直接読んで返信するまで待ち）
    if report_type == "error":
        return TriggerEvent(
            phase="analyzing",
            error_hash=error_hash,
            sent_message_id="",
            reply_subject="",
            reply_body="（自動起動: ユーザー報告 → Claude 解析開始）",
            in_reply_to="",
            detected_at=_now_iso(),
            report_type="error",
        )
    return None


# ────────────────────────────────────────────────────────────
# Sent フォルダ: kyohei さんの返信検知
# ────────────────────────────────────────────────────────────


def _find_sent_folder(mail: imaplib.IMAP4_SSL) -> Optional[str]:
    """利用可能な Sent フォルダ名を返す（動的検出）。"""
    # Step 1: LIST でフォルダ一覧取得 → \Sent フラグ or "Sent"/"送信" を含むものを探す
    try:
        status, folders = mail.list()
    except imaplib.IMAP4.error:
        folders = None
        status = "NO"

    if status == "OK" and folders:
        for raw in folders:
            if not raw:
                continue
            try:
                line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            except Exception:
                continue
            lower = line.lower()
            # \Sent フラグ持ち or 名前に Sent/送信/Sent Mail/送信済み を含む
            has_sent_flag = "\\sent" in lower
            has_sent_keyword = (
                "送信済" in line or "送信" in line or "/sent" in lower or '"sent"' in lower
            )
            if not (has_sent_flag or has_sent_keyword):
                continue
            # フォルダ名は最後の引用符付き文字列
            m = re.search(r'"([^"]+)"\s*$', line)
            if not m:
                continue
            folder_name = m.group(1)
            quoted = f'"{folder_name}"'
            try:
                s, _ = mail.select(quoted, readonly=False)
                if s == "OK":
                    logger.info("Sent フォルダ検出: %s", folder_name)
                    return quoted
            except imaplib.IMAP4.error:
                continue

    # Step 2: フォールバック候補で順次試す
    for candidate in GMAIL_SENT_FOLDERS:
        try:
            status, _ = mail.select(candidate, readonly=False)
            if status == "OK":
                logger.info("Sent フォルダ検出（フォールバック）: %s", candidate)
                return candidate
        except imaplib.IMAP4.error:
            continue
    return None


def _scan_sent_folder(
    mail: imaplib.IMAP4_SSL, sent_folder: str
) -> list[TriggerEvent]:
    """Sent フォルダから新規返信を検知してトリガーを返す。"""
    # 全件 → 直近 50 件
    status, data = mail.search(None, "ALL")
    if status != "OK":
        return []

    ids = data[0].split() if data and data[0] else []
    triggers: list[TriggerEvent] = []
    for msg_id in ids[-50:]:
        try:
            trig = _classify_sent_message(mail, msg_id)
            if trig is not None:
                triggers.append(trig)
        except Exception as e:
            logger.warning("Sent エントリ処理失敗 (id=%s): %s", msg_id, e)
    return triggers


def _classify_sent_message(
    mail: imaplib.IMAP4_SSL, msg_id: bytes
) -> Optional[TriggerEvent]:
    """送信済みメッセージを判定。kyohei さん返信なら dialog トリガーを返す。

    重複防止のため、すでに同じ Sent Message-ID で処理済の state があればスキップ。
    """
    msg = _fetch_message(mail, msg_id)
    if msg is None:
        return None

    subject = _decode_header(msg.get("Subject", ""))
    in_reply_to = (msg.get("In-Reply-To") or "").strip("<>").strip()
    sent_msg_id = (msg.get("Message-ID") or "").strip("<>").strip()
    body = _extract_text_body(msg)
    detected_at = _now_iso()

    # 重複処理防止用マーカー（処理済み Sent Message-ID リストをローカルに持つ）
    if sent_msg_id and _sent_already_processed(sent_msg_id):
        return None

    # === ご要望への kyohei 返信（Re: 【ClipGift ご要望】...） ===
    if "Re:" in subject and SupportConfig.SUBJECT_FILTER_REQUEST in subject:
        incident = _find_incident_by_reply(in_reply_to, subject)
        if incident is None:
            return None
        return TriggerEvent(
            phase="dialog",
            error_hash=incident.error_hash,
            sent_message_id=sent_msg_id,
            reply_subject=subject,
            reply_body=body,
            in_reply_to=in_reply_to,
            detected_at=detected_at,
            report_type="request",
        )

    # === 修正案レビュー依頼への kyohei 返信（hash で紐付け確実） ===
    review_match = re.search(
        r"【ClipGift 修正案レビュー依頼】([0-9a-f]{12})", subject
    )
    if "Re:" in subject and review_match:
        error_hash = review_match.group(1)
        incident = state_machine.load(error_hash)
        if incident is None:
            return None
        return TriggerEvent(
            phase="dialog",
            error_hash=error_hash,
            sent_message_id=sent_msg_id,
            reply_subject=subject,
            reply_body=body,
            in_reply_to=in_reply_to,
            detected_at=detected_at,
            report_type="error",
        )

    # === 確認依頼（旧名）への kyohei 返信（後方互換） ===
    confirm_match = re.search(r"【ClipGift 確認依頼】([0-9a-f]{12})", subject)
    if "Re:" in subject and confirm_match:
        error_hash = confirm_match.group(1)
        incident = state_machine.load(error_hash)
        if incident is None:
            return None
        return TriggerEvent(
            phase="dialog",
            error_hash=error_hash,
            sent_message_id=sent_msg_id,
            reply_subject=subject,
            reply_body=body,
            in_reply_to=in_reply_to,
            detected_at=detected_at,
            report_type="error",
        )

    # === 元のエラー報告への kyohei 返信（受信箱で「対応して」と直接返信した場合） ===
    # ※ 件名は「Re: 【ClipGift エラー報告】v...」、自社送信件名は除外
    if (
        "Re:" in subject
        and subject.strip().split(":", 1)[-1].strip().startswith(SupportConfig.SUBJECT_FILTER)
        and "修正案レビュー依頼" not in subject
        and "確認依頼" not in subject
    ):
        incident = _find_incident_by_reply(in_reply_to, subject)
        if incident is None:
            return None
        return TriggerEvent(
            phase="dialog",
            error_hash=incident.error_hash,
            sent_message_id=sent_msg_id,
            reply_subject=subject,
            reply_body=body,
            in_reply_to=in_reply_to,
            detected_at=detected_at,
            report_type="error",
        )

    return None


# ────────────────────────────────────────────────────────────
# 重複処理防止（Sent / INBOX 両方の Message-ID を記録）
# ────────────────────────────────────────────────────────────

_PROCESSED_LOG = "_processed_sent_ids.txt"
_PROCESSED_INBOX_LOG = "_processed_inbox_ids.txt"


def _sent_already_processed(sent_msg_id: str) -> bool:
    log_path = SupportConfig.INCOMING_DIR / _PROCESSED_LOG
    if not log_path.exists():
        return False
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            return any(line.strip() == sent_msg_id for line in f)
    except OSError:
        return False


def mark_sent_processed(sent_msg_id: str) -> None:
    if not sent_msg_id:
        return
    SupportConfig.INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    log_path = SupportConfig.INCOMING_DIR / _PROCESSED_LOG
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(sent_msg_id + "\n")
    except OSError as e:
        logger.warning("processed_sent_ids 書き込み失敗: %s", e)


def _inbox_already_processed(message_id: str) -> bool:
    """INBOX の Message-ID が処理済みリストに含まれているか。"""
    if not message_id:
        return False
    log_path = SupportConfig.INCOMING_DIR / _PROCESSED_INBOX_LOG
    if not log_path.exists():
        return False
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            return any(line.strip() == message_id for line in f)
    except OSError:
        return False


def mark_inbox_processed(message_id: str) -> None:
    """INBOX の Message-ID を処理済みとして記録。state ファイルとは独立して管理。"""
    if not message_id:
        return
    SupportConfig.INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    log_path = SupportConfig.INCOMING_DIR / _PROCESSED_INBOX_LOG
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(message_id + "\n")
    except OSError as e:
        logger.warning("processed_inbox_ids 書き込み失敗: %s", e)


def initialize_processed_ids_if_missing() -> None:
    """初回起動時に Sent 既存メールを処理済みとして記録。

    INBOX 側は廃止: 代わりに boot_time 判定で「起動時刻より古いメールは無視」する。
    これにより、kyohei さんが完全リセットしても新着は確実に処理される。
    （Sent は kyohei 返信検知用、INBOX とは性質が異なるため初期化を維持）
    """
    sent_log_path = SupportConfig.INCOMING_DIR / _PROCESSED_LOG
    if sent_log_path.exists():
        return  # 既存、スキップ

    if not SupportConfig.MAIL_USER or not SupportConfig.MAIL_APP_PASSWORD:
        logger.warning("IMAP 設定不足で初期化スキップ")
        return

    SupportConfig.INCOMING_DIR.mkdir(parents=True, exist_ok=True)

    try:
        mail = imaplib.IMAP4_SSL(
            SupportConfig.IMAP_HOST,
            SupportConfig.IMAP_PORT,
        )
        mail.login(SupportConfig.MAIL_USER, SupportConfig.MAIL_APP_PASSWORD)
    except (imaplib.IMAP4.error, OSError) as e:
        logger.warning("初期化用 IMAP 接続失敗: %s", e)
        return

    try:
        sent_folder = _find_sent_folder(mail)
        if sent_folder is None:
            return
        status, data = mail.search(None, "ALL")
        if status != "OK":
            return
        ids = data[0].split() if data and data[0] else []
        recorded = 0
        for msg_id in ids[-100:]:
            try:
                msg = _fetch_message(mail, msg_id)
                if msg is None:
                    continue
                sent_msg_id = (msg.get("Message-ID") or "").strip("<>").strip()
                if sent_msg_id:
                    mark_sent_processed(sent_msg_id)
                    recorded += 1
            except Exception:
                continue
        logger.info("Sent 履歴初期化: %d 件記録", recorded)
    finally:
        try:
            mail.close()
            mail.logout()
        except Exception:
            pass


def _find_incident_by_reply(
    in_reply_to: str, subject: str
) -> Optional[state_machine.IncidentState]:
    """返信メールから元エラー報告の state を見つける。"""
    # In-Reply-To が一致する state を全走査
    if in_reply_to:
        for path in SupportConfig.INCOMING_DIR.glob("*.state.json"):
            try:
                inc = state_machine.load(path.stem.replace(".state", ""))
                if inc and inc.original_message_id == in_reply_to:
                    return inc
            except Exception:
                continue

    # 件名からも照合（"Re: " を除去した残りが original_subject と一致）
    cleaned = re.sub(r"^(Re|RE|Fwd|Fw|FW):\s*", "", subject).strip()
    for path in SupportConfig.INCOMING_DIR.glob("*.state.json"):
        try:
            inc = state_machine.load(path.stem.replace(".state", ""))
            if inc and inc.original_subject.strip() == cleaned:
                return inc
        except Exception:
            continue
    return None


# ────────────────────────────────────────────────────────────
# 共通ヘルパー
# ────────────────────────────────────────────────────────────


def _fetch_message(
    mail: imaplib.IMAP4_SSL, msg_id: bytes
) -> Optional[email.message.Message]:
    status, data = mail.fetch(msg_id, "(RFC822)")
    if status != "OK" or not data or not data[0]:
        return None
    raw_bytes = data[0][1] if isinstance(data[0], tuple) else b""
    if not raw_bytes:
        return None
    return email.message_from_bytes(raw_bytes)


def _decode_header(raw: str) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    decoded: list[str] = []
    for value, charset in parts:
        if isinstance(value, bytes):
            try:
                decoded.append(value.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                decoded.append(value.decode("utf-8", errors="replace"))
        else:
            decoded.append(value)
    return "".join(decoded)


def _extract_text_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace")
                except LookupError:
                    return payload.decode("utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True) or b""
    charset = msg.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _hash_for(subject: str, message_id: str, body: str) -> str:
    return hashlib.sha256(
        f"{subject}|{message_id}|{body[:300]}".encode("utf-8")
    ).hexdigest()[:12]


def _extract_email(raw: str) -> str:
    if not raw:
        return ""
    m = re.search(r"[\w\.\-+]+@[\w\.\-]+", raw)
    return m.group(0) if m else ""


def _extract_form_user_email(body: str) -> str:
    """エラー報告メール本文から「ユーザー返信先: xxx@xxx」のメアドを抽出。

    Workers が送信するメールには `- ユーザー返信先: <user-email>` の行がある。
    これがユーザーがアプリのフォームで入力した実際の連絡先。
    """
    if not body:
        return ""
    # 全角コロン / 半角コロン両方対応、行頭の "- " は任意
    m = re.search(
        r"ユーザー返信先[:：]\s*([\w\.\-+]+@[\w\.\-]+)",
        body,
    )
    if m:
        return m.group(1).strip()
    return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
