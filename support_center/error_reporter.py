"""
アプリ側エラー報告クライアント（HTTPS POST）

エンドユーザーの PC で動く。SMTP 認証情報は持たない（漏洩リスクのため）。
Cloudflare Workers の `/support/report` に JSON を POST するのみ。
"""

from __future__ import annotations

import json
import logging
import platform
import sys
from typing import Any

import requests

from support_center.config import SUPPORT_REPORT_ENDPOINT
from support_center.pii_masker import license_tail, mask_log

logger = logging.getLogger(__name__)

# 送信タイムアウト（接続 + 読込）
_TIMEOUT_SECONDS = (5, 10)

# ユーザーログとして送る最大行数（過剰な情報送信を防ぐ）
_MAX_LOG_LINES = 200


class ErrorReportError(Exception):
    """エラー報告送信に関する例外（Workers 不通 / 4xx / 5xx）。"""


def build_payload(
    *,
    app_version: str,
    license_key: str,
    user_email: str,
    user_comment: str,
    error_log: str,
    extra_env: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    report_type: str = "error",
) -> dict[str, Any]:
    """送信ペイロードを組み立てる（マスキング適用済）。

    attachments: [{filename, content_base64, content_type}, ...] 形式（最大 3 枚 / 各 5MB）
    """

    # ログ行数を制限（最後の N 行を採用）
    lines = error_log.splitlines()
    if len(lines) > _MAX_LOG_LINES:
        truncated = lines[-_MAX_LOG_LINES:]
        truncated.insert(0, f"...（先頭 {len(lines) - _MAX_LOG_LINES} 行は省略）")
        log_for_send = "\n".join(truncated)
    else:
        log_for_send = "\n".join(lines)

    masked_log = mask_log(log_for_send)
    masked_comment = mask_log(user_comment)

    env_info: dict[str, Any] = {
        "python": ".".join(map(str, sys.version_info[:3])),
        "platform": platform.platform(),
    }
    if extra_env:
        env_info.update(extra_env)

    payload: dict[str, Any] = {
        "app_version": app_version,
        "os": _short_os_name(),
        "license_tail": license_tail(license_key),
        "user_email": user_email or "",  # 空なら返信なし
        "user_comment": masked_comment,
        "error_log": masked_log,
        "env_info": env_info,
        "report_type": report_type,
    }
    if attachments:
        payload["attachments"] = attachments
    return payload


def send_report(payload: dict[str, Any]) -> dict[str, Any]:
    """Workers `/support/report` に POST。

    成功時はサーバー応答 JSON を返す。失敗時は ErrorReportError。
    """
    headers = {"Content-Type": "application/json; charset=utf-8"}

    try:
        response = requests.post(
            SUPPORT_REPORT_ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        logger.warning("エラー報告 POST 失敗（接続エラー）: %s", e)
        raise ErrorReportError(f"Workers への接続に失敗: {e}") from e

    if response.status_code >= 400:
        logger.warning(
            "エラー報告 POST 失敗（HTTP %d）: %s",
            response.status_code,
            response.text[:200],
        )
        raise ErrorReportError(
            f"Workers が {response.status_code} を返しました: {response.text[:200]}"
        )

    try:
        return response.json()
    except ValueError:
        logger.warning("Workers 応答が JSON でない: %s", response.text[:200])
        return {"status": "ok", "raw": response.text[:200]}


def report_error(
    *,
    app_version: str,
    license_key: str,
    user_email: str,
    user_comment: str,
    error_log: str,
    extra_env: dict[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    report_type: str = "error",
) -> dict[str, Any]:
    """エラー報告のメインエントリ。

    UI / Flask ルートからはこの関数を呼ぶだけで良い。
    """
    payload = build_payload(
        app_version=app_version,
        license_key=license_key,
        user_email=user_email,
        user_comment=user_comment,
        error_log=error_log,
        extra_env=extra_env,
        attachments=attachments,
        report_type=report_type,
    )
    logger.info(
        "エラー報告を送信します: ver=%s, license=****-%s, mail=%s, 添付=%d 枚",
        app_version,
        payload["license_tail"] or "なし",
        "あり" if payload["user_email"] else "なし",
        len(attachments or []),
    )
    return send_report(payload)


def _short_os_name() -> str:
    """OS バージョンの簡易表記を返す。"""
    sys_name = platform.system()
    if sys_name == "Windows":
        release = platform.release()  # "10" / "11" 等
        return f"Windows {release}"
    if sys_name == "Darwin":
        return f"macOS {platform.mac_ver()[0]}"
    return platform.platform()
