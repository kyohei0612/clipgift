# CLAUDE.md

Claude Code / AI アシスタント向けのプロジェクトコンテキスト。ユーザー向け情報は [README.md](README.md)、課題は [ISSUES.md](ISSUES.md) を参照。

---

## このプロジェクトは何か

Windows デスクトップで動く Flask 製のクリップ作成ツール。`python app.py` で `127.0.0.1:5000` にローカルサーバーを立て、ブラウザ UI から操作する構成。エンドユーザーは Inno Setup 製インストーラー経由で利用する。

---

## アーキテクチャ概要

```
[ブラウザ UI]
   │  fetch
   ▼
[Flask サーバー app.py]         ─▶ Flask ルート + プロセスオーケストレーション
   │
   ├─ paths.py                   BASE_DIR/BIN_DIR/LAST_FONT_FILE 等の定数
   ├─ chat_analyzer.py           チャット解析（純粋関数）
   ├─ font_manager.py            日本語フォント列挙＋last_font 保存
   ├─ system_utils.py            Python パス解決・一時ファイル掃除・起動回数
   ├─ YouTube DL 呼出 ───────▶ downloader.py  (pytubefix + requests)
   ├─ クリップ生成 ───────────▶ mp4inchatnagasi.py (subprocess で起動)
   └─ 自動更新 ───────────────▶ auto_update.py (GitHub raw 取得)
```

- **`mp4inchatnagasi.py` は `subprocess.Popen` で別プロセス起動される**（importして関数呼び出しではない）。
- 進捗は `temp_dir/progress.json` をファイル経由で共有。親プロセスが書き込み、UI が `/progress` でポーリング。
- 重い処理は `threading.Thread(daemon=True)` で非同期化。`/process_clips` は即 `200` を返し、実処理はスレッドで走る。

---

## 重要な落とし穴

### 1. Windows 固有の挙動
- **パスは `os.path.join` で統一**。`/` 直書きしない。
- **日本語ファイル名**が多数。`subprocess` に渡すときはエンコード注意。
- **Shift-JIS の CSV** が来る可能性あり（[app.py:487-492](app.py:487) で UTF-8 → SJIS フォールバック）。

### 2. グローバル状態とロック
- `processing_lock` — クリップ生成の多重起動防止
- `_state_lock` — 以下の共有状態を保護:
  - `_is_processing` — watchdog 無効化判定用フラグ
  - `current_process` — 実行中の `subprocess.Popen`
  - `current_clip_index` — UI 表示用
  - `cancel_flag` — キャンセル要求
- `_is_downloading` + `_is_downloading_lock` — ダウンロード中フラグ
- `_process_logs` + `_process_logs_lock` — 最新 200 行のログ

**ロック取得順序**: `processing_lock` → `_state_lock` の順を守る（デッドロック防止）。`_state_lock` は短時間のみ保持する（I/O や subprocess 呼び出しは外で行う）。

### 3. Watchdog の存在
- [app.py:406-429](app.py:406) に「ハートビート途絶検知 → サーバー終了」の watchdog スレッドがある
- **テスト時に `os._exit(0)` で突然終了することがある**ので、デバッグ中は `_heartbeat_watchdog` を一時無効化すると楽
- UI からの `/heartbeat` POST を 30 秒以上受けないと終了する

### 4. 2 つの UI ページ
- `/` → `templates/index.html` （755 行、シンプル解析画面）
- `/page2` → `templates/index2.html` （**2226 行、メイン画面**）
- ほとんどの作業は `index2.html` で行われる。静的アセットは `static/index2.{css,js}`

### 5. `app.py` の構成（P1 リファクタ後）
- ルーティング + プロセスオーケストレーション + watchdog のみ（約 780 行）
- チャット解析・フォント管理・一時ファイル掃除・Python パス解決は別モジュールに分離済み
- 編集時はルートを追加するか、既存ルート内の挙動を変更する程度なら app.py 単独で済む

### 6. 進捗ファイルの扱い
- `progress.json` は**一時ディレクトリ内**が正。`BASE_DIR` 直下に出来た場合は古い残骸なので削除（起動時に [app.py:1024-1030](app.py:1024) でクリーンアップ）。
- `dl_progress.json` はダウンロード専用、こちらは `BASE_DIR` 直下で OK。

### 7. 自動更新
- `version.json` のバージョン番号はセマンティック（`major.minor.patch`）
- パッチは `build_and_push.bat` が自動インクリメント
- `auto_update.py` の `EXCLUDE_FILES` に入っているファイルは更新スキップされる（特に `bin/ffmpeg.exe` など大きいもの、`server_start_count.txt` などローカル状態）
- **`auto_update.py` 自身の編集は慎重に**。自己更新で壊れると復旧手段が `.bak` 手動リネームしかない。

### 8. ffmpeg の参照経路が 2 つ
- `bin/ffmpeg.exe`（インストーラー経由）
- `imageio_ffmpeg.get_ffmpeg_exe()`（pip 経由）
- どちらも import されているので、**使っている側を確認してから変更**すること

---

## ビルド・リリースフロー

`build_and_push.bat` は以下を一括実行する:

1. `version.json` のパッチバージョンを `+1`
2. `git add -A && git commit -m "update" && git push origin main`
3. Inno Setup (`setup.iss`) でインストーラー生成

**コミットメッセージはすべて `"update"`**（本人の運用スタイル。`git log` で履歴が追いにくいので、変更内容は diff で確認する）。

---

## テスト・動作確認

- 単体テストは現状なし
- `backtest_runner.py` がチャット解析ロジックのバックテスト（クリップ検出精度検証）
- 手動確認が中心：`python app.py` → ブラウザで `index2.html` を操作

---

## コードスタイル

- コメント・log は **日本語**、絵文字 (`🎯`, `▶`, `✅`, `🛑` など) を進捗表示に多用
- 関数名は英語、ファイル名はローマ字と英語混在（例: `mp4inchatnagasi.py`）
- `app.py` は `logger = logging.getLogger(__name__)` を使用。他のモジュールも同様。`logging.basicConfig` は `app.py` で一回だけ設定
- `mp4inchatnagasi.py` と `downloader.py` は **subprocess として起動される**ため、`print()` が残っている（親プロセスに stdout 経由でログを流す設計）

---

## よくある作業

### ルートを追加したい
`app.py` の末尾付近（既存ルートの並びに追記）に `@app.route(...)` で追加。

### クリップ生成ロジックを変える
`mp4inchatnagasi.py` を編集。**別プロセスで起動**されるのでログは `subprocess` の stdout 経由で親に流れる。デコードエラーは `errors="replace"` で握り潰されている（ISSUES.md B-5）。

### チャット解析パラメータを調整
`app.py:55-150` の `analyze_chat_*` 関数群、または `backtest_runner.py` でパラメータを振って精度を確認。

### UI を変える
`templates/index2.html`（2226 行、HTML/CSS/JS 混在）、`static/index2.css`, `static/index2.js`。
