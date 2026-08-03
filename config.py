"""
アプリの設定値を一元管理する。
ハードコードされていた閾値・タイムアウトを集約し、必要なら
環境変数 (CLIPGEN_*) で上書きできる構成。
"""

import os


def _env_int(name, default):
    try:
        return int(os.environ.get(f"CLIPGEN_{name}", default))
    except (TypeError, ValueError):
        return default


def _env_float(name, default):
    try:
        return float(os.environ.get(f"CLIPGEN_{name}", default))
    except (TypeError, ValueError):
        return default


# ---------- HTTP / アップロード ----------
SERVER_HOST = os.environ.get("CLIPGEN_HOST", "127.0.0.1")
SERVER_PORT = _env_int("PORT", 5000)

# request 全体の上限（Flask/Werkzeug が強制）。長時間配信を想定して 20GB。
MAX_UPLOAD_BYTES = _env_int("MAX_UPLOAD_BYTES", 20 * 1024 * 1024 * 1024)

ALLOWED_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".m4v"}
ALLOWED_CSV_EXTS = {".csv", ".txt"}


# ---------- エラー報告の添付ファイル ----------
# templates/index2.html / index.html のクライアント側制限と必ず揃えること。
# （クライアントは fetch を握られれば迂回できるので、サーバー側でも同じ値を強制する）
ATTACHMENT_MAX_COUNT = _env_int("ATTACHMENT_MAX_COUNT", 3)
ATTACHMENT_MAX_BYTES = _env_int("ATTACHMENT_MAX_BYTES", 5 * 1024 * 1024)


# ---------- watchdog / heartbeat ----------
# 2026-05-06 に「ハートビート途絶で自動終了」は廃止済み。
# 終了経路はユーザー明示の /api/shutdown のみなので、関連する閾値設定も削除した。
# （復活させる場合は app.py の _heartbeat_watchdog ごと復元すること）


# ---------- プロセス / クリップ生成 ----------
# 音声抽出 ffmpeg の上限秒数。ハングした ffmpeg が Flask のリクエストスレッドを
# 永久占有するのを防ぐ（長尺クリップでも 10 分あれば足りる）
FFMPEG_TIMEOUT_SEC = _env_int("FFMPEG_TIMEOUT_SEC", 600)
# 全クリップ完了後、UI が結果を取得するまで待つ秒数
COMPLETION_HOLD_SEC = _env_int("COMPLETION_HOLD_SEC", 5)
# プロセスログのリングバッファ上限
PROCESS_LOG_MAX = _env_int("PROCESS_LOG_MAX", 200)
# キャンセル時に terminate → kill にエスカレートするタイムアウト
TERMINATE_TIMEOUT_SEC = _env_float("TERMINATE_TIMEOUT_SEC", 1.0)


# ---------- progress ファイルのリトライ ----------
PROGRESS_READ_RETRIES = _env_int("PROGRESS_READ_RETRIES", 3)
PROGRESS_READ_RETRY_INTERVAL_SEC = _env_float("PROGRESS_READ_RETRY_INTERVAL_SEC", 0.05)
