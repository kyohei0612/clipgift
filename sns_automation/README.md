# sns_automation

クリップギフト販売プロジェクトの SNS 自動投稿システム。

## 概要

X / Threads / Bluesky の 3 媒体に対し、テンプレートベースで定期投稿 + note 投下時の連動告知を行う。
詳細設計: [`../.company/engineering/docs/sns-auto-post-system.md`](../.company/engineering/docs/sns-auto-post-system.md)

## 構成

```
sns_automation/
├── env_loader.py             ← .env 読込ユーティリティ
├── verify_apis.py            ← 3 媒体疎通確認スクリプト
├── platforms/
│   ├── threads_poster.py     ← Threads 投稿実装
│   ├── x_poster.py           ← X 投稿実装
│   └── bluesky_poster.py     ← Bluesky 投稿実装
├── requirements.txt
└── README.md（このファイル）
```

## セットアップ

### 1. 依存ライブラリ

```powershell
pip install -r sns_automation/requirements.txt
```

### 2. 認証情報

`sns_automation/.env` を編集（`sns_automation/.env.example` をコピーして埋める）。

```env
THREADS_APP_ID=...
THREADS_APP_SECRET=...
THREADS_ACCESS_TOKEN=...
THREADS_USER_ID=...

X_CONSUMER_KEY=...
X_CONSUMER_SECRET=...
X_ACCESS_TOKEN=...
X_ACCESS_TOKEN_SECRET=...

BLUESKY_HANDLE=xxx.bsky.social
BLUESKY_APP_PASSWORD=...
```

⚠️ `.env` は絶対に Git にコミットしない（`.gitignore` 設定済）。

### 3. 疎通確認

プロジェクトルートで実行:

```powershell
python -m sns_automation.verify_apis
```

成功例:
```
[1/3] Threads   [OK] @0nenengineea  id=...
[2/3] X         [OK] @0nenengineea  id=...
[3/3] Bluesky   [OK] xxx.bsky.social  did=...
全媒体 OK
```

## 各媒体の投稿モジュール

### Threads

```python
from sns_automation.env_loader import load_env
from sns_automation.platforms import threads_poster

load_env()
post_id = threads_poster.post("テスト投稿")
# 画像付き
post_id = threads_poster.post("画像付き", image_url="https://example.com/x.jpg")
```

### X

```python
from sns_automation.platforms import x_poster
tweet_id = x_poster.post("テストツイート")
```

### Bluesky

```python
from sns_automation.platforms import bluesky_poster
uri = bluesky_poster.post("テスト投稿")
```

## 文字数上限（参考）

| 媒体 | 上限 | 単位 |
|---|---|---|
| Threads | 500 | 文字 |
| X | 280 | 半角換算（日本語は実質 140 文字） |
| Bluesky | 300 | grapheme |

## トラブルシューティング

### Threads: "Invalid OAuth 2.0 Access Token"
- `.env` のトークンに改行・空白が混入していないか確認
- 60 日経過で失効。Meta ダッシュボードから再発行

### X: 401 / 403
- API 申請の Tier を確認（Free tier は POST /2/tweets / GET /2/users/me のみ）
- OAuth 1.0a の 4 値（consumer key/secret + access token/secret）が揃っているか

### Bluesky: ログイン失敗
- App Password を使う（メイン PW は不可）
- ハンドルは `xxx.bsky.social` の完全形で

## 実装状況（2026-05-06 時点）

### 完了
- [x] `__init__.py` / `env_loader.py` / `verify_apis.py`
- [x] `platforms/threads_poster.py` / `x_poster.py` / `bluesky_poster.py`
- [x] `poster.py` メインエントリ（dry-run / 変数指定 / プラットフォーム絞込み対応）
- [x] `config/templates.yaml`（X/Threads/Bluesky 全カテゴリのテンプレ収録）
- [x] `config/schedule.yaml`（4 cron + 2 手動スケジュール定義）
- [x] `utils/template_engine.py`（変数置換 + ランダム選択 + 履歴回避 + 変数フィルタ）
- [x] `utils/scheduler.py`（schedule.yaml 解決）
- [x] `utils/logger.py`（共通ロギング）
- [x] `.github/workflows/sns_post.yml`（GitHub Actions、Secrets 9 種参照）
- [x] 全スケジュール dry-run 動作確認

### kyohei さん側で残っているタスク
- [ ] **Threads トークン再発行**（漏洩対策、Meta ダッシュボードで再生成 → `.env` 更新）
- [ ] **GitHub Secrets 登録**（`.env` の 10 値を GitHub 側にコピー）
  - リポジトリ Settings → Secrets and variables → Actions → New repository secret
  - 必要なキー: `THREADS_APP_ID`, `THREADS_APP_SECRET`, `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID`, `X_CONSUMER_KEY`, `X_CONSUMER_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`, `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD`
- [ ] **Bluesky ハンドル統一**（`0nenenjinia` → `0nenengineea`、任意）
- [ ] **本物の note 公開時に `note_announce` 実行テスト**（手動 workflow_dispatch で）
- [ ] **本投稿テスト**（`--dry-run` 外して実投稿、平日朝 cron で初回確認）

### 既知の制約
- `note_announce` 用テンプレで `{{title}}` `{{summary}}` `{{url}}` のいずれか不足時は変数フィルタが除外、それでも残る場合は投稿スキップ（リテラル `{{var}}` が SNS に出るのを防ぐ保険）
- `weekend_evening` の Threads は `longform` カテゴリで `{{url}}` 必須 → cron 経由では note URL を渡せないため自動スキップされる。手動 `workflow_dispatch` で URL を渡す or `longform` に `{{url}}` 不要のテンプレを追加すること
- BOOTH URL は出店後に `--var booth_url=...` または schedule 起動時に渡す必要あり

## CLI 使用例

```powershell
# 動作確認だけ（投稿しない）
python -m sns_automation.poster --schedule weekday_morning --dry-run

# 特定媒体だけ
python -m sns_automation.poster --schedule weekday_morning --only x --dry-run

# note 投下時の告知（変数指定）
python -m sns_automation.poster --schedule note_announce `
    --var title="記事タイトル" `
    --var summary="記事の要約 1-2 行" `
    --var url="https://note.com/xxx/n/abc"

# 本番投稿（dry-run なし、自己責任で）
python -m sns_automation.poster --schedule weekday_morning
```

## ディレクトリ構成（完成版）

```
sns_automation/
├── __init__.py
├── env_loader.py
├── verify_apis.py
├── poster.py                    ← メインエントリ
├── config/
│   ├── templates.yaml           ← 投稿テンプレ
│   └── schedule.yaml            ← cron + カテゴリ
├── platforms/
│   ├── threads_poster.py
│   ├── x_poster.py
│   └── bluesky_poster.py
├── utils/
│   ├── logger.py
│   ├── template_engine.py
│   └── scheduler.py
├── requirements.txt
├── README.md
└── .history.json                ← gitignored、テンプレ使用履歴
```
