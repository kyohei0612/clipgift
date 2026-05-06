# SNS 自動投稿用 GitHub Secrets セットアップ手順

`.github/workflows/sns_post.yml` が利用する **8 つの Secret** を GitHub リポジトリに登録するための手順書。

未登録の場合、ワークフローは早期チェック step で **「❌ 必須 Secrets が未登録です」** を出して停止する。

---

## 全体の流れ

```
[各 SNS の Developer Console] → [トークン取得] → [GitHub Settings に登録] → [Workflow 再実行]
```

所要時間目安: 30〜60 分（X の Developer 申請が未承認の場合は別途）

---

## 必要な Secret 一覧

| # | Secret 名 | 用途 | 取得元 |
|---|-----------|------|--------|
| 1 | `THREADS_APP_ID` | Threads アプリ ID | Meta for Developers |
| 2 | `THREADS_APP_SECRET` | Threads アプリシークレット | Meta for Developers |
| 3 | `THREADS_ACCESS_TOKEN` | Threads ユーザートークン（長期） | Graph API Explorer |
| 4 | `THREADS_USER_ID` | Threads ユーザー数値 ID | Graph API |
| 5 | `X_CONSUMER_KEY` | X (Twitter) API キー | X Developer Portal |
| 6 | `X_CONSUMER_SECRET` | X API シークレット | X Developer Portal |
| 7 | `X_ACCESS_TOKEN` | X アクセストークン | X Developer Portal |
| 8 | `X_ACCESS_TOKEN_SECRET` | X アクセストークンシークレット | X Developer Portal |
| 9 | `BLUESKY_HANDLE` | Bluesky のハンドル（`xxx.bsky.social`） | Bluesky 設定画面 |
| 10 | `BLUESKY_APP_PASSWORD` | Bluesky アプリパスワード | Bluesky 設定画面 |

> 注: ワークフロー側で必須としているのは上記のうち **#3, #4, #5–#8, #9, #10 の 8 つ**。
> `THREADS_APP_ID` と `THREADS_APP_SECRET` は `env` で渡しているがチェック対象外（トークン更新時のみ使用）。

---

## 1. GitHub に Secret を登録する操作（共通）

1. `https://github.com/kyohei0612/clipgift` を開く
2. 上部タブ **Settings** をクリック
3. 左メニュー **Secrets and variables → Actions** を開く
4. **New repository secret** ボタンを押す
5. **Name** に Secret 名（例: `X_CONSUMER_KEY`）を、**Secret** に値を貼り付けて保存
6. 一覧に追加されたら OK（中身は再表示できないので、登録時に必ずバックアップを取ること）

> 値は **改行・前後空白・引用符を含めない**。コピペ時に末尾の改行が紛れ込まないよう注意。

---

## 2. X (Twitter) のトークン取得

### 前提

- X Developer アカウントが承認済みであること（無料枠 Free でも可）
- アプリ単位で **Read and write** 権限を有効にしてあること

### 手順

1. <https://developer.twitter.com/en/portal/dashboard> を開く
2. 対象 Project / App を選択
3. **Keys and tokens** タブを開く
4. **API Key and Secret** の **Regenerate** で新規生成 → 表示された値を控える
   - `API Key` → `X_CONSUMER_KEY`
   - `API Key Secret` → `X_CONSUMER_SECRET`
5. **Access Token and Secret** の **Regenerate** で生成
   - `Access Token` → `X_ACCESS_TOKEN`
   - `Access Token Secret` → `X_ACCESS_TOKEN_SECRET`
6. 4 つを上記「1. GitHub に Secret を登録する操作」で個別に登録

### よくある罠

- **権限変更後に Token 再生成を忘れる**: Read 権限のままのトークンで投稿すると 403 が返る
- **Free プランの月 1500 投稿上限**: cron で 1 日 4 回 × 30 日 = 120 投稿なので余裕

---

## 3. Threads のトークン取得

### 前提

- Meta for Developers アカウント
- Threads API アクセスを有効化したアプリが作成済み

### 手順

1. <https://developers.facebook.com/apps/> でアプリを開く
2. 左メニュー **Threads → API setup** を開く
3. **Generate access token** をクリック → ユーザートークン（短期）が表示される
4. 短期トークンを **長期トークン**（60 日）に交換:
   ```
   https://graph.threads.net/access_token
     ?grant_type=th_exchange_token
     &client_secret={THREADS_APP_SECRET}
     &access_token={短期トークン}
   ```
   レスポンスの `access_token` が長期トークン → これが `THREADS_ACCESS_TOKEN`
5. ユーザー ID を取得:
   ```
   https://graph.threads.net/me?fields=id&access_token={長期トークン}
   ```
   レスポンスの `id` → これが `THREADS_USER_ID`
6. GitHub Secret として登録

### 60 日経過時の更新

長期トークンは 60 日で期限切れ。期限内に再交換することで延長可能:

```
https://graph.threads.net/refresh_access_token
  ?grant_type=th_refresh_token
  &access_token={現行長期トークン}
```

カレンダーに「Threads トークン更新」を 50 日後にリマインダー登録推奨。

---

## 4. Bluesky のアプリパスワード取得

### 手順

1. Bluesky アプリ or <https://bsky.app/> にログイン
2. **Settings → App Passwords** を開く
3. **Add App Password** をクリック → 任意の名前（例: `clipgift-cron`）を入力
4. 生成された `xxxx-xxxx-xxxx-xxxx` 形式のパスワードを控える
5. GitHub Secret として登録:
   - `BLUESKY_HANDLE`: 自分のハンドル（例: `clipgift.bsky.social`）
   - `BLUESKY_APP_PASSWORD`: 上記で生成したアプリパスワード

### 注意

- **アカウント本体のパスワードを使ってはいけない**（アプリパスワード必須）
- アプリパスワードは一度しか表示されない。失くしたら再生成

---

## 5. 登録後の確認

1. GitHub リポジトリで **Actions** タブを開く
2. 左サイドバーから **SNS 自動投稿** ワークフローを選択
3. **Run workflow** ボタンで `dry_run: true` を指定して手動実行
4. **Verify required secrets** step が ✅ で通ることを確認
5. **Run poster** step が `--dry-run` モードで投稿フォーマットを表示すれば成功

実投稿テストは `dry_run: false` で同じ手順。

---

## 6. トラブルシューティング

| 症状 | 原因 | 対処 |
|------|------|------|
| `❌ 必須 Secrets が未登録です: X_CONSUMER_KEY` | Secret 名のタイポ | 大文字小文字・アンダースコア確認 |
| X で 401 Unauthorized | トークン失効 or 権限不足 | 上記 X 手順 4–5 でトークン再生成 |
| X で 403 Forbidden | アプリの権限が Read のみ | Developer Portal の App Settings で Read and write に変更 → トークン再生成 |
| Threads で `OAuthException` | 60 日経過でトークン失効 | `refresh_access_token` で更新 |
| Bluesky で `InvalidLogin` | アプリパスワード形式間違い | ハイフン区切り 4×4 形式（小文字英数）か確認 |

---

## メンテナンス TODO

- [ ] Threads 長期トークン更新（次回期限の 50 日前にリマインダー）
- [ ] X トークン年次ローテーション
- [ ] Bluesky アプリパスワードの権限見直し（必要最小権限）
