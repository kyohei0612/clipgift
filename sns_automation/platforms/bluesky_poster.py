"""Bluesky 投稿モジュール

atproto ライブラリ経由で AT Protocol に投稿する。

参考: https://atproto.blue/
"""

from __future__ import annotations

import logging

from atproto import Client

from ..env_loader import require

logger = logging.getLogger(__name__)


def _client() -> Client:
    """ログイン済みクライアントを返す（毎回ログインしてセッション取得）。"""
    client = Client()
    client.login(require("BLUESKY_HANDLE"), require("BLUESKY_APP_PASSWORD"))
    return client


def post(text: str) -> str:
    """Bluesky にテキスト投稿する。

    Args:
        text: 投稿本文（Bluesky は 300 文字上限、grapheme 基準）

    Returns:
        投稿 URI（at://did:plc:.../app.bsky.feed.post/... 形式）
    """
    client = _client()
    response = client.send_post(text=text)
    logger.info("Bluesky 投稿成功: uri=%s", response.uri)
    return response.uri


def me() -> dict:
    """疎通確認用に自分のプロフィール情報を取得する。"""
    client = _client()
    return {
        "handle": client.me.handle,
        "did": client.me.did,
    }
