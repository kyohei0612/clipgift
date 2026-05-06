"""
sns_automation/.env の値を GitHub Actions Secrets に同期する。

- 値はターミナルに出さず、stdin 経由で gh CLI に渡す
- 既存 Secret は上書き（gh secret set の挙動）
- 認証トークンは bin/github_token.txt から読み込む（環境変数 GH_TOKEN にセット）
"""

import os
import subprocess
import sys
from pathlib import Path

# プロジェクトルートに移動（sns_automation の env_loader を使うため）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sns_automation.env_loader import load_env

REPO = "kyohei0612/clipgift"

# 同期対象 Secret 名（workflow が必須としている 8 + 補助 2）
TARGET_SECRETS = [
    "THREADS_APP_ID",
    "THREADS_APP_SECRET",
    "THREADS_ACCESS_TOKEN",
    "THREADS_USER_ID",
    "X_CONSUMER_KEY",
    "X_CONSUMER_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
    "BLUESKY_HANDLE",
    "BLUESKY_APP_PASSWORD",
]


def main() -> int:
    # GitHub トークンを bin/github_token.txt から読む
    token_file = PROJECT_ROOT / "bin" / "github_token.txt"
    if not token_file.exists():
        print(f"❌ {token_file} が見つかりません", file=sys.stderr)
        return 1
    gh_token = token_file.read_text(encoding="utf-8").strip()

    # .env から値を読み込む
    env_path = PROJECT_ROOT / "sns_automation" / ".env"
    parsed = load_env(env_path)
    print(f"📂 {env_path} から {len(parsed)} 件の環境変数を読み込み")

    # 各 Secret を gh CLI に流す
    successes: list[str] = []
    failures: list[tuple[str, str]] = []
    skipped: list[str] = []

    for name in TARGET_SECRETS:
        value = parsed.get(name, "").strip()
        if not value:
            skipped.append(name)
            continue
        # ターミナルに値を出さず、stdin 経由で gh secret set に渡す
        # （--body フラグなしなら stdin から読み込む仕様）
        result = subprocess.run(
            ["gh", "secret", "set", name, "--repo", REPO],
            input=value,
            text=True,
            capture_output=True,
            env={**os.environ, "GH_TOKEN": gh_token},
        )
        if result.returncode == 0:
            successes.append(name)
            print(f"  ✅ {name}")
        else:
            err = (result.stderr or result.stdout or "").strip()
            failures.append((name, err))
            print(f"  ❌ {name}: {err}", file=sys.stderr)

    print("=" * 50)
    print(
        f"成功: {len(successes)} / 失敗: {len(failures)} / スキップ(未設定): {len(skipped)}"
    )
    if skipped:
        print(f"  ⚠️  .env に値がない: {', '.join(skipped)}")
    return 0 if not failures else 2


if __name__ == "__main__":
    sys.exit(main())
