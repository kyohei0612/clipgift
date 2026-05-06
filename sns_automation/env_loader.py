"""環境変数ローダー

`.env` をプロジェクトルートから読み込む（python-dotenv 依存を避けるシンプル実装）。
GitHub Actions 環境では既に環境変数がセットされているのでスキップ。
"""

import os
from pathlib import Path


def load_env(env_path: Path | str | None = None) -> dict[str, str]:
    """`.env` を読み込み、`os.environ` に未設定のキーだけ反映する。

    既に環境変数がある場合（GitHub Actions など）はそちらを優先。
    戻り値は `.env` の内容そのもの（環境変数より広い、デバッグ用）。
    """
    if env_path is None:
        # sns_automation/.env を優先、なければプロジェクトルートを fallback（旧配置互換）
        here = Path(__file__).resolve().parent
        env_path = here / ".env"
        if not env_path.exists():
            env_path = here.parent / ".env"
    env_path = Path(env_path)

    parsed: dict[str, str] = {}
    if not env_path.exists():
        return parsed

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        parsed[key] = value
        os.environ.setdefault(key, value)

    return parsed


def require(key: str) -> str:
    """環境変数を必須として取得。未設定なら例外。"""
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"環境変数 {key} が未設定です（.env を確認）")
    return value
