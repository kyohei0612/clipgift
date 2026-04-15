# クリップ作成ツール

配信アーカイブのチャットログからハイライトを検出し、クリップ動画を生成する Flask 製 Windows デスクトップツール。

- YouTube 配信の動画＋チャット自動ダウンロード
- キーワード / コメント密度によるハイライト自動検出
- クリップ切り出し＋コメントオーバーレイ付き MP4 生成

---

## 動作環境

- Windows 10 / 11
- Python 3.10+ 推奨（3.7 以上必須）
- ffmpeg / ffprobe（`bin/` に同梱想定、または `imageio-ffmpeg` から取得）
- audiowaveform.exe（波形生成用、`bin/` に同梱想定）

---

## セットアップ

### A. インストーラー経由（エンドユーザー向け）

`installer_output/YouTubeClipTool_Setup.exe` を実行。Python ランタイム・依存ライブラリ・`bin/` 配下のツール類がセットアップされる。

### B. 開発環境セットアップ

```bash
git clone https://github.com/kyohei0612/clipgift.git
cd clipgift

# 仮想環境（推奨）
python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

`bin/` 配下に以下を手動配置:

- `ffmpeg.exe`, `ffprobe.exe` — https://www.gyan.dev/ffmpeg/builds/ など
- `audiowaveform.exe` — https://github.com/bbc/audiowaveform/releases

---

## 起動

### 開発時
```bash
python app.py
```
→ http://127.0.0.1:5000 が自動で開く

### インストール後
`launcher.vbs` をダブルクリック。`pythonw.exe` でコンソールなし起動、10 秒後にブラウザを開く。

---

## 画面

| URL | 役割 |
|---|---|
| `/` (`templates/index.html`) | トップ・簡易解析 |
| `/page2` (`templates/index2.html`) | メイン作業画面（クリップ編集・生成） |

---

## 主な API エンドポイント

| メソッド | パス | 役割 |
|---|---|---|
| POST | `/analyze_chat_csv` | チャット CSV を解析しクリップ候補を返す |
| POST | `/extract_audio` | 指定範囲の音声を抽出（波形表示用） |
| POST | `/download-yt-video-chat` | YouTube の動画＋チャットをダウンロード |
| GET  | `/progress` | ダウンロード進捗を返す |
| POST | `/process_clips` | クリップを生成（動画切出＋コメント焼き込み） |
| POST | `/cancel_process` | 生成処理をキャンセル |
| GET  | `/get-fonts` | 使用可能フォント一覧 |
| GET  | `/check-update` | GitHub 最新バージョンを確認 |
| POST | `/start-update` | 自動更新を開始 |

---

## リリース・ビルド

`build_and_push.bat` で一気に実行:

1. `version.json` のパッチバージョンをインクリメント
2. `git add -A && git commit -m "update" && git push origin main`
3. Inno Setup (`setup.iss`) でインストーラーをビルド

出力: `installer_output/YouTubeClipTool_Setup.exe`

---

## 自動更新の仕組み

- `version.json` にローカルバージョン
- `auto_update.py` が GitHub (`kyohei0612/clipgift`) の `main` ブランチから最新を取得
- `/check-update` で差分確認、`/start-update` で更新実行
- `bin/ffmpeg.exe` など `EXCLUDE_FILES` は更新対象外

---

## ファイル構成

```
app.py                  メインサーバー（Flask・ルート・解析・クリップ処理）
downloader.py           YouTube 動画＋チャットダウンロード
mp4inchatnagasi.py      コメント流し MP4 生成（ffmpeg + Pillow）
auto_update.py          GitHub からの自動更新
backtest_runner.py      解析ロジックのバックテスト
launcher.vbs            pythonw.exe でサイレント起動
setup.iss               Inno Setup インストーラー定義
build_and_push.bat      バージョン更新→push→ビルドの一括スクリプト
templates/              HTML
static/                 JS / CSS / 画像
bin/                    ffmpeg, ffprobe, audiowaveform, フォント設定
version.json            ローカルバージョン
```

---

## 課題・改善計画

開発中の課題は [ISSUES.md](ISSUES.md) 参照。
