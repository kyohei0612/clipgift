"""
手動ライセンスキー発行 CLI

BOOTH 自動連携が落ちた時の緊急発行 / テスト用 / 特例対応 / プロモコード用に使う。

使い方:
    python scripts/issue_license.py --plan std --buyer-email user@example.com \\
        --reason "BOOTH order #1234 manual" --support-months 12

環境変数:
    CLIPGIFT_LICENSE_SERVER_URL  サーバー URL（デフォルト: workers.dev）
    CLIPGIFT_ADMIN_TOKEN         管理 Bearer トークン

副作用:
    - サーバーで /admin/issue を呼んでキーを発行
    - 発行履歴を scripts/.issued_keys.jsonl に追記（gitignore 済み）
    - メール文を scripts/issued_keys/YYYY-MM-DD-<keyTail>.txt に保存（コピペ用）
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

DEFAULT_SERVER_URL = "https://clipgift-license.kyohei0612.workers.dev"

SCRIPT_DIR = Path(__file__).resolve().parent
HISTORY_FILE = SCRIPT_DIR / ".issued_keys.jsonl"
EMAIL_DIR = SCRIPT_DIR / "issued_keys"

PLAN_LABELS = {"single": "ClipGift 買い切り版"}
PLAN_PRICES = {"single": "通常 9,800 円 / 先着 10 名限定 6,980 円"}


def main() -> int:
    parser = argparse.ArgumentParser(description="ライセンスキーを手動発行する")
    parser.add_argument(
        "--plan",
        default="single",
        choices=["single"],
        help="プラン種別（Phase 1 は single 1 種のみ）",
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="サーバー呼び出しをせず、入力内容のみ確認する",
    )
    args = parser.parse_args()

    admin_token = os.environ.get("CLIPGIFT_ADMIN_TOKEN")
    if not args.dry_run and not admin_token:
        print(
            "エラー: 環境変数 CLIPGIFT_ADMIN_TOKEN を設定してください",
            file=sys.stderr,
        )
        return 1

    # 重複発行警告（同じ buyer_email + plan で短時間に再発行）
    _warn_if_duplicate_recent(args.buyer_email, args.plan)

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

    if args.dry_run:
        print("[dry-run] 送信予定のリクエスト:")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return 0

    response = _call_issue_api(args.server_url, admin_token, body)
    if response is None:
        return 2

    if response.get("status") != "ok":
        print(
            f"発行失敗: {json.dumps(response, ensure_ascii=False, indent=2)}",
            file=sys.stderr,
        )
        return 4

    key = response["key"]
    issued_at = datetime.now().isoformat(timespec="seconds")

    # ① 履歴ログ追記
    _append_history(
        {
            "issued_at": issued_at,
            "key": key,
            "plan": args.plan,
            "buyer_email": args.buyer_email,
            "order_id": args.order_id,
            "reason": args.reason,
            "support_expires_at": response.get("support_expires_at"),
            "extension_expires_at": response.get("extension_expires_at"),
        }
    )

    # ② メール文を .txt に保存
    email_path = _save_email_file(args, response, issued_at)

    # ③ ターミナルにも表示
    _print_summary(args, response, email_path)
    return 0


def _call_issue_api(server_url: str, admin_token: str, body: dict) -> dict | None:
    """サーバー /admin/issue を呼ぶ"""
    url = server_url.rstrip("/") + "/admin/issue"
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
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        print(f"エラー: HTTP {e.code}", file=sys.stderr)
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            print(json.dumps(err_body, ensure_ascii=False, indent=2), file=sys.stderr)
        except Exception:
            pass
        return None
    except URLError as e:
        print(f"接続エラー: {e.reason}", file=sys.stderr)
        return None


def _warn_if_duplicate_recent(email: str, plan: str, hours: int = 24) -> None:
    """直近 24h で同一 email × plan の発行があれば警告（自動キャンセルはしない）"""
    if not HISTORY_FILE.exists():
        return
    cutoff = datetime.now().timestamp() - hours * 3600
    matches: list[dict] = []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    entry.get("buyer_email", "").lower() == email.lower()
                    and entry.get("plan") == plan
                ):
                    issued_ts = datetime.fromisoformat(
                        entry["issued_at"]
                    ).timestamp()
                    if issued_ts >= cutoff:
                        matches.append(entry)
    except OSError:
        return

    if matches:
        print(
            f"⚠️  警告: 過去 {hours}h 以内に同じ購入者 ({email}) "
            f"× {plan} プランで {len(matches)} 件発行済みです:",
            file=sys.stderr,
        )
        for m in matches:
            print(f"  - {m['issued_at']}  {m['key']}  ({m.get('reason','')})", file=sys.stderr)
        confirm = input("それでも続行しますか？ (yes/no): ").strip().lower()
        if confirm not in ("yes", "y"):
            print("中止しました", file=sys.stderr)
            sys.exit(10)


def _append_history(entry: dict) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _save_email_file(args, response: dict, issued_at: str) -> Path:
    """メール送付テンプレを .txt ファイルに保存"""
    EMAIL_DIR.mkdir(parents=True, exist_ok=True)
    key = response["key"]
    key_tail = key.split("-")[-1] if "-" in key else key[-4:]
    today = issued_at[:10]
    filename = EMAIL_DIR / f"{today}-{key_tail}-{args.plan}.txt"

    plan_label = PLAN_LABELS.get(args.plan, args.plan)
    plan_price = PLAN_PRICES.get(args.plan, "")
    support_expires = response.get("support_expires_at") or "—"
    if support_expires != "—":
        support_expires = support_expires[:10]

    body = f"""宛先: {args.buyer_email}
件名: 【ClipGift】ご購入ありがとうございます（ライセンスキー）

{args.buyer_email} 様

このたびはクリップギフト（{plan_label}プラン / {plan_price}）を
ご購入いただき、誠にありがとうございます。

以下のライセンスキーで認証してください：

    {key}

【手順】
  1. クリップギフトのインストーラー（ClipGift_Setup.exe）を実行してインストール
  2. アプリ初回起動時、ブラウザでアクティベーション画面が開きます
  3. 上記のキーをコピー＆ペーストして「アクティベーションする」を押下
  4. 認証完了後、自動でアプリが起動します

【ご利用条件】
  ・ アクティベーション可能台数: 最大 2 台
  ・ サポート / アップデート期限: {support_expires}
  ・ 別マシンに移行する場合は、現マシンの設定画面から「ライセンス解除」を実行してください

【サポート】
  ご不明な点や不具合がございましたら、以下までお気軽にお問い合わせください。
  メール: support@clipgift.app
  返信目安: 平日営業日 24 時間以内

今後ともクリップギフトをよろしくお願いいたします。

---
発行日時: {issued_at}
注文 ID: {args.order_id or '—'}
発行理由（社内記録）: {args.reason}
"""
    filename.write_text(body, encoding="utf-8")
    return filename


def _print_summary(args, response: dict, email_path: Path) -> None:
    print("=" * 60)
    print(f"✅ ライセンスキー発行完了")
    print("=" * 60)
    print(f"キー:                {response['key']}")
    print(f"プラン:              {args.plan} ({PLAN_LABELS.get(args.plan)})")
    print(f"購入者:              {args.buyer_email}")
    print(f"サポート期限:        {response.get('support_expires_at', '—')}")
    print(f"拡張機能期限:        {response.get('extension_expires_at', '—')}")
    print(f"発行理由:            {args.reason}")
    print("=" * 60)
    print(f"📧 メール文を保存しました:")
    print(f"   {email_path}")
    print(f"   → このファイルを開いて本文をコピーし、購入者へ送信してください")
    print(f"📜 履歴: {HISTORY_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
