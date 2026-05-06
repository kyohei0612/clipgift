# BOOTH 受注対応の運用手順

クリップギフトを BOOTH で販売した際の **受注 → ライセンスキー発行 → 顧客送信** までの作業手順書。

> 想定運用: BOOTH 自動連携が完成するまでは「半手動運用」。
> kyohei さんが BOOTH 通知メールを受け取ったら、本書の手順を上から実行する。

---

## 0. 事前準備（一度だけ）

### 環境変数を設定

PowerShell で永続化する場合（推奨）:

```powershell
[Environment]::SetEnvironmentVariable("CLIPGIFT_ADMIN_TOKEN", "<管理用 Bearer トークン>", "User")
[Environment]::SetEnvironmentVariable("CLIPGIFT_LICENSE_SERVER_URL", "https://clipgift-license.kyohei0612.workers.dev", "User")
```

> 注: 管理用 Bearer トークンは Cloudflare Workers の `wrangler secret put ADMIN_BEARER_TOKEN` で設定したものと一致させる。
> 紛失した場合は `npx wrangler secret put ADMIN_BEARER_TOKEN` で上書き再設定。

### サーバー疎通確認

```powershell
curl https://clipgift-license.kyohei0612.workers.dev/health
# → {"status":"ok"} が返れば OK
```

---

## 1. BOOTH の受注通知が届いたら

### 確認すること

BOOTH の販売者管理画面 → 注文一覧で:

- [ ] 入金完了している（pending ではない）
- [ ] 購入者のメールアドレス（メッセージ機能で確認可、見えなければ DM 依頼）
- [ ] どのプランを買ったか（ライト / スタンダード / 拡張）
- [ ] 注文 ID（後の追跡用）

> ⚠️ 入金完了前にキー発行すると、後でキャンセルされた時に失効処理が必要になる。必ず入金確認後。

---

## 2. ライセンスキー発行（CLI 一発）

```powershell
cd C:\Users\kyohei\ClipGift

python scripts/issue_license.py `
    --plan std `
    --buyer-email "buyer@example.com" `
    --order-id "BOOTH-2026-05-22-001" `
    --reason "BOOTH order"
```

### `--plan` の選び方

| プラン | 引数 | 価格 |
|--------|------|------|
| ライト | `--plan lite` | 1,980 円 |
| スタンダード（メイン） | `--plan std` | 4,980 円 / 早期割 2,980 円 |
| 拡張 | `--plan ext` | 9,800 円 |

### 実行後に起こること

- ✅ Cloudflare Workers でキー発行
- ✅ `scripts/.issued_keys.jsonl` に発行履歴追記（gitignore 済み）
- ✅ `scripts/issued_keys/YYYY-MM-DD-XXXX-std.txt` にメール文保存

### 重複発行警告

同じ購入者 × 同じプランで 24h 以内に再発行しようとすると警告が出る。
通常は誤操作なので **No** を選択。BOOTH のキャンセル → 再注文等で正当な再発行なら **yes**。

---

## 3. 購入者にメール送信

### 手順

1. ターミナルに表示された **メール文ファイルパス** を開く
   ```
   📧 メール文を保存しました:
      C:\Users\kyohei\ClipGift\scripts\issued_keys\2026-05-22-X5Y6-std.txt
   ```
2. ファイルを開いて全文をコピー
3. 普段使うメーラー（Gmail / Outlook 等）で新規メール作成
4. 宛先欄にファイル冒頭の `宛先: ...` のメールアドレスを貼り付け
5. 件名欄にファイルの `件名: ...` を貼り付け
6. 本文欄にそれ以降の本文をすべて貼り付け
7. 送信前に最終チェック:
   - [ ] 宛先が正しい
   - [ ] キーが正しく入っている
   - [ ] サポート期限の日付が妥当
8. 送信

### BOOTH メッセージ機能で送る場合

メールアドレス開示拒否の購入者には BOOTH メッセージ機能で送信する。
ただし **キーをそのまま BOOTH メッセージに貼ると履歴が残る**ので、
「メールでお送りしますのでメールアドレスをご教示ください」と返信し、メール送信を促す。

---

## 4. 発行済みキーの確認

### 履歴ファイル

```powershell
# 全履歴
Get-Content C:\Users\kyohei\ClipGift\scripts\.issued_keys.jsonl

# 直近 5 件
Get-Content C:\Users\kyohei\ClipGift\scripts\.issued_keys.jsonl | Select-Object -Last 5

# 特定購入者の履歴
Get-Content C:\Users\kyohei\ClipGift\scripts\.issued_keys.jsonl | Select-String "buyer@example.com"
```

### サーバー側で個別キーを確認したい場合

Cloudflare ダッシュボード → Workers KV → LICENSES ネームスペース で `key:CGFT-...` キーを直接見る。

---

## 5. キー失効（返金 / 不正検出時）

```powershell
python scripts/revoke_license.py --key "CGFT-STD-XXXX-XXXX-XXXX" --reason "BOOTH refund"
```

失効後、購入者がそのキーで認証しようとすると `key_revoked` エラーで弾かれる。

---

## 6. トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| `エラー: 環境変数 CLIPGIFT_ADMIN_TOKEN を設定してください` | 環境変数未設定 | 「0. 事前準備」を実行 |
| `接続エラー: ...` | サーバーダウン or ネット不通 | `curl /health` で確認 |
| `HTTP 401 unauthorized` | 管理トークン不一致 | wrangler 側のトークンと環境変数を再同期 |
| `HTTP 500` | Workers 側のバグ | Cloudflare ダッシュボードのログで詳細確認 |
| キーがメーラーで折り返される | 改行が混入 | コピペ時に末尾改行を含めない |

---

## 7. 運用に関するメモ

### 自動化の余地（将来）

- BOOTH の出荷 webhook を Cloudflare Workers で受け取って `/admin/issue` を自動呼び出し
- メール送信を SendGrid / Resend で自動化（個人情報を絞った設計に注意）

ただし販売開始直後は手動運用で十分（受注ペースが速くなったら検討）。

### 個人情報の取り扱い

- `scripts/.issued_keys.jsonl` と `scripts/issued_keys/*.txt` は **購入者のメールアドレス** を含む。
- これらは `.gitignore` 済み。**絶対にコミット / push しない**。
- バックアップは暗号化された外部ストレージへ（OneDrive 個人保管 等）。
- 90 日経過したエントリは年次で削除を検討（過剰保管しない）。

### 月次のヘルスチェック

- 月初に `/health` を叩いて生存確認
- 発行件数 / プラン構成を集計（kyohei さん月報用）
  ```powershell
  Get-Content scripts/.issued_keys.jsonl | ConvertFrom-Json | Group-Object plan | Format-Table Name,Count
  ```
