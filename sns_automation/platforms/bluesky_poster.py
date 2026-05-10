"""Bluesky 投稿モジュール

atproto ライブラリ経由で AT Protocol に投稿する。

参考: https://atproto.blue/
"""

from __future__ import annotations

import logging
import re

from atproto import Client, client_utils

from ..env_loader import require

logger = logging.getLogger(__name__)


def _client() -> Client:
    """ログイン済みクライアントを返す（毎回ログインしてセッション取得）。"""
    client = Client()
    client.login(require("BLUESKY_HANDLE"), require("BLUESKY_APP_PASSWORD"))
    return client


# ハッシュタグ正規表現（# の後に空白以外が 1 文字以上、改行や , . など終端記号は含めない）
# 日本語ハッシュタグも拾えるよう、ASCII 制御文字 / スペース / 句読点系を除外
_HASHTAG_RE = re.compile(r"#([^\s#,.!?。、！？\n]+)")


def _build_text_with_facets(text: str):
    """text 中の #ハッシュタグを atproto の TextBuilder で組み立て、facets 付きで返す。

    Bluesky は AT Protocol の仕様で `facets`（リッチテキストの範囲メタ情報）を
    明示的に渡さないとハッシュタグがリンクとして認識されない（X / Threads と違う）。
    """
    tb = client_utils.TextBuilder()
    last_end = 0
    for m in _HASHTAG_RE.finditer(text):
        # ハッシュ前の通常テキスト
        if m.start() > last_end:
            tb.text(text[last_end:m.start()])
        # ハッシュタグ部分（# は表示には含まれるが、tag 値は # を除く）
        tag_value = m.group(1)
        tb.tag(m.group(0), tag_value)
        last_end = m.end()
    # 最後のテキスト残り
    if last_end < len(text):
        tb.text(text[last_end:])
    return tb


def post(text: str) -> str:
    """Bluesky にテキスト投稿する（ハッシュタグ facets 付き）。

    Args:
        text: 投稿本文（Bluesky は 300 文字上限、grapheme 基準）

    Returns:
        投稿 URI（at://did:plc:.../app.bsky.feed.post/... 形式）
    """
    client = _client()
    tb = _build_text_with_facets(text)
    response = client.send_post(text=tb)
    logger.info("Bluesky 投稿成功: uri=%s", response.uri)
    return response.uri


def me() -> dict:
    """疎通確認用に自分のプロフィール情報を取得する。"""
    client = _client()
    return {
        "handle": client.me.handle,
        "did": client.me.did,
    }
