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


# ---------- watchdog / heartbeat ----------
# UI からハートビートが何秒途絶えたらサーバーを終了するか
HEARTBEAT_TIMEOUT_SEC = _env_int("HEARTBEAT_TIMEOUT_SEC", 30)
# 起動直後に watchdog を抑制する秒数（false positive 防止）
WATCHDOG_START_DELAY_SEC = _env_int("WATCHDOG_START_DELAY_SEC", 10)
# watchdog のチェック間隔
WATCHDOG_INTERVAL_SEC = _env_float("WATCHDOG_INTERVAL_SEC", 1.0)


# ---------- プロセス / クリップ生成 ----------
# 全クリップ完了後、UI が結果を取得するまで待つ秒数
COMPLETION_HOLD_SEC = _env_int("COMPLETION_HOLD_SEC", 5)
# プロセスログのリングバッファ上限
PROCESS_LOG_MAX = _env_int("PROCESS_LOG_MAX", 200)
# キャンセル時に terminate → kill にエスカレートするタイムアウト
TERMINATE_TIMEOUT_SEC = _env_float("TERMINATE_TIMEOUT_SEC", 1.0)


# ---------- progress ファイルのリトライ ----------
PROGRESS_READ_RETRIES = _env_int("PROGRESS_READ_RETRIES", 3)
PROGRESS_READ_RETRY_INTERVAL_SEC = _env_float("PROGRESS_READ_RETRY_INTERVAL_SEC", 0.05)
