"""3 媒体の API 疎通確認スクリプト

使い方:
    python -m sns_automation.verify_apis

`.env` を読み込み、各媒体に対して読み取り系 API を叩いて成功可否を表示する。
投稿は行わない（読み取りのみで安全）。
"""

from __future__ import annotations

import sys

from .env_loader import load_env
from .platforms import bluesky_poster, threads_poster, x_poster


def main() -> int:
    load_env()

    results: list[tuple[str, bool, str]] = []

    print("=" * 60)
    print("SNS API 疎通確認")
    print("=" * 60)

    print("\n[1/3] Threads (Meta Graph API)")
    try:
        info = threads_poster.me()
        msg = f"@{info.get('username')}  id={info.get('id')}"
        print(f"  [OK] {msg}")
        results.append(("Threads", True, msg))
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"  [NG] {msg}")
        results.append(("Threads", False, msg))

    print("\n[2/3] X (Twitter API v2 / OAuth 1.0a)")
    try:
        info = x_poster.me()
        msg = f"@{info.get('username')}  id={info.get('id')}  name={info.get('name')}"
        print(f"  [OK] {msg}")
        results.append(("X", True, msg))
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"  [NG] {msg}")
        results.append(("X", False, msg))

    print("\n[3/3] Bluesky (atproto)")
    try:
        info = bluesky_poster.me()
        msg = f"{info['handle']}  did={info['did']}"
        print(f"  [OK] {msg}")
        results.append(("Bluesky", True, msg))
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"  [NG] {msg}")
        results.append(("Bluesky", False, msg))

    print()
    print("=" * 60)
    failures = [name for name, ok, _ in results if not ok]
    if failures:
        print(f"NG: {', '.join(failures)}")
        return 1
    print("全媒体 OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
