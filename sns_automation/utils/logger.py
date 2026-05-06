"""ロギング設定

CLI からも GitHub Actions からも同じ形式で出るように一括設定。
"""

import logging
import os


def setup_logging(level: str | None = None) -> None:
    """ルートロガーを設定する。`level` 未指定時は環境変数 LOG_LEVEL or INFO。"""
    lvl = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, lvl, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
