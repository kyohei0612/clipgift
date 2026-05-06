"""
ライセンスキー失効 CLI

BOOTH 返金 / 不正検出 / キー流出時に使う。
失効後は /activate /verify で即時拒否される。

使い方:
    python scripts/revoke_license.py --key CGFT-LITE-XXXX-XXXX-XXXX --reason "BOOTH refund"
"""

import argparse
import json
import os
import sys
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

DEFAULT_SERVER_URL = "https://clipgift-license.kyohei0612.workers.dev"


def main() -> int:
    parser = argparse.ArgumentParser(description="ライセンスキーを失効する")
    parser.add_argument("--key", required=True, help="失効するライセンスキー")
    parser.add_argument("--reason", required=True, help="失効理由（記録用、必須）")
    parser.add_argument(
        "--server-url",
        default=os.environ.get("CLIPGIFT_LICENSE_SERVER_URL", DEFAULT_SERVER_URL),
    )
    args = parser.parse_args()

    admin_token = os.environ.get("CLIPGIFT_ADMIN_TOKEN")
    if not admin_token:
        print(
            "エラー: 環境変数 CLIPGIFT_ADMIN_TOKEN を設定してください",
            file=sys.stderr,
        )
        return 1

    body = {"key": args.key.strip().upper(), "reason": args.reason}
    url = args.server_url.rstrip("/") + "/admin/revoke"
    req = urllib_request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {admin_token}",
        },
        method="POST",
    )

    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        print(f"エラー: HTTP {e.code}", file=sys.stderr)
        return 2
    except URLError as e:
        print(f"接続エラー: {e.reason}", file=sys.stderr)
        return 3

    if response.get("status") != "ok":
        print(
            f"失効失敗: {json.dumps(response, ensure_ascii=False, indent=2)}",
            file=sys.stderr,
        )
        return 4

    print("=" * 50)
    print(f"✅ ライセンスキー失効完了")
    print("=" * 50)
    print(f"キー:        {args.key}")
    print(f"失効日時:    {response.get('revoked_at', '—')}")
    print(f"理由:        {args.reason}")
    print("=" * 50)
    print(
        "\n注意: 該当キーで動作中のアプリは、次回起動時 / 30 日後の /verify で停止します。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
