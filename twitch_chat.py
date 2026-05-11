"""
Twitch VOD チャット取得モジュール

Twitch GraphQL API 経由で VOD のチャットを全件取得し、
既存 YouTube CSV フォーマット（time, user, comment）と互換のデータ構造で返す。

Reference: https://github.com/lay295/TwitchDownloader （GraphQL クエリの参考実装）
"""

import csv as csv_module
import re
import time
from typing import Optional

# Chrome TLS impersonation（Twitch のボット検知 "failed integrity check" 回避）
# Python の標準 requests だと TLS fingerprint で弾かれる
from curl_cffi import requests

from chat_filter import should_skip_comment, strip_emojis

# downloader からの import は lazy に行う（pytubefix の依存を切り分けるため）
# - safe_write_json: 進捗書き込み
# - sanitize_filename: タイトル正規化


# === Twitch GraphQL 設定 ===

TWITCH_GQL_URL = "https://gql.twitch.tv/gql"

# Twitch web クライアント ID（公知、3rd party tools で広く使用される値）
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"

# ブラウザ風 User-Agent（Python requests のデフォルト UA だと
# Twitch の bot 判定で "failed integrity check" になるため必須）
TWITCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# VideoCommentsByOffsetOrCursor は Twitch 側の persisted query を使う
# （通常クエリだと "Unknown argument cursor on field comments" でエラー）
# Reference: TwitchDownloader が利用している公知 hash
TWITCH_COMMENTS_OPERATION = "VideoCommentsByOffsetOrCursor"
TWITCH_COMMENTS_HASH = (
    "b70a3591ff0f4e0313d126c6a1502d79a1c02baebb288227c582044aa76adf6a"
)

CHAT_FETCH_TIMEOUT_SEC = 60
CHAT_FETCH_RETRIES = 3
CHAT_BATCH_SLEEP_SEC = 0.1


def is_twitch_url(url: str) -> bool:
    """URL が Twitch かどうか判定"""
    return bool(re.match(r"https?://(www\.|m\.)?twitch\.tv/", url))


def extract_twitch_video_id(url: str) -> Optional[str]:
    """
    Twitch VOD URL から video ID を抽出

    対応 URL:
    - https://www.twitch.tv/videos/123456
    - https://twitch.tv/videos/123456
    - https://m.twitch.tv/videos/123456
    - https://www.twitch.tv/{user}/v/123456 (legacy)
    """
    m = re.search(r"twitch\.tv/(?:videos/|[^/]+/v/)(\d+)", url)
    return m.group(1) if m else None


def format_seconds_to_time_string(secs: int) -> str:
    """
    秒数を YouTube CSV と同じ time 形式に変換

    既存形式: "M:SS" or "H:MM:SS"（M / H は zero-pad なし）

    例:
        format_seconds_to_time_string(33)    -> "0:33"
        format_seconds_to_time_string(125)   -> "2:05"
        format_seconds_to_time_string(3725)  -> "1:02:05"
    """
    secs = int(secs)
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fetch_twitch_video_title(video_id: str) -> str:
    """Twitch GraphQL で VOD のタイトル取得（取得失敗時は video ID を返す）"""
    from downloader import sanitize_filename  # lazy import
    title_query = "query GetVideoTitle($videoID: ID!) { video(id: $videoID) { title } }"
    body = {"query": title_query, "variables": {"videoID": video_id}}
    headers = {"Client-ID": TWITCH_CLIENT_ID}
    try:
        r = requests.post(
            TWITCH_GQL_URL,
            headers=headers,
            json=body,
            timeout=30,
            impersonate="chrome120",
        )
        r.raise_for_status()
        title = (r.json().get("data") or {}).get("video", {}).get("title", "") or ""
        sanitized = sanitize_filename(title)
        return sanitized or f"twitch_{video_id}"
    except Exception as e:
        print(f"[WARN] タイトル取得失敗、video ID をタイトルとして使用: {e}", flush=True)
        return f"twitch_{video_id}"


# セッション全体で共有するクライアント識別子と integrity トークン
_CLIENT_STATE = {
    "device_id": None,
    "session_id": None,
    "integrity_token": None,
    "integrity_expires_at": 0.0,
}


def _ensure_client_ids():
    """device_id / session_id を初期化（モジュールロード時に 1 度だけ）"""
    import uuid as _uuid
    if _CLIENT_STATE["device_id"] is None:
        _CLIENT_STATE["device_id"] = _uuid.uuid4().hex[:32]
        _CLIENT_STATE["session_id"] = _uuid.uuid4().hex[:16]


def _build_browser_headers() -> dict:
    """Twitch web client が送る一般的なヘッダー一式"""
    _ensure_client_ids()
    return {
        "Client-ID": TWITCH_CLIENT_ID,
        "User-Agent": TWITCH_USER_AGENT,
        "Origin": "https://www.twitch.tv",
        "Referer": "https://www.twitch.tv/",
        "Accept": "*/*",
        "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
        "X-Device-Id": _CLIENT_STATE["device_id"],
        "Client-Session-Id": _CLIENT_STATE["session_id"],
    }


def _fetch_integrity_token(force_refresh: bool = False) -> str:
    """
    POST /integrity で integrity トークンを取得（24h くらい有効）。
    cursor 付き GraphQL クエリ（ページネーション）で必須。
    """
    now = time.time()
    if (
        not force_refresh
        and _CLIENT_STATE["integrity_token"]
        and now < _CLIENT_STATE["integrity_expires_at"]
    ):
        return _CLIENT_STATE["integrity_token"]

    _ensure_client_ids()
    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Origin": "https://www.twitch.tv",
        "Referer": "https://www.twitch.tv/",
        "X-Device-Id": _CLIENT_STATE["device_id"],
    }
    r = requests.post(
        "https://gql.twitch.tv/integrity",
        headers=headers,
        json={},
        timeout=30,
        impersonate="chrome120",
    )
    r.raise_for_status()
    data = r.json()
    token = data.get("token")
    if not token:
        raise RuntimeError(f"integrity トークン取得失敗: {data}")
    expiration_ms = data.get("expiration", 0)
    _CLIENT_STATE["integrity_token"] = token
    _CLIENT_STATE["integrity_expires_at"] = (expiration_ms / 1000) - 60
    return token


def _post_persisted_query(operation_name: str, query_hash: str, variables: dict) -> dict:
    """
    Twitch の persisted query を使って GraphQL リクエストを送信。

    Twitch のボット検知（"failed integrity check"）回避には:
    1. **curl_cffi の impersonate="chrome120"** で TLS fingerprint を Chrome 偽装
    2. **cursor 付きクエリ（ページネーション）には Client-Integrity トークンが必須**
    3. X-Device-Id / Origin / Referer もブラウザ風に
    """
    _ensure_client_ids()
    headers = {
        "Client-ID": TWITCH_CLIENT_ID,
        "Origin": "https://www.twitch.tv",
        "Referer": "https://www.twitch.tv/",
        "X-Device-Id": _CLIENT_STATE["device_id"],
    }
    # cursor 付きはページネーション → integrity トークン必須
    if "cursor" in variables:
        headers["Client-Integrity"] = _fetch_integrity_token()
    body = {
        "operationName": operation_name,
        "variables": variables,
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": query_hash,
            }
        },
    }
    last_err: Optional[Exception] = None
    for attempt in range(CHAT_FETCH_RETRIES):
        try:
            r = requests.post(
                TWITCH_GQL_URL,
                headers=headers,
                json=body,
                timeout=CHAT_FETCH_TIMEOUT_SEC,
                impersonate="chrome120",
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if attempt < CHAT_FETCH_RETRIES - 1:
                delay = 2 ** attempt
                print(
                    f"⚠️ Twitch チャット取得失敗、{delay}s 待って再試行 "
                    f"{attempt + 1}/{CHAT_FETCH_RETRIES}",
                    flush=True,
                )
                time.sleep(delay)
    raise RuntimeError(f"Twitch チャット取得に失敗: {last_err}")


def _parse_edges(edges: list) -> list:
    """
    GraphQL の edges 配列を YouTube 互換 CSV 形式の dict 配列に変換

    フィルタ:
    - Twitch エモート fragment（emote フィールドあり）を除外
    - 残テキストが英数字のみ（3 文字以上）→ スキップ
    """
    result = []
    for e in edges:
        node = e.get("node") or {}
        secs = node.get("contentOffsetSeconds", 0)
        commenter = node.get("commenter") or {}
        user = commenter.get("displayName") or "（匿名）"
        fragments = (node.get("message") or {}).get("fragments") or []

        # Twitch エモート（fragment.emote が存在）は除外、テキスト fragment のみ抽出
        text_parts = []
        for f in fragments:
            if f.get("emote"):
                continue
            text_parts.append(f.get("text") or "")
        text = "".join(text_parts).strip()

        # 絵文字 / ピクトグラムを取り除く（BIZ UDゴシック等で描画失敗するため）
        text = strip_emojis(text).strip()

        if should_skip_comment(text, user=user):
            continue

        result.append({
            "time": format_seconds_to_time_string(secs),
            "user": user,
            "comment": text,
        })
    return result


def fetch_twitch_chat(video_id: str, progress_path: Optional[str] = None) -> list:
    """
    Twitch GraphQL でチャット全件取得

    Note: cursor ベースのページネーションは Twitch の bot 検知（integrity check）
    で弾かれるため、contentOffsetSeconds ベースでページネーションする。
    重複は (time, user, comment) のキーで除外。

    Returns:
        list of dicts with keys: time, user, comment（既存 CSV 互換）
    """
    all_comments: list = []
    seen_keys: set = set()
    offset = 0
    batch_count = 0
    consecutive_empty_pages = 0

    # 配信終端では Twitch 側が一時エラー（service error / timeout）を返す
    # 3 回連続で発生したら「配信終了」とみなして取得完了
    END_CHECK_ATTEMPTS = 3
    # new_count == 0（同秒大量コメント / 一時的に空ページ）が
    # この回数連続したら本当に終端とみなす。
    # 単発の 0 だと「offset 秒に大量コメント → 全 seen」のケースで誤って打ち切る。
    EMPTY_PAGES_BEFORE_END = 30

    while True:
        variables = {"videoID": video_id, "contentOffsetSeconds": offset}

        data = None
        for check_attempt in range(END_CHECK_ATTEMPTS):
            data = _post_persisted_query(
                TWITCH_COMMENTS_OPERATION,
                TWITCH_COMMENTS_HASH,
                variables,
            )
            errors = data.get("errors") or []
            transient = any(
                "service error" in str(e).lower()
                or "timeout" in str(e).lower()
                or "temporarily unavailable" in str(e).lower()
                for e in errors
            )
            if not errors:
                break
            if not transient:
                break
            print(
                f"[INFO] 配信終了チェック中... ({check_attempt + 1}/{END_CHECK_ATTEMPTS}, "
                f"offset={offset}s)",
                flush=True,
            )
            time.sleep(1)

        if data.get("errors"):
            error_msg = str(data["errors"]).lower()
            if "service error" in error_msg or "timeout" in error_msg:
                # 配信終端を確認、正常完了として取得終了
                print(
                    f"[INFO] ✅ 配信終了を確認、取得完了（累計 {len(all_comments)} 件）",
                    flush=True,
                )
                break
            raise RuntimeError(f"Twitch GraphQL エラー: {data['errors']}")

        video_data = (data.get("data") or {}).get("video")
        if not video_data:
            raise RuntimeError("Twitch VOD が見つかりません（video ID 不正 or 削除済み）")

        comments_data = video_data.get("comments") or {}
        edges = comments_data.get("edges") or []
        if not edges:
            break

        new_count = 0
        last_sec = offset
        page_records = _parse_edges(edges)
        for rec in page_records:
            key = (rec["time"], rec["user"], rec["comment"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_comments.append(rec)
            new_count += 1

        # edges から最大の contentOffsetSeconds を取り出す
        for e in edges:
            sec = e.get("node", {}).get("contentOffsetSeconds", 0) or 0
            if sec > last_sec:
                last_sec = sec

        batch_count += 1
        if batch_count % 5 == 0:
            print(f"[INFO] チャット取得中... {len(all_comments)} 件", flush=True)
            if progress_path:
                from downloader import safe_write_json  # lazy import
                pct = min(75, 45 + len(all_comments) // 100)
                safe_write_json(progress_path, {
                    "progress": pct,
                    "message": f"チャットダウンロード中 ({len(all_comments)} 件)",
                    "phase": "チャットダウンロード",
                })

        # 注: Twitch GraphQL の pageInfo.hasNextPage は cursor ベースの
        # ページネーションを前提にした値を返すことがあり、本実装の
        # contentOffsetSeconds ベース取得では false でも実際は続きがある。
        # そのため hasNextPage は終了判定に使わず、
        # 「connsecutive_empty_pages 回連続で新規ゼロ」だけで判定する。
        if new_count == 0:
            # 全部 seen → offset 秒に大量コメントが集中して同じ秒を再取得してるケース。
            # 次秒に進めて続行。連続で空が続いた時のみ「本当に終端」と判定。
            consecutive_empty_pages += 1
            if consecutive_empty_pages >= EMPTY_PAGES_BEFORE_END:
                print(
                    f"[INFO] {EMPTY_PAGES_BEFORE_END} 連続で新規ゼロ → 取得完了 "
                    f"(累計 {len(all_comments)} 件)",
                    flush=True,
                )
                break
            offset += 1
        else:
            consecutive_empty_pages = 0
            if last_sec <= offset:
                # offset が進まない（同じ秒のコメント大量） → 強制的に 1 秒進める
                offset += 1
            else:
                offset = last_sec
        time.sleep(CHAT_BATCH_SLEEP_SEC)

    print(f"[INFO] Twitch チャット取得完了: {len(all_comments)} 件", flush=True)
    return all_comments


def write_chat_csv(comments: list, csv_path: str) -> None:
    """既存 YouTube CSV と完全互換のフォーマットで書き出し"""
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv_module.writer(f)
        writer.writerow(["time", "user", "comment"])
        for c in comments:
            writer.writerow([c["time"], c["user"], c["comment"]])
