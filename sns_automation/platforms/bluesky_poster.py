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


# URL とハッシュタグを 1 つの正規表現で順序通りに抽出する。
# - URL は末尾句読点 ,.!?。、！？)] を含めない（ペースト時に文末記号が引っ付く誤検出を防ぐ）
# - ハッシュタグは # の後に空白 / 句読点を含まない 1 文字以上
# 2026-05-13: URL facets 抜けバグ修正（kyohei 報告: 自動投稿の note URL がリンク化されなかった）
_TOKEN_RE = re.compile(
    r"(https?://[^\s,.!?。、！？)\]>]+|#[^\s#,.!?。、！？\n]+)"
)


def _build_text_with_facets(text: str):
    """text 中の URL / #ハッシュタグを atproto の TextBuilder で組み立て、facets 付きで返す。

    Bluesky は AT Protocol の仕様で `facets`（リッチテキストの範囲メタ情報）を
    明示的に渡さないと URL もハッシュタグもリンクとして認識されない
    （X / Threads は自動検出してくれるので不要）。
    """
    tb = client_utils.TextBuilder()
    last_end = 0
    for m in _TOKEN_RE.finditer(text):
        # マッチ前の通常テキスト
        if m.start() > last_end:
            tb.text(text[last_end:m.start()])
        token = m.group(0)
        if token.startswith("#"):
            # ハッシュタグ（# 込みで表示、tag 値は # を除く）
            tb.tag(token, token[1:])
        else:
            # URL（表示テキストと href を同じ URL にする）
            tb.link(token, token)
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
