"""
kyohei さん宛通知メール送信（Workers /support/notify 経由）

A 案では 1 通のメールに「修正サマリ」「ユーザー返信案」を両方含めて、
kyohei さんが OK 1 回で全フェーズ承認できるようにする。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from support_center import state_machine
from support_center.config import SupportConfig

logger = logging.getLogger(__name__)


def _extract_section(text: str, header: str, max_len: int = 400) -> str:
    """`## {header}` セクションの本文を抽出。次の `## ` か EOF まで。
    Claude が CLAUDE.md の指示通り `## 原因` `## 修正サマリ` `## ユーザー返信案`
    形式で repair_summary を書いた前提。見つからなければ空文字。"""
    if not text:
        return ""
    pattern = re.compile(
        r"^##\s+" + re.escape(header) + r"\s*\n(.+?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        return ""
    body = m.group(1).strip()
    if len(body) > max_len:
        body = body[:max_len].rstrip() + "..."
    return body


def _summarize_error_msg(original_body: str, max_len: int = 240) -> str:
    """ユーザー報告本文からエラーの核心 1-3 行だけ抽出。
    'エラー:' 'Error' 'Exception' を含む行を優先、なければ先頭から非空行を採用。"""
    if not original_body:
        return "（エラーメッセージなし）"
    lines = [l.strip() for l in original_body.splitlines() if l.strip()]
    key = [
        l for l in lines
        if l.startswith("エラー:")
        or "Error" in l
        or "Exception" in l
        or "unavailable" in l.lower()
        or "failed" in l.lower()
    ]
    picked = (key or lines)[:3]
    msg = " / ".join(picked)
    if len(msg) > max_len:
        msg = msg[:max_len].rstrip() + "..."
    return msg


def notify_review_request(
    incident: state_machine.IncidentState,
) -> bool:
    """修正案 + ユーザー返信案を 1 通の確認依頼メールで kyohei さんに送る。

    件名: 【ClipGift 確認依頼】<error_hash> - <元件名>
    （kyohei さんがこのメールに「OK」と返信するとフェーズ 3 へ進む）
    """
    endpoint = SupportConfig.WORKERS_ENDPOINT.rstrip("/") + "/support/notify"
    if not SupportConfig.ADMIN_TOKEN:
        logger.error("SUPPORT_ADMIN_TOKEN が未設定のため通知送信できません")
        return False

    repair_raw = incident.repair_summary or ""
    user_email = incident.user_email or "（なし、ユーザー送信スキップ）"

    # kyohei さん向けはシンプル 4 セクション:
    #   エラーメッセージ → 原因 → 修正案要約 → 内容 OK?
    # 詳細情報（生ログ・エラーコード等）は秘書（watch_support_idle.py）が
    # 元の受信メールを保持しているのでそちらで参照可能。
    error_msg = _summarize_error_msg(incident.original_body_excerpt, 240)
    cause = _extract_section(repair_raw, "原因", 300) or "（解析中／未特定）"
    fix = _extract_section(repair_raw, "修正サマリ", 300) or "（解析中／未特定）"

    combined_body = (
        "─────────────────────────\n"
        "【エラーメッセージ】\n"
        "─────────────────────────\n"
        f"{error_msg}\n"
        "\n"
        "─────────────────────────\n"
        "【原因】\n"
        "─────────────────────────\n"
        f"{cause}\n"
        "\n"
        "─────────────────────────\n"
        "【修正案】\n"
        "─────────────────────────\n"
        f"{fix}\n"
        "\n"
        "─────────────────────────\n"
        "内容 OK？\n"
        "─────────────────────────\n"
        "このメールに「OK」と返信 → 自動で push + ユーザー返信送信。\n"
        "返信案を直したい場合は OK の後ろに修正内容を続けて返信。\n"
        "（例: OK ただし最後の挨拶を 〜〜 に変えて）\n"
        "\n"
        f"返信先ユーザー: {user_email}\n"
        f"error_hash:    {incident.error_hash}\n"
    )

    # 件名: 【ClipGift 確認依頼】<hash> - <元件名から短縮>
    short_subject = (incident.original_subject or "")[:80]
    payload: dict[str, Any] = {
        "error_hash": f"{incident.error_hash} - {short_subject}",
        "user_email": user_email,
        # 【ユーザー報告（要約）】を Workers が prepend するので、ここは空相当で重複回避
        "original_error_summary": "（詳細は本文【エラーメッセージ】参照）",
        "repair_summary": combined_body,
    }

    try:
        resp = requests.post(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {SupportConfig.ADMIN_TOKEN}",
            },
            timeout=(5, 15),
        )
    except requests.RequestException as e:
        logger.error("確認依頼メール送信失敗: %s", e)
        return False

    if resp.status_code >= 400:
        logger.error("確認依頼メール HTTP %d: %s", resp.status_code, resp.text[:300])
        return False

    logger.info("確認依頼メール送信成功 hash=%s", incident.error_hash)
    return True


def notify_kyohei_legacy(
    report,
    repair_summary: str,
    user_email: str = "",
) -> bool:
    """既存呼び出しとの互換用（旧 INBOX 監視フローの名残）。"""
    endpoint = SupportConfig.WORKERS_ENDPOINT.rstrip("/") + "/support/notify"
    if not SupportConfig.ADMIN_TOKEN:
        logger.error("SUPPORT_ADMIN_TOKEN 未設定")
        return False
    payload = {
        "error_hash": getattr(report, "error_hash", ""),
        "user_email": user_email,
        "original_error_summary": _short(getattr(report, "raw_body", ""), 600),
        "repair_summary": repair_summary or "（サマリなし）",
    }
    try:
        resp = requests.post(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {SupportConfig.ADMIN_TOKEN}",
            },
            timeout=(5, 15),
        )
    except requests.RequestException as e:
        logger.error("legacy 通知送信失敗: %s", e)
        return False
    return resp.status_code < 400


def notify_invalid_command(
    incident: state_machine.IncidentState,
    kyohei_reply: str,
) -> bool:
    """kyohei の返信が認識できないコマンドだった時の通知メール。

    件名: 【ClipGift コマンド認識エラー】<hash>
    本文: 「認識されませんでした、有効なコマンド一覧」
    """
    endpoint = SupportConfig.WORKERS_ENDPOINT.rstrip("/") + "/support/notify"
    if not SupportConfig.ADMIN_TOKEN:
        logger.error("SUPPORT_ADMIN_TOKEN 未設定")
        return False

    short_subject = (incident.original_subject or "")[:60]

    body = (
        "ご返信を受け取りましたが、認識できないコマンドでした。\n"
        "（認識されませんでした。無効なコマンドです）\n"
        "\n"
        "─────────────────────────\n"
        "【受け取った返信本文（冒頭）】\n"
        "─────────────────────────\n"
        f"{(kyohei_reply or '').strip()[:400]}\n"
        "\n"
        "─────────────────────────\n"
        "【有効なコマンド】\n"
        "─────────────────────────\n"
        "● ユーザーへ送信を承認:\n"
        "    OK / ok / よろしく / 了解 / 送って / 進めて / お願い / Yes / good\n"
        "\n"
        "● 中止（送信せず終了）:\n"
        "    やめて / キャンセル / 中止 / 保留 / Stop\n"
        "\n"
        "もう一度、上記いずれかのコマンドだけを返信本文に含めて再送信してください。\n"
        "（追加の指示文を入れる必要はありません）\n"
        "\n"
        f"error_hash: {incident.error_hash}\n"
    )

    payload: dict[str, Any] = {
        "error_hash": f"{incident.error_hash} CMD-ERR - {short_subject}",
        "user_email": incident.user_email or "（なし）",
        "original_error_summary": "（コマンド認識エラー）",
        "repair_summary": body,
    }

    try:
        resp = requests.post(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {SupportConfig.ADMIN_TOKEN}",
            },
            timeout=(5, 15),
        )
    except requests.RequestException as e:
        logger.error("コマンド認識エラー通知送信失敗: %s", e)
        return False

    if resp.status_code >= 400:
        logger.error("コマンド認識エラー通知 HTTP %d: %s", resp.status_code, resp.text[:300])
        return False

    logger.info("コマンド認識エラー通知送信成功 hash=%s", incident.error_hash)
    return True


def _short(text: str, limit: int) -> str:
    if not text:
        return ""
    text = text.strip()
    return text[:limit] + ("..." if len(text) > limit else "")


# 後方互換のため旧名もエクスポート
notify_kyohei = notify_kyohei_legacy
