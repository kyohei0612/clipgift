"""Threads 投稿モジュール

Threads Graph API は 2 ステップ:
1. メディアコンテナ作成 → creation_id 取得
2. 公開 → 投稿 ID 取得

参考: https://developers.facebook.com/docs/threads
"""

from __future__ import annotations

import logging

import requests

from ..env_loader import require

logger = logging.getLogger(__name__)

API_BASE = "https://graph.threads.net/v1.0"


def post(text: str, image_url: str | None = None, timeout: int = 30) -> str:
    """Threads にテキスト or 画像付きテキストを投稿する。

    Args:
        text: 投稿本文（Threads は 500 文字上限）
        image_url: 画像 URL（公開アクセス可能な HTTPS 必須）
        timeout: HTTP タイムアウト秒

    Returns:
        投稿 ID（Threads 上の post id）
    """
    access_token = require("THREADS_ACCESS_TOKEN")
    user_id = require("THREADS_USER_ID")

    # Step 1: コンテナ作成
    params: dict[str, str] = {
        "access_token": access_token,
        "media_type": "IMAGE" if image_url else "TEXT",
        "text": text,
    }
    if image_url:
        params["image_url"] = image_url

    container_resp = requests.post(
        f"{API_BASE}/{user_id}/threads",
        params=params,
        timeout=timeout,
    )
    container_resp.raise_for_status()
    creation_id = container_resp.json()["id"]
    logger.info("Threads コンテナ作成: creation_id=%s", creation_id)

    # Step 2: 公開
    publish_resp = requests.post(
        f"{API_BASE}/{user_id}/threads_publish",
        params={
            "access_token": access_token,
            "creation_id": creation_id,
        },
        timeout=timeout,
    )
    publish_resp.raise_for_status()
    post_id = publish_resp.json()["id"]
    logger.info("Threads 投稿成功: post_id=%s", post_id)
    return post_id


def me() -> dict:
    """疎通確認用に自分のプロフィールを取得する。"""
    access_token = require("THREADS_ACCESS_TOKEN")
    resp = requests.get(
        f"{API_BASE}/me",
        params={
            "fields": "id,username,threads_profile_picture_url,threads_biography",
            "access_token": access_token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
