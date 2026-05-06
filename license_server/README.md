# clipgift-license

クリップギフト ライセンス認証サーバー (Cloudflare Workers + Workers KV)

## セットアップ

```powershell
cd license_server
npm install
```

### Cloudflare アカウント準備

1. https://dash.cloudflare.com/ で無料アカウント作成
2. `npx wrangler login` でブラウザ認証

### KV ネームスペース作成

```powershell
# 本番用
npx wrangler kv:namespace create LICENSES
# プレビュー用（dev 時）
npx wrangler kv:namespace create LICENSES --preview
```

返ってきた ID を `wrangler.toml` の `id` / `preview_id` に貼り付け。

### シークレット登録

```powershell
# HMAC 署名用（32 byte 以上のランダム文字列）
npx wrangler secret put HMAC_SECRET
# 例: openssl rand -hex 32

# 管理用 Bearer トークン（手動キー発行 / 失効用）
npx wrangler secret put ADMIN_BEARER_TOKEN
```

## 開発・デプロイ

```powershell
# ローカル開発（プレビュー KV を使用）
npm run dev

# 本番デプロイ
npm run deploy
```

デプロイ後の URL: `https://clipgift-license.<account>.workers.dev`

## API

| Method | Path | 用途 | 認証 |
|---|---|---|---|
| POST | `/activate` | 初回アクティベーション | なし |
| POST | `/verify` | 30 日ハートビート | なし |
| POST | `/deactivate` | マシン解放 | なし |
| POST | `/admin/issue` | 手動キー発行 | Bearer |
| POST | `/admin/revoke` | キー失効 | Bearer |
| GET | `/health` | ヘルスチェック | なし |

詳細仕様は `../.company/engineering/docs/license-system.md` 参照。

## 動作確認

```powershell
# ヘルスチェック
curl https://clipgift-license.<account>.workers.dev/health

# 手動キー発行（管理者）
curl -X POST https://clipgift-license.<account>.workers.dev/admin/issue `
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"plan":"std","buyer_email":"test@example.com","reason":"test"}'
```

## ファイル構成

```
license_server/
├── wrangler.toml              ← Cloudflare Workers 設定
├── package.json
├── tsconfig.json
└── src/
    ├── index.ts               ← エントリ + ルーティング
    ├── types.ts               ← 型定義
    ├── keys.ts                ← キー生成・HMAC 署名
    ├── utils.ts               ← 共通ヘルパー
    └── handlers/
        ├── activate.ts
        ├── verify.ts
        ├── deactivate.ts
        └── admin.ts           ← issue / revoke
```

各ファイル 300 行以内（プロチェックmd.txt ルール 1 遵守）。
