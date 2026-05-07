"""
サポートセンター連携の設定（環境変数読み込み）

3 種類の設定を扱う:
- アプリ側（ユーザーの PC で動く）: Workers エンドポイント URL のみ
- ローカル側（kyohei さん PC）: IMAP 認証情報 + Workers エンドポイント
- Workers 側: wrangler secrets で別管理（ここでは扱わない）
"""

import os
from pathlib import Path

# .env を読み込む（軽量実装、python-dotenv 非依存）
_ENV_FILE = Path(__file__).resolve().parent / ".env"
if _ENV_FILE.exists():
    with open(_ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


# アプリ側で使う公開エンドポイント（認証情報なし）
SUPPORT_REPORT_ENDPOINT = os.environ.get(
    "SUPPORT_REPORT_ENDPOINT",
    "https://clipgift-license.kyohei0612.workers.dev/support/report",
)


class SupportConfig:
    """ローカル側（kyohei さん PC）の設定。アプリ側は直接 SUPPORT_REPORT_ENDPOINT を参照。"""

    # IMAP 受信設定
    IMAP_HOST = os.environ.get("SUPPORT_IMAP_HOST", "imap.mail.yahoo.co.jp")
    IMAP_PORT = int(os.environ.get("SUPPORT_IMAP_PORT", "993"))
    MAIL_USER = os.environ.get("SUPPORT_MAIL_USER", "")
    MAIL_APP_PASSWORD = os.environ.get("SUPPORT_MAIL_APP_PASSWORD", "")

    # Workers 経由で送信する際のエンドポイント / 管理トークン
    WORKERS_ENDPOINT = os.environ.get(
        "SUPPORT_WORKERS_ENDPOINT",
        "https://clipgift-license.kyohei0612.workers.dev",
    )
    ADMIN_TOKEN = os.environ.get("SUPPORT_ADMIN_TOKEN", "")

    # Claude CLI
    CLAUDE_CLI_PATH = os.environ.get("CLAUDE_CLI_PATH", "claude")

    # ポーリング設定
    POLL_INTERVAL_MINUTES = int(os.environ.get("SUPPORT_POLL_INTERVAL", "10"))
    SUBJECT_FILTER = os.environ.get(
        "SUPPORT_SUBJECT_FILTER", "【ClipGift エラー報告】"
    )
    SUBJECT_FILTER_REQUEST = os.environ.get(
        "SUPPORT_SUBJECT_FILTER_REQUEST", "【ClipGift ご要望】"
    )

    # 受信メール保存ディレクトリ
    INCOMING_DIR = (
        Path(__file__).resolve().parent / "incoming"
    )

    @classmethod
    def validate_local(cls) -> list[str]:
        """ローカル側設定の不足チェック。エラーリストを返す。"""
        errors: list[str] = []
        if not cls.MAIL_USER:
            errors.append("SUPPORT_MAIL_USER が設定されていません")
        if not cls.MAIL_APP_PASSWORD:
            errors.append("SUPPORT_MAIL_APP_PASSWORD が設定されていません")
        if not cls.ADMIN_TOKEN:
            errors.append(
                "SUPPORT_ADMIN_TOKEN が未設定です（kyohei 通知 / ユーザー返信に必須）"
            )
        return errors
