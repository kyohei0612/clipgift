"""
手動ライセンスキー発行 CLI

BOOTH 自動連携が落ちた時の緊急発行 / テスト用 / 特例対応 / プロモコード用に使う。

使い方:
    python scripts/issue_license.py --plan std --buyer-email user@example.com \\
        --reason "BOOTH order #1234 manual" --support-months 12

環境変数:
    CLIPGIFT_LICENSE_SERVER_URL  サーバー URL（デフォルト: workers.dev）
    CLIPGIFT_ADMIN_TOKEN         管理 Bearer トークン
"""

import argparse
import json
import os
import sys
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

DEFAULT_SERVER_URL = "https://clipgift-license.kyohei0612.workers.dev"


def main() -> int:
    parser = argparse.ArgumentParser(description="ライセンスキーを手動発行する")
    parser.add_argument(
        "--plan",
        required=True,
        choices=["lite", "std", "ext"],
        help="プラン種別",
    )
    parser.add_argument(
        "--buyer-email", required=True, help="購入者メールアドレス"
    )
    parser.add_argument("--reason", required=True, help="発行理由（記録用）")
    parser.add_argument("--order-id", default=None, help="注文 ID（任意）")
    parser.add_argument(
        "--support-months",
        type=int,
        default=None,
        help="サポート期間（月、デフォルトはプラン別の標準値）",
    )
    parser.add_argument(
        "--extension-months",
        type=int,
        default=None,
        help="拡張機能期間（月、デフォルトはプラン別の標準値）",
    )
    parser.add_argument(
        "--server-url",
        default=os.environ.get(
            "CLIPGIFT_LICENSE_SERVER_URL", DEFAULT_SERVER_URL
        ),
        help="ライセンスサーバー URL",
    )
    args = parser.parse_args()

    admin_token = os.environ.get("CLIPGIFT_ADMIN_TOKEN")
    if not admin_token:
        print(
            "エラー: 環境変数 CLIPGIFT_ADMIN_TOKEN を設定してください",
            file=sys.stderr,
        )
        return 1

    body = {
        "plan": args.plan,
        "buyer_email": args.buyer_email,
        "reason": args.reason,
    }
    if args.order_id is not None:
        body["order_id"] = args.order_id
    if args.support_months is not None:
        body["support_months"] = args.support_months
    if args.extension_months is not None:
        body["extension_months"] = args.extension_months

    url = args.server_url.rstrip("/") + "/admin/issue"
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
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            print(json.dumps(err_body, ensure_ascii=False, indent=2), file=sys.stderr)
        except Exception:
            pass
        return 2
    except URLError as e:
        print(f"接続エラー: {e.reason}", file=sys.stderr)
        return 3

    if response.get("status") != "ok":
        print(
            f"発行失敗: {json.dumps(response, ensure_ascii=False, indent=2)}",
            file=sys.stderr,
        )
        return 4

    print("=" * 50)
    print(f"✅ ライセンスキー発行完了")
    print("=" * 50)
    print(f"キー:                {response['key']}")
    print(f"プラン:              {args.plan}")
    print(f"購入者:              {args.buyer_email}")
    print(f"サポート期限:        {response.get('support_expires_at', '—')}")
    print(f"拡張機能期限:        {response.get('extension_expires_at', '—')}")
    print(f"発行理由:            {args.reason}")
    print("=" * 50)
    print("\nメール送付テンプレ:")
    print("---")
    print(f"件名: クリップギフトご購入ありがとうございます（ライセンスキー）")
    print(f"")
    print(f"このたびはクリップギフトをご購入いただきありがとうございます。")
    print(f"")
    print(f"以下のライセンスキーで認証してください:")
    print(f"")
    print(f"    {response['key']}")
    print(f"")
    print(f"アプリ初回起動時の入力欄にコピペしてください。")
    print(f"最大 2 台までアクティベーションできます。")
    print("---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
