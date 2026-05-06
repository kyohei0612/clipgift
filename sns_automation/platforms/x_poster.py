"""X (Twitter) 投稿モジュール

X API v2 + OAuth 1.0a User Context を使用。
tweepy ではなく requests + requests_oauthlib（軽量）。

参考: https://docs.x.com/x-api/posts/creation-of-a-post
"""

from __future__ import annotations

import logging

import requests
from requests_oauthlib import OAuth1

from ..env_loader import require

logger = logging.getLogger(__name__)

API_BASE = "https://api.twitter.com/2"


def _auth() -> OAuth1:
    return OAuth1(
        require("X_CONSUMER_KEY"),
        require("X_CONSUMER_SECRET"),
        require("X_ACCESS_TOKEN"),
        require("X_ACCESS_TOKEN_SECRET"),
    )


def post(text: str, timeout: int = 30) -> str:
    """X にテキスト投稿する。

    Args:
        text: 投稿本文（X は 280 文字上限、半角換算）
        timeout: HTTP タイムアウト秒

    Returns:
        Tweet ID
    """
    resp = requests.post(
        f"{API_BASE}/tweets",
        auth=_auth(),
        json={"text": text},
        timeout=timeout,
    )
    resp.raise_for_status()
    tweet_id = resp.json()["data"]["id"]
    logger.info("X 投稿成功: tweet_id=%s", tweet_id)
    return tweet_id


def me() -> dict:
    """疎通確認用に自分のユーザー情報を取得する。"""
    resp = requests.get(
        f"{API_BASE}/users/me",
        auth=_auth(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("data", {})
