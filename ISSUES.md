# 課題一覧 (クリップ作成ツール)

Flask 製クリップ作成ツールの整理課題。コードレビューで洗い出した実在確認済みの項目のみ記載。

優先度の基準:
- 🔴 **高**: バグ / データ損失 / セキュリティ / 再現性のある不具合
- 🟡 **中**: UX 低下 / 保守困難 / 中程度のリスク
- 🟢 **低**: リファクタ / 将来への備え

---

## まず着手すべき TOP 5

| # | 優先度 | 課題 | 場所 | 状態 |
|---|---|---|---|---|
| 1 | 🔴 | アップロードファイルのサイズ・拡張子検証なし | [app.py](app.py) | ✅ 対応済 |
| 2 | 🔴 | `processing_lock` の acquire/release が try/finally で管理されていない | [app.py](app.py) | ✅ 対応済 |
| 3 | 🔴 | README / requirements.txt / CLAUDE.md 欠落 | リポジトリ全体 | ✅ 対応済 |
| 4 | 🟡 | `app.py` 1042行が責務過多 → 4モジュールに分割（784行に圧縮） | [app.py](app.py) | ✅ 対応済 |
| 5 | 🟡 | `print` 散在、`logging` 未活用 → app.py と新規モジュールで logger に統一 | 全体 | ✅ 対応済 |

## P1 で完了した追加項目

- **B-3 グローバル状態の保護**: `_cancel_flag_lock` を `_state_lock` にリネームし、`current_process` / `current_clip_index` / `cancel_flag` / `_is_processing` をすべて同一ロック下で操作するように変更。
- **B-4 watchdog の TOCTOU 修正**: `processing_lock.acquire(non_blocking) + 即 release` の不自然な書き方を廃止。`_is_processing` フラグを `_state_lock` 下で原子的にチェックするように変更。

## P2 後フォロー修正（コードレビュー指摘の対応）

- **R-1 `current_process` race condition** — [app.py](app.py)
  - 指摘: `run_process` 内で `current_process = subprocess.Popen(...)` をロック外で代入していた。`cancel_process` がロック内で読むため、Popen 起動中のキャンセルで不完全な状態を見る可能性。
  - 対応: Popen をローカル変数 `proc` に受け、起動後に `_state_lock` 下で `current_process = proc` と `current_clip_index = idx` を更新。以降の I/O は `proc` 経由で行うので他スレッドの影響を受けない。

- **R-2 `/downloads` パス検証の論理ミス** — [app.py](app.py)
  - 指摘: `requested == downloads_dir` を許可してしまっていた（ディレクトリ自体を要求された場合に通る）。
  - 対応: `if not requested.startswith(downloads_dir + os.sep)` のみで判定するよう簡略化。

---

## バグ・潜在的な不具合

### 🔴 高
- **[B-1] アップロードファイルのサイズ・拡張子検証なし** — [app.py:795-828](app.py:795)
  - 現状: `request.files.get("video")` / `"chat"` をそのまま `video_file.save(video_path)` へ。サイズ上限・拡張子・magic number いずれも未チェック。
  - 影響: 数GB ファイルで OOM、非 MP4 で ffmpeg が落ちる、不正 CSV でパース例外。
  - 対応案: `app.config["MAX_CONTENT_LENGTH"]` で上限、`werkzeug.utils.secure_filename`、拡張子ホワイトリスト。

- **[B-2] `processing_lock` が try/finally で管理されていない** — [app.py:784-974](app.py:784)
  - 現状: 784 で acquire、802・967・974 の 3 箇所で手動 release。現時点では経路上 OK だが、例外経路追加時に release 漏れが起きやすい。
  - 影響: 解放漏れで「処理中です」が永続的に出続ける可能性。
  - 対応案: `with processing_lock:` またはコンテキストマネージャ化、release は finally に一本化。

### ✅ 対応済
- **[B-3] `current_process` / `current_clip_index` が無保護** — [app.py](app.py)
  - 対応: `_cancel_flag_lock` を `_state_lock` にリネーム、状態変数をすべて同一ロック下で操作するように修正。`cancel_process` は `current_process` をロック内でスナップショットしてから `poll()/terminate()` を呼ぶようにした。

- **[B-4] watchdog の `acquire → 即 release` が意図通り動かない** — [app.py](app.py)
  - 対応: `processing_lock` の試行 acquire を廃止し、`_is_processing` フラグを `_state_lock` 下で原子的にチェックする方式に変更。`/process_clips` 先頭でフラグを立て、`run_process.finally` と末尾 finally でクリアする。

### ✅ 対応済 (P2)
- **[B-5] サブプロセス出力のデコードエラー** — [app.py](app.py), [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 対応: `errors="replace"` を `"backslashreplace"` に変更（app.py 2箇所、mp4inchatnagasi.py 1箇所）。不正バイトが `\xNN` として残るので原因特定可能。

- **[B-6] CSV エンコード fallback** — [app.py](app.py)
  - 対応: 先に bytes として read してから `utf-8-sig → utf-8 → cp932 → shift_jis` の順に試行。最後の手段で `errors="replace"`。seek 不可ストリームでも安全。

---

## セキュリティ

### ✅ 対応済
- **[S-1] アップロード検証なし** → **B-1 と同一**（セキュリティ観点でも高優先）。

### ✅ 対応済 (P2)
- **[S-2] `/downloads/<filename>` の明示的検証** — [app.py](app.py)
  - 対応: `os.path.realpath` で正規化後、`downloads_dir` 配下に収まっているか明示的にチェック。逸脱したら 400 を返す。

- **[S-3] `subprocess` への日本語タイトル・特殊文字** — [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 対応: `sanitize_filename` を強化。Windows 予約語（CON, PRN ほか）回避、末尾 `.`/空白除去、長さ上限 100、空文字なら `untitled` にフォールバック。

- **[S-4] デバッグ出力でファイルパスが大量に print** — [app.py](app.py)
  - 対応: Q-2 の logging 化と同時に解決。app.py 内の `print()` は完全に排除済み（2026-05-06 確認）、すべて `logger.info/debug` に統一。`mp4inchatnagasi.py` / `downloader.py` の `print()` は subprocess 設計上意図的なもの（親プロセスへ stdout 経由でログを流すため）。

---

## コード品質・設計

### ✅ 対応済
- **[Q-1] `app.py` 1042 行が責務過多** — [app.py](app.py)
  - 対応: `paths.py`, `chat_analyzer.py`, `font_manager.py`, `system_utils.py` に分離。app.py は Flask ルート + プロセスオーケストレーション + watchdog のみに整理（784行に圧縮）。

- **[Q-2] `print` 散在、`logging` 未活用** — 全体
  - 対応: `logging.basicConfig(level=INFO, ...)` を app.py に配置し、全モジュールで `logger = logging.getLogger(__name__)` を使用。app.py 内の 18 箇所の print を `logger.info/debug/warning/error` に置換。

- **[Q-3] グローバル変数多数** — [app.py:400-404, 764-776](app.py:400)
  - `_last_heartbeat` / `_is_downloading` / `cancel_flag` / `current_process` / `_process_logs` / `_dl_logs_global`
  - 影響: テスト時の初期化漏れ、並行性バグの温床。
  - 対応案: 状態を `ProcessState`・`HeartbeatState` クラスに寄せる。

### 🟢 低
- **[Q-4] 命名の混在（英語関数名 / ローマ字ファイル名）** — [mp4inchatnagasi.py](mp4inchatnagasi.py) など
  - ファイル名 `mp4inchatnagasi.py` はローマ字、関数名は英語。統一基準なし。
  - 対応案: 新規は英語統一、既存は移行時に段階対応。

### ✅ 対応済 (P3)
- **[Q-5] マジックナンバー** — [mp4inchatnagasi.py](mp4inchatnagasi.py), [downloader.py](downloader.py)
  - 対応: mp4inchatnagasi.py 冒頭で `DEFAULT_FONTSIZE / COMMENT_DISPLAY_DURATION_SEC / VIDEO_EDGE_PADDING_PX / PROGRESS_LOG_EVERY / CLIP_END_CUT_MARGIN_SEC / CLIP_RETRY_COUNT / CLIP_RETRY_DELAY_SEC` を定義し、使用箇所を全て差し替え。downloader.py でも `HTML_FETCH_TIMEOUT_SEC / CHAT_FETCH_*` 一式を定数化し `0.08` を `CHAT_BATCH_SLEEP_SEC` に。

---

## パフォーマンス

### ✅ 対応済 (P3)
- **[P-1] YouTube チャット取得に再試行/バックオフなし** — [downloader.py](downloader.py)
  - 対応: `_fetch_chat` を指数バックオフ（1, 2, 4, 8, 16 秒、上限 30s）に刷新、429 時は `Retry-After` ヘッダを尊重。恒久エラー（400/401/403/404/410）は即失敗で無駄な再試行なし。`_fetch_html` 側にも 3 回再試行を追加。

### 🟢 低（未対応）
- **[P-2] 大量コメント時のメモリ・進捗更新粒度** — [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 現状: PNG 生成の進捗更新は `PROGRESS_LOG_EVERY=50` 件ごと、1000 件超えると UI が数秒無反応に見える可能性。
  - 対応案: `PROGRESS_LOG_EVERY` を 10〜20 に、`gc.collect()` をループ内に適切配置。実測必須。

---

## UX・動作の堅牢性

### ✅ 対応済 (P2)
- **[U-1] キャンセル反応が遅い** — [app.py](app.py)
  - 対応: `_terminate_then_kill(proc, timeout)` ヘルパーを追加。terminate → 1秒待ち → kill にエスカレート。`run_process` 内のキャンセルブレークと `cancel_process` ルートの両方で使用。

- **[U-2] 例外時のクリーンアップが不完全** — [app.py](app.py)
  - 対応: `run_process.finally` と `/process_clips` 末尾 finally の両方で `shutil.rmtree(temp_dir, ignore_errors=True)`。Thread に委譲済みなら run_process 側、未委譲なら末尾 finally 側で掃除する。

### 🟢 低
- **[U-3] 大容量ファイル（5GB+）の挙動未検証**
  - 対応案: `backtest_runner.py` に長時間配信ケースを追加。

---

## 運用・保守性

### 🔴 高
- **[M-1] ドキュメント類の欠落**
  - 無いもの: `README.md`, `CLAUDE.md`, `requirements.txt`, 機能一覧, セットアップ手順, ffmpeg 依存の明記。
  - 影響: 新規環境構築が不可能、AI アシスタント（Claude Code 含む）の作業効率低下。
  - 対応案:
    - `requirements.txt` を `pip freeze` から作成（バージョン固定）。
    - `README.md` に機能概要・セットアップ・起動方法。
    - `CLAUDE.md` にアーキテクチャ要点・よくある落とし穴。

### ✅ 対応済 (P2)
- **[M-2] 設定のハードコード** — [config.py](config.py)
  - 対応: `config.py` を新規作成し、ポート/アップロード上限/拡張子/ハートビート/watchdog/ログ上限/タイムアウト等を集約。`CLIPGEN_*` 環境変数で上書き可能。app.py の各所をリファレンスに置き換え。

- **[M-3] `auto_update` の失敗時ロールバック** — [auto_update.py](auto_update.py)
  - 対応: 更新開始時に `.update_in_progress` マーカーを置く。成功時に削除、失敗時は残す。`check_and_recover_from_failed_update()` を起動時 (app.py main) で呼び出し、マーカー残存時は自動的に `.bak` から復旧する。成功時は `_cleanup_backups()` で `.bak` を掃除。

---

## 依存・ビルド

### ✅ 対応済 (P3)
- **[D-1] ffmpeg / ffprobe 参照の統一** — [system_utils.py](system_utils.py)
  - 対応: `get_ffmpeg_path()` / `get_ffprobe_path()` を新設。優先順位は `bin/*.exe` → `imageio_ffmpeg`（ffmpeg のみ）→ `PATH`。app.py / downloader.py / mp4inchatnagasi.py の該当箇所を全て統一ヘルパー経由に差し替え。未使用になった `import imageio_ffmpeg` を app.py と downloader.py から削除。

- **[D-2] Python バージョン明示** — [pyproject.toml](pyproject.toml), [README.md](README.md), [requirements.txt](requirements.txt)
  - 対応: `pyproject.toml` を新規作成し `requires-python = ">=3.10"` を明記。README と requirements.txt の記述も「3.10 以上必須」に統一。

---

## 取り下げた誤認項目（記録用）

- ~~`app.py:805` json.loads 失敗時のデッドロック~~ — 外側 except で release されるため実害なし
- ~~`pathlib.Path` 未 import~~ — [app.py:157](app.py:157) で import 済み

---

## 進め方の提案

1. **P0（完了）**: M-1（ドキュメント）→ B-1/S-1（アップロード検証）→ B-2（ロック管理）
2. **P1（完了）**: Q-1（`app.py` 分割）→ Q-2（logging 化）→ B-3/B-4（競合の整理）
3. **P2（完了）**: U-1/U-2（UX 改善）、M-2/M-3（設定外部化と更新復旧）、B-5/B-6（CSV/デコード）、S-2/S-3（パス検証/sanitize）
4. **P3（完了）**: P-1（チャットDL 指数バックオフ）、D-1（ffmpeg 参照統一）、Q-5（マジックナンバー定数化）、D-2（Python バージョン明示）
5. **P3.1 後始末（完了）**: 🔴 setup.iss の [Files] に新規 Python モジュール 5 種＋ドキュメント 5 種を追加（リファクタ後に配布が壊れる問題）、MyAppVersion の自動同期 (`sync_setup_version.py`) を `build_and_push.bat` に組み込み
6. **残課題（任意）**: Q-3（グローバル変数の状態クラス化、🟡 中）、Q-4（命名統一・ファイルリネーム影響大）、P-2（進捗粒度・実測必須）、U-3（大容量 5GB+ 挙動検証）— Q-3 以外は 🟢 低

## 2026-05-06 ローンチ前 最終棚卸し

### 販売前の判断

| 項目 | 優先度 | 判断 | 理由 |
|---|---|---|---|
| Q-3 グローバル変数の状態クラス化 | 🟡 中 | **後回し** | 既に `_state_lock` で保護済み、現状の動作に支障なし。リファクタは購入者がついて運用が安定してから |
| Q-4 命名統一 | 🟢 低 | **後回し** | ファイルリネームで影響範囲大、本名 ch 9k 登録で実証済みの命名なので壊さない |
| P-2 進捗粒度 | 🟢 低 | **要望次第** | UI レスポンスのユーザー報告があれば対応 |
| U-3 大容量 5GB+ 挙動 | 🟢 低 | **販売後の継続課題** | テスト環境の準備工数大、ユーザー実機の報告から判断 |

### ローンチ時の本体品質
- 🔴 高優先課題 **0 件**
- 🟡 中優先課題 1 件（Q-3、リファクタ系で動作支障なし）
- 🟢 低優先課題 3 件
- **販売開始可能な品質**（プロダクション稼働実績 = 本名 ch 2 年 9k 登録）

## 2026-08-03 全ファイル デバッグ棚卸し — 第1バッチ（app.py + コアモジュール）✅ 全件修正済

対象: `app.py` / `config.py` / `paths.py` / `system_utils.py` / `chat_analyzer.py` / `chat_filter.py` / `font_manager.py`

### 🔴 高

- **[DB-1] `%TEMP%` 配下の他アプリファイルを無差別削除していた** — [system_utils.py](system_utils.py)
  - 現象: 起動 2 回ごとに `*.json` / `*.tmp` / `*.mp3` / `*.wav` をワイルドカードで削除。他アプリの作業中ファイルを巻き込む。
  - 同時に、自分が作る `mp4chat_*` ディレクトリと `tmp*.mp4`（`mkstemp` の既定プレフィックス）は**対象外で消し残っていた**。掃除の必要なものだけを残す真逆の挙動。
  - 対応: 対象を `clipgen_*` / `mp4chat_*` のプレフィックス 2 種に限定。`extract_audio` の `mkstemp` にも `prefix="clipgen_"` を付けて掃除対象に含めた。

- **[DB-2] 失敗クリップの番号が 1 ズレ、進捗が 100% を超える** — [app.py](app.py)
  - 現象: `idx` は `enumerate(clips, 1)` で既に 1 始まりなのに更に `+1` していた。
    ①「クリップ1の失敗」が「クリップ2の失敗」と表示 ② `failed_clip_indices`（素の idx）と表示番号が不一致 ③ 最終クリップ失敗時に `(idx+1)/len*100` が 100 超（3 個中 3 個目で 133%）。
  - 対応: `+1` を除去。進捗は `min(99, ...)` で頭打ち（100 はループ後の完了ブロック専用。UI が `progress>=100` を「このクリップ完了」と解釈するため）。

### 🟡 中

- **[DB-3] 添付ファイルのサイズ検証がサーバー側に存在しなかった** — [app.py](app.py)
  - 現象: docstring とコメントには「最大 3 枚 / 各 5MB / 画像のみ」とあるが、実装は枚数と `content_type` のみ。5MB 制限はクライアント（`index2.html` / `index.html`）にしか無く、`fetch` を直接叩けば無制限に送れた。
  - 対応: `content_base64` の長さからデコード後サイズを算出して検証。上限は `config.ATTACHMENT_MAX_COUNT` / `ATTACHMENT_MAX_BYTES` に集約（クライアント側の値と揃える）。

- **[DB-4] `extract_audio` に timeout が無く、一時ファイルもリークしていた** — [app.py](app.py)
  - 現象: ① `subprocess.run` に `timeout` 無し → ffmpeg ハングで Flask のリクエストスレッドを永久占有 ② `returncode != 0` の return 経路で入力動画を削除しておらず、失敗のたび数 GB が `%TEMP%` に残留 ③ キャッシュキーが「ファイル名 + 範囲」の md5 のみで、同名別内容の動画に**別動画の音声が返る**。
  - 対応: `config.FFMPEG_TIMEOUT_SEC`（既定 600 秒）を指定し 504 を返す。入力動画の削除を `finally` に移動。キャッシュキーにファイルサイズを追加。失敗/タイムアウト時は中途半端な mp3 も削除。

- **[DB-5] `/progress` `/get-progress-file` のパス検証が prefix マッチで甘い** — [app.py](app.py)
  - 現象: `abs_path.startswith(temp_dir)` は `C:\...\Temp` に対し `C:\...\TempEvil\x.json` を通す。`realpath` していないためリンク経由でも回避可能。加えて `/get-progress-file` は**検証した `abs_path` ではなく未正規化の `progress_path` を open** しており、検証対象と読むファイルがズレていた。
  - 対応: `_is_under()` ヘルパーを追加（両側 `realpath` + `os.sep` 境界）。open も検証済みパスに統一。

- **[DB-6] `/download-yt-video-chat` が Content-Type 次第で 500** — [app.py](app.py)
  - 現象: `request.get_json()` に `silent=True` が無く、JSON 以外のリクエストで例外 → 500。他ルートは `silent=True` を使っており不統一。
  - 対応: `get_json(silent=True) or {}` に統一。

- **[DB-7] `merge_clips` が引数を破壊、反転補正の順序も誤り** — [chat_analyzer.py](chat_analyzer.py)
  - 現象: 呼び出し元のリストを破壊的に `sort()`。さらに `start > end` の swap を **sort 後**に行っており、反転クリップが混ざるとソート順が崩れたままマージ判定に入る。
  - 対応: 入力を複製してから「反転補正 → sort」の順に変更。`hitLogs` も複製して共有参照を断つ。

- **[DB-8] 動画長クランプで `start > end` の逆転クリップが生成される** — [chat_analyzer.py](chat_analyzer.py)
  - 現象: `end` のみクランプし `start` は放置。さらに `video_duration_sec is not None` 判定のため、app.py が未指定時に渡す **`0` で全クリップの end が 0 に潰れていた**。
  - 対応: `start` も同時にクランプし、潰れたクリップは除外。判定を truthy に変更して `0` を「未指定」として扱う。

### 🟢 低

- **[DB-9] 解析が O(動画長 × ヒット数)** — [chat_analyzer.py](chat_analyzer.py): 1 秒刻みループの各回で `time_list` を全走査。`bisect` による二分探索に置換（旧実装と 200 パターンで一致確認済み）。併せて `time_list` を明示ソート（`max_time` が `time_list[-1]` 前提なのに、結合ログ等で時刻順の保証が無かった）。
- **[DB-10] 多重起動検知の誤検知** — [app.py](app.py): TCP 接続可否のみで判定していたため、5000 番を使う無関係なアプリがいると ClipGift が起動できず「既に稼働中」と誤案内。`/is_downloading` を叩いて ClipGift の応答かまで確認するように変更。
- **[DB-11] `_clipgift_restart.bat` の残骸** — [app.py](app.py): 生成後に削除されず `BASE_DIR` に残り続けていた。最終行の `del "%~f0"` で自己削除（`exit /b` を後置すると削除前に抜けるため置かない）。
- **[DB-12] 死んだ watchdog コード** — [app.py](app.py), [config.py](config.py), [CLAUDE.md](CLAUDE.md): 中身が `return` だけの `_heartbeat_watchdog()` と、未使用の `HEARTBEAT_TIMEOUT_SEC` / `WATCHDOG_START_DELAY_SEC` / `WATCHDOG_INTERVAL_SEC` を削除。CLAUDE.md の「Watchdog の存在」節が実態と逆だったので現状に合わせて書き換え。

### 検証

`python -m pytest tests` 120 件 pass に加え、以下を専用スクリプトで確認（全 PASS）:
新旧 `window_count` の一致（200 パターン）/ 未ソート CSV での解析一致 / `merge_clips` の非破壊性 / 反転クリップ補正 / `videoDuration=0` で全滅しないこと / `_is_under` の境界 4 ケース / base64 サイズ算出（11 パターン）/ キャッシュキー分離 / cleanup が実際に glob するパターンと他アプリ `.json` の生存 / 修正した 4 ルートの HTTP 応答。

---

## 2026-08-03 全ファイル デバッグ棚卸し — 第2バッチ その1（mp4inchatnagasi.py）✅ 全件修正済

### 🔴 高

- **[DB-13] リトライ中に `progress=-1` を書いて UI が死ぬ** — [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 現象: `gen_clip` の except と `main()` のリトライ節が、失敗のたびに `progress=-1` を書いていた。UI の**両方**のポーリング経路（[static/index2.js](static/index2.js) の `pollProgress` の `data.progress === -1` と、解析画面ループの `pData.progress < 0`）は、これを見た瞬間にポーリングを打ち切って「エラー終了」扱いにする。
  - 影響: 1 回目の試行で失敗すると、**リトライ（最大 3 回）が成功しても UI はエラー表示のまま二度と更新されない**。後続クリップの進捗も一切出なくなる。
  - 対応: リトライ余地がある間は `progress=0` +「再試行します」メッセージに変更。最終失敗時も `-1` を書かず「失敗（スキップして続行）」に変更し、最終状態の決定権は親（app.py の `run_process`）に一本化。子は exit code 1 で異常を伝える。ファイル全体から `-1` 書き込みが消えたことを検証済み。

- **[DB-14] 低解像度動画でコメントが 1 件も出ず、ログも出ない** — [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 現象: `candidates = range(min_y, max_y + 1, 70)` が空になると `y=None` で全コメントを `continue` で捨てていた。ログは一切なし。実測で **160x120 の動画では 0 件**（320x240 + fontsize 100 でも 1 レーンのみ）。
  - 対応: 候補が空なら最低 1 レーンにフォールバックして警告を出力。加えて「表示枠が空かず除外 N 件 / フォント未対応で除外 N 件」の内訳を必ずログに出すようにした。実動画で 0 件 → 1 件に改善することを確認。

### 🟡 中

- **[DB-15] 選択可能なフォントの一部で文字の下端が切れる** — [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 現象: `create_text_image` が `draw.text((pad_x//2, pad_y//2), ...)` と固定位置に描いていたが、`textbbox` の原点はフォントのアセント分ずれる（`bbox[1]`）。キャンバス高が `text_h + pad_y` しかないため `bbox[1] > 20` のフォントで下端がはみ出す。
  - 実測: UI に出る **30 フォント中 4 つ**（Noto Serif JP 11px / はちまるポップ 16px / モッチーポップ One 5px / レゲエ One 9px）が fontsize=100 で切れていた。システムフォント全体では 158 中 63 件。
  - 対応: 描画原点を `pad/2 - bbox[0]`, `pad/2 - bbox[1]` に補正。30 フォント × 3 サイズ = 90 組で切断ゼロを確認。

- **[DB-16] 無音動画でクリップ生成が失敗する** — [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 現象: `-map 0:a` は音声ストリームが無いと `Stream map '0:a' matches no streams` で ffmpeg が失敗。音声トラックを持たない録画で生成不能。
  - 対応: 両経路とも `-map 0:a?`（`?` = 無ければ省略）に変更。音声あり / 音声なしの実動画 E2E で両方成功を確認。

- **[DB-17] `get_video_info` が ffprobe 失敗で例外死 / fps 0 除算** — [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 現象: `json.loads(result.stdout)` が無防備で、ffprobe 失敗時の空 stdout で `JSONDecodeError` → 末尾のフォールバック `(1920,1080,30)` に到達できない。さらに `r_frame_rate` が `"0/0"` を返すコンテナで `ZeroDivisionError`。
  - 対応: returncode / 空出力 / JSON 例外 / `den==0` をすべてガードし、既定値へフォールバック。壊れたファイル・存在しないファイルで検証済み。

- **[DB-18] エンコーダ検出をクリップ 1 本ごとに毎回実行** — [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 現象: `_VIDEO_ENCODER = _detect_encoder()` がモジュールトップレベルにあり、本スクリプトは**クリップ 1 本ごとに subprocess 起動**されるため毎回 ffmpeg を起動していた。実測 288ms/回（HW エンコーダ非搭載機は 3 回失敗するので約 1 秒）。timeout も無し。
  - 対応: `bin/video_encoder.json` にキャッシュ（ffmpeg 実体のサイズ+mtime をキーにして入れ替わりを検出）。probe に timeout を追加。実測 186ms → 8ms。
  - ⚠️ キャッシュは**マシン固有（GPU 構成依存）**なので [.gitignore](.gitignore) と `auto_update.EXCLUDE_FILES` の両方に追加した。commit されると開発機の `h264_nvenc` が全ユーザーに配布され、NVIDIA GPU の無い環境でクリップ生成が全滅する。

- **[DB-19] 未使用 import で起動が毎回 +310ms** — [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 現象: `from flask import Flask, request, jsonify, url_for` が完全に未使用のまま残っていた。クリップごとに subprocess 起動されるため、実測でクリップ 1 本あたり約 310ms を捨てていた。`import random` も未使用（乱数は `np.random`）。
  - 対応: 両方削除。AST で実 import を検査して確認。

- **[DB-20] filtergraph のパスエスケープ不足** — [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 現象: `_escape_movie_path` が `:` しかエスケープしない。一時ディレクトリは `%TEMP%`（= `C:\Users\<ユーザー名>\...`）配下なので、**ユーザー名に `'` や `,` を含むアカウント**では filter graph が壊れてクリップ生成が丸ごと失敗する。
  - 対応: `:` `'` `,` `;` `[` `]` をまとめてエスケープ。

- **[DB-21] `read_comments` が CSV の並び順に依存して無言で欠落** — [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 現象: レンジのカーソルを前方へ進める実装で「CSV が時刻昇順」が前提。downloader.py の出力は整列済みだが、ユーザーが用意した CSV や結合ログでは時刻が前後し、カーソル通過後の行が捨てられる。しかも「Comments loaded: N 件」としか出ないので気付けない。
  - 対応: 並び順に依存しない判定に置換（main 側でどのみち再フィルタされるので実害なし）。加えて、ヘッダー名不一致（`time`/`comment` 列が無い）で全行落ちるケースを検出して警告を出すようにした。

### 🟢 低

- **[DB-22] デッドコード撤去** — `CommentTrack.find_y()`（gen_clip 内に同じロジックがインライン実装されており未呼び出し）、`build_ffmpeg_overlay_filter()`（`movie=` 方式に置換済みで未呼び出し、0 除算ガードも無し）、`safe_write_progress` の未使用変数 `dir_name` を削除。`--is-last` は未使用だが外部起動時に落ちないよう `argparse.SUPPRESS` で受け口のみ残置。

### 検証

`pytest` 120 件 pass に加え、専用スクリプトで 9 分類を確認（全 PASS）。さらに**実動画での E2E**:
音声あり（映像+音声ストリーム生成）/ 音声なし（映像のみ、`0:a?` で成功）/ 320x240 + fontsize100（内訳ログ出力）/ 160x120（レーン フォールバック発動、旧実装なら 0 件 → 1 件生成）。

---

## 2026-08-03 全ファイル デバッグ棚卸し — 第2バッチ その2（downloader.py / auto_update.py）✅ 全件修正済

### 🔴 高

- **[DB-23] チャット CSV を手書きしていて、カンマ入りコメントが途中で切れる** — [downloader.py](downloader.py)
  - 現象: `f.write(f"{t},{author},{msg}\n")` でエスケープ無しに書いていた。コメントに半角カンマが入ると列が増え、読み手（mp4inchatnagasi の `DictReader` / app.py の `row[2]`）が**カンマ以降を丸ごと失う**。
  - 実測: `え,まって,今の何` → `え` に切り詰め。クリップ検出のキーワード判定にも、動画に流れるコメント本文にも影響。後段の「時間順ソート」が `csv.writer` で書き戻すため、列がずれたまま固定される。
  - 対応: 書き込みを `csv_module.writer` に統一（クォート付き）。実走テストでカンマ入り・引用符入りが往復で保たれることを確認。

- **[DB-24] 最後のバッチのコメントが丸ごと捨てられる** — [downloader.py](downloader.py)
  - 現象: `download_chat` のループで「動画長に到達したら break」の判定が**書き込みより前**にあった。到達を検知したバッチのメッセージは 1 件も保存されない。終盤ほど盛り上がる＝クリップ対象なので影響が大きい。
  - 併発: `duration` が HTML から取れず 0 になると `0/1000 >= 0` が即成立し、**チャットが 1 件も保存されないまま「✅ 完了」**になっていた。
  - 対応: 書き込みを終了判定より前に移動。`duration > 0` を判定条件に追加。両方ともフェイク応答での実走テストで確認。

- **[DB-25] 既存フォルダを問答無用で削除してユーザーの素材を失う** — [downloader.py](downloader.py)
  - 現象: `download_with_pytubefix` が `if os.path.exists(title_folder): shutil.rmtree(title_folder)`。フォルダ名は**タイトル先頭 30 文字**なので、シリーズ物のように前半が同じ配信だと別動画でも衝突し、**前回 DL した動画・チャット・波形が消える**（未クリップの素材なら復旧不能）。
  - 対応: 完成品 `{title}.mp4` が存在する場合は削除せず連番フォルダ `タイトル(1)` に退避。中断された残骸（`video_temp.mp4` のみ等）なら従来どおり作り直す。

- **[DB-26] `.bak` の上書きでロールバック先が壊れる** — [auto_update.py](auto_update.py)
  - 現象: `_download_file` が毎回 `shutil.copy2(local_path, local_path + ".bak")` を実行。`.bak` が残っている状態＝**前回の更新が失敗して未ロールバック**なので、そこで再更新すると「壊れた更新後の内容」がバックアップになる。次回起動時の自動ロールバックで**壊れたファイルが復元される**（`.bak` 手動リネームでも復旧不能）。
  - 対応: `.bak` が既に存在する場合は上書きしない（最初に取った更新前の内容を守る）。2 回連続更新 → ロールバックで元の内容に戻ることを実走テストで確認。

### 🟡 中

- **[DB-27] 不正バージョン文字列で更新が永久に止まる** — [auto_update.py](auto_update.py)
  - 現象: `_version_tuple` が `int(x)` を直呼びしていたため、`version.json` に `2.0.1-beta` のような値が一度でも入ると `ValueError` → `check_update` が例外を握って `has_update: False` を返し、**以後ずっと更新できなくなる**。ユーザー側から原因が全く見えない。
  - 対応: 数値部分のみを取り出し、非数値は 0 として扱う実装に変更。

- **[DB-28] 更新の二重起動（TOCTOU）** — [auto_update.py](auto_update.py), [app.py](app.py)
  - 現象: `/start-update` は `status == "updating"` を見て弾いていたが、「状態を読む」→「スレッドを起動する」の間に別リクエストが割り込める。2 スレッドが同じファイルを同時に書き換え、`.bak` も壊れる。
  - 対応: `run_update_async()` 内のロックでチェックと状態遷移を原子的に実行し、bool を返すよう変更。app.py 側はその戻り値で判定。

- **[DB-29] auto_update の削除処理が依存/成果物ディレクトリを走査・全削除する** — [auto_update.py](auto_update.py)
  - 現象: 「GitHub にないローカルファイルを削除」の `os.walk` が `node_modules` / `src-tauri/target` / `installer_output` / `.venv` / `.wrangler` に降りていく。これらは `.gitignore` 済み＝GitHub のファイル一覧に載らないため、**中身が全削除対象**になる（`node_modules` は数万ファイル、`target` は 100MB+）。
  - 対応: `skip_dirs` に上記を追加。

- **[DB-30] 壊れた `.py` を書き込みうる** — [auto_update.py](auto_update.py)
  - 現象: 構文チェックが `data.decode("utf-8", errors="replace")` で行われており、不正バイトが U+FFFD に化けて `compile` を通過 → 壊れたファイルをそのまま保存する可能性があった。
  - 対応: 厳密デコードに変更し、UTF-8 として不正なら破損として弾く。

- **[DB-31] `subprocess` の timeout 指定漏れ** — [downloader.py](downloader.py)
  - 現象: 映像+音声の ffmpeg 結合、波形用 wav 抽出、ffprobe、audiowaveform のいずれにも `timeout` が無い。ハングすると DL スレッドが永久に戻らず、UI は「ダウンロード中」のまま固まる。
  - 対応: `FFMPEG_MERGE_TIMEOUT_SEC` (3600) / `WAVEFORM_TIMEOUT_SEC` (1800) を定義して全箇所に付与。ファイル内の `subprocess.run` / `check_output` 全件に timeout があることを検証済み。

- **[DB-32] `ytInitialData` の解析失敗が生の例外になる** — [downloader.py](downloader.py)
  - 現象: `json.loads(yid_m.group(1))` が無防備。正規表現が非貪欲なので JSON の途中で切れることがあり、`JSONDecodeError` がそのままユーザーに出る。
  - 対応: 失敗時は `None` に倒し、既存の親切メッセージ（`ChatNotAvailableError`）経路へ合流。

- **[DB-33] 1 つの action に複数コメントがあると取りこぼす** — [downloader.py](downloader.py)
  - 現象: `_parse_messages` が `a["replayChatItemAction"].get("actions", [{}])[0]` と先頭のみ処理。2 個目以降が捨てられる。
  - 対応: 全 action をループ。3 件入りのレスポンスで全件取得を確認。

- **[DB-34] `safe_write_json` が失敗時に一時ファイルを残す** — [downloader.py](downloader.py)
  - 現象: `NamedTemporaryFile(delete=False)` の後で例外が出ると `tmpXXXX` が残る。出力先は `Downloads/<タイトル>/` 配下なのでユーザーの目に触れるゴミになる。
  - 対応: `finally` で確実に削除。

### 🟢 低

- **[DB-35] 取得バッチ上限に達しても無言** — [downloader.py](downloader.py): `for i in range(3000)` を抜けても「✅ 完了」としか出ず、取りこぼしに気付けなかった。上限到達時に警告を出すよう変更。
- **[DB-36] 全コメントを 1 件ずつ print** — [downloader.py](downloader.py): 数万件を stdout に流し、親（app.py）が `logger.info` で全件出力していた。20 バッチごとの集計ログで足りるので削除。
- **[DB-37] `get_remote_version` のキャッシュ無効化クエリが二重** — [auto_update.py](auto_update.py): 呼び出し側と `_fetch_url` の両方で `?t=` を付けて `?t=123&t=123` になっていた。呼び出し側を削除。

### 検証

`pytest` 120 件 pass。専用スクリプトで downloader 8 分類 / auto_update 6 分類を確認（全 PASS）。
第1バッチ・mp4inchatnagasi の検証スクリプトと実動画 E2E も再実行して全 PASS を維持。

---

## 2026-08-03 全ファイル デバッグ棚卸し — 第3バッチ（Twitch 系）✅ 全件修正済

対象: `twitch_chat.py` / `twitch_video.py` / `downloader_twitch.py`（+ `downloader.py` の受け渡し）

### 🔴 高

- **[DB-38] VOD タイトルにアポストロフィがあると動画結合が丸ごと失敗** — [twitch_video.py](twitch_video.py)
  - 現象: ffmpeg concat demuxer の filelist を `file '{パス}'` と書いていたが、パス中の `'` をエスケープしていない。セグメントは `<出力mp4>.segments/` 配下なので、**VOD タイトルに `'` が入るとパスに `'` が混入して結合が失敗**する。`downloader.sanitize_filename` は `'` を除去しないため実際に再現する（`Don't stop me now` → `Don't_stop_me_now`）。
  - 実測: エスケープ無しで ffmpeg が `No such file or directory` を返して失敗、`'\''` 形式のエスケープで成功。
  - 対応: `path.replace("'", "'\\''")` を適用。アポストロフィ入りパスでの実結合テストを追加。

- **[DB-39] セグメント DL / 結合が失敗すると数 GB の .ts が残り続ける** — [twitch_video.py](twitch_video.py)
  - 現象: `download_twitch_video_native` は**正常終了パスでしか** `shutil.rmtree(temp_folder)` を呼んでいない。失敗すると `Downloads/<タイトル>.mp4.segments/` に全セグメントが残る。`cleanup_temp_files_and_dirs` の対象プレフィックス（`clipgen_` / `mp4chat_`）にも該当せず、%TEMP% でもないので永久に残る。
  - 対応: `try/finally` で必ず削除。擬似的な DL 失敗を注入して残骸ゼロを確認。

- **[DB-40] Twitch で既存フォルダを問答無用に削除** — [downloader_twitch.py](downloader_twitch.py)
  - 現象: DB-25（YouTube 側）と同型のバグが Twitch 側に残っていた。`if os.path.exists(title_folder): shutil.rmtree(title_folder)`。同じ配信者が同じタイトルで配信していると、**前回 DL した動画・チャット・波形が消える**。
  - 対応: YouTube 側と同じ方針に統一（完成品が残っていれば連番フォルダへ退避、未完了の残骸だけ作り直す）。

- **[DB-41] Twitch では UI の画質指定が完全に無視される** — [downloader.py](downloader.py), [downloader_twitch.py](downloader_twitch.py)
  - 現象: `downloader.py` の Twitch 分岐が `download_video_and_chat_twitch(video_url, base_output_folder, progress_path)` と呼んでおり **`max_resolution` を渡していない**。受け側にも引数が無く、`download_twitch_video` は `max_height=1080` をハードコード。UI で 480p を選んでも常に 1080p で DL され、時間もディスクも余計に消費していた。
  - 対応: `downloader.py` → `download_video_and_chat_twitch` → `download_twitch_video` → `download_twitch_video_native` まで `max_resolution` を通した。差し替えテストで 480 が最終段まで届くことを確認。

### 🟡 中

- **[DB-42] bot 判定回避のヘッダーが 1 つも送られていなかった** — [twitch_chat.py](twitch_chat.py)
  - 現象: `_post_persisted_query` / `_fetch_integrity_token` / `fetch_twitch_video_title` の 3 か所がそれぞれ別のヘッダー dict を手組みしており、**`_build_browser_headers()` は一度も呼ばれない死んだ関数**だった。結果、モジュール冒頭で「bot 判定回避に必須」と明記されている `User-Agent` / `Accept-Language` / `Client-Session-Id` が実際には送信されていなかった（`TWITCH_USER_AGENT` 定数も未使用）。
  - 対応: 3 か所すべて `_build_browser_headers()` に一本化。
  - ⚠️ 実 Twitch VOD に対する通信テストは未実施（手元に検証用 VOD が無いため）。送るヘッダーが増える方向の変更なので退行リスクは低いが、次回 Twitch DL 時に動作確認したい。

- **[DB-43] `contentOffsetSeconds` が null だとチャット取得ごと落ちる** — [twitch_chat.py](twitch_chat.py)
  - 現象: `node.get("contentOffsetSeconds", 0)` はキーが存在して値が null の場合デフォルトが使われず None が返る → `int(None)` で TypeError。同じ関数の `last_sec` 側には `or 0` が付いているのに、ここだけ抜けていた。
  - 対応: `or 0` を追加。null 入りの edge で実確認。

- **[DB-44] タイトル取得失敗時に全 VOD が同じフォルダ名になりうる** — [twitch_chat.py](twitch_chat.py)
  - 現象: `sanitize_filename("")` は汎用フォールバック `"video"` を返すため、`sanitized or f"twitch_{video_id}"` の `or` が効かない。タイトルが取れない VOD がすべて `video/` フォルダに入り、互いを上書きしうる（DB-40 の保護が入るまでは削除にも繋がる）。
  - 対応: タイトルが空、または sanitize 結果が汎用名 `"video"` の場合は `twitch_{video_id}` を返す。

- **[DB-45] チャット取得ループに上限が無い** — [twitch_chat.py](twitch_chat.py)
  - 現象: `while True:` の終了条件は Twitch の応答依存（edges 空 / 連続新規ゼロ / エラー）のみ。想定外の応答が返り続けると `offset` が 1 秒ずつしか進まないままリクエストを打ち続ける。`downloader.py` 側には 3000 バッチの上限があるのに、こちらには無かった。
  - 対応: `MAX_BATCHES` を設け、到達時は警告して打ち切り（欠落の可能性も明示）。

- **[DB-46] `subprocess` の timeout 指定漏れ（Twitch 系）** — [downloader_twitch.py](downloader_twitch.py), [twitch_video.py](twitch_video.py)
  - 現象: DB-31 で downloader.py 側は直したが、Twitch 側の波形生成（ffmpeg / ffprobe / audiowaveform）とセグメント結合に timeout が無いまま残っていた。
  - 対応: `downloader.WAVEFORM_TIMEOUT_SEC` を共有し、`twitch_video` には `CONCAT_TIMEOUT_SEC` を新設。両ファイルの `subprocess` 全件に timeout があることを検証済み。

### 検証

`pytest` 120 件 pass。第3バッチ専用スクリプトで 9 分類（全 PASS）。特に **ffmpeg concat のアポストロフィ問題は実際に .ts を生成して結合まで実行**して確認。第1・第2バッチの検証スクリプトと実動画 E2E も再実行して全 PASS を維持。

---

## 2026-08-03 全ファイル デバッグ棚卸し — 第5バッチ（license_server / 稼働中のみ）✅ 修正済 ⚠️ 未デプロイ

対象: `index.ts` / `utils.ts` / `types.ts` / `handlers/support.ts` / `handlers/support_pending.ts` / `handlers/incoming_mail.ts` / `handlers/admin.ts`

**スキップ**: `handlers/stripe_webhook.ts`（`STRIPE_WEBHOOK_SECRET` 未設定で 503 = 休眠）、`handlers/activate.ts` / `verify.ts` / `deactivate.ts` / `keys.ts`（Phase 1 は `PHASE_1_HONOR_SYSTEM` で全バイパス = 休眠）。kyohei さん指示により休眠コードは対象外。

> ⚠️ **これらの修正は `wrangler deploy` するまで本番に反映されません。** デプロイは未実行（外向きの操作なので指示待ち）。

### 🔴 高

- **[DB-47] 承認メールのなりすましが可能（件名だけで「kyohei の承認」になる）** — [license_server/src/handlers/incoming_mail.ts](license_server/src/handlers/incoming_mail.ts)
  - 現象: `classifyMessage` は **`from` を引数で受け取りながら一切使っていなかった**。判定は件名のみ。外部の誰でも `support@clipgift.org` 宛に `Re: 【ClipGift 確認依頼】 <12桁hex>` という件名でメールを送れば `reply_to_secretary` トリガーが生成される。ローカル watcher はこれを「kyohei が承認した」と解釈して **ユーザーへの返信メールを自動送信**しうる。件名は誰でも自由に付けられるので実質ノーガード。
  - 対応: `isTrustedOperator()` を追加し、`reply_to_secretary` は送信元が運営アドレス（`SUPPORT_FORWARD_TO` / `SUPPORT_FORWARD_TO_REQUEST` / `SUPPORT_REPLY_TO`）と一致する場合のみに限定。表示名付き `A <a@b.com>` / 大文字小文字 / 前後空白を正規化して比較し、前方後方の付け足し（`xnekodori...` / `...@gmail.com.evil.example`）で擦り抜けないことをテストで確認。外部からの Re: は `ignore` にしたうえで警告ログを残す（転送はされるので人の目には触れる）。
  - 注: **エラー報告・ご要望の新規受付は従来どおり誰からでも受ける**（ユーザー起点なので塞ぐと運用が壊れる）。塞いだのは承認系のみ。

- **[DB-48] 自動受付メールが第三者への送信踏み台になり、Resend 日次枠を焼き切れる** — [license_server/src/handlers/support.ts](license_server/src/handlers/support.ts)
  - 現象: `/support/report` は公開エンドポイントで、`user_email` に指定された **任意のアドレス** へ ClipGift 名義の「受け付けました」メールを送る。IP レート制限（5 通/分）はあるが 1 日換算では桁違いに多く、**Resend Free の 100 通/日を数分で使い切れる**。枠が尽きると運営宛のエラー報告メール自体が送れなくなり、サポートが停止する。
  - （過去に H-2 で `user_comment` のエコーバックは廃止済みだが、**送信そのものは止まっていなかった**）
  - 対応: `consumeAckQuota()` で ack の日次全体上限（30 通）を設けた。上限到達時は ack のみスキップし、運営宛メール・KV 保存・trigger 生成は継続する（犠牲にする順序を「ack → 運営宛」に固定）。KV 障害時は送る側に倒して正規ユーザーへの通知を止めない。

### 🟡 中

- **[DB-49] キー発行済みなのに 500 を返し、二重発行を招く** — [license_server/src/handlers/admin.ts](license_server/src/handlers/admin.ts)
  - 現象: `key:` を KV に保存した**後**で購入者インデックス `email:{hash}` を `JSON.parse` しており、既存データが壊れていると例外 → 500。呼び出し側（`scripts/issue_license.py`）は失敗と判断して再実行するが、`order_id` なしの手動発行では重複チェックが効かないため **同一購入者にキーが二重発行される**。
  - 対応: インデックス更新全体を try/catch で包み、パース失敗・非配列は作り直す。補助データの破損で発行自体を失敗させない。

- **[DB-50] `handleAdminRevoke` の `JSON.parse` が無防備** — [license_server/src/handlers/admin.ts](license_server/src/handlers/admin.ts)
  - 現象: `handleAdminIssue` 側は M-6 で try/catch 済みなのに、失効側は素通しでレコード破損時に 500。**壊れたレコードのキーほど失効させたい**のに失効できない。
  - 対応: パース失敗でも `blacklist:` 登録（失効の実効部分）は必ず実行するよう分離。

- **[DB-51] 公開エンドポイントに入力長の上限が無い** — [license_server/src/handlers/support.ts](license_server/src/handlers/support.ts)
  - 現象: `error_log` / `user_comment` / `app_version` に長さ制限が無い。アプリ側（`error_reporter.py`）は 200 行に絞るが、この API は公開なので任意長を投げ込める。巨大 body をメール本文に組み立てると Worker のメモリ / CPU 上限に当たる。
  - 対応: `error_log` 10 万字 / `user_comment` 2000 字 / `app_version` 40 字で足切り（超過は明示して省略）。

### 🟢 低（未対応・記録のみ）

- **[DB-52] `/support/pending` の cheap path に取りこぼしがある** — [license_server/src/handlers/support_pending.ts](license_server/src/handlers/support_pending.ts)
  - watcher が LIST で受け取った後に処理へ失敗して `/support/processed` を呼ばないと、pending は残るのに marker は変わらないため、次回以降 `since === marker` で 0 件が返り続ける。**新しい受信が来るまで取り残される。**
  - Worker 側で塞ぐと毎回 LIST になり KV Free 枠（1000 lists/日）を圧迫するため、**watcher 側（`scripts/watch_support_http.py`）で「全件処理できたときだけ last_marker を保存する」のが正しい**。第6バッチで対応する。
- **[DB-53] レート制限が `x-forwarded-for` にフォールバックし、IP 不明時は素通し** — Cloudflare Workers では `cf-connecting-ip` が常に付くため実際には到達しない経路。記録のみ。
- **[DB-54] エラー報告 1 件で KV write が 5 回**（report / incoming / pending / queue_marker / rate）。Free 枠 1000 writes/日に対し余裕はあるが、報告が増えたら見直す。

### 検証

`npx tsc --noEmit` クリーン。`license_server/test/incoming_mail.test.ts` を新規作成し **14 件 pass**（テストは未整備だった。`package.json` には `vitest run` が定義済みだったので、そこに載せた）。
DB-47 は差分テストにしてある — **同じ件名**で送信元が運営なら `reply_to_secretary`、外部なら `ignore` になることを対にして検証しているので、ガードを外せば必ず赤くなる。

---

## 2026-08-03 全ファイル デバッグ棚卸し — 第6〜8バッチ ✅ 修正済

### 第6バッチ: support_center / watcher

- **[DB-55] 🔴 失敗したトリガーが永久に取り残される** — [scripts/watch_support_http.py](scripts/watch_support_http.py)
  - `last_marker = marker` を処理結果に関わらず更新していた。processed を送っていなくても marker が進むので、次回 poll は Workers の cheap path で 0 件。**失敗分は次の受信が来るまで再取得されない**（第5バッチ DB-52 の実体）。
  - 対応: 全件成功時のみ marker を進める。あわせて同一トリガーの再試行を 3 回で打ち切る（1 件ごとに Claude を起動するため、直らないトリガーがあると 5 秒おきに Claude を叩き続ける）。
- **[DB-56] 🔴 バックオフが一度も動いていなかった** — `consecutive_failures` を**インクリメントする行が存在しない**。常に 0 なので `> 5` は永遠に偽。原因は `_fetch_pending` が失敗時も `([], since)` を返し「失敗」と「新着なし」を区別できなかったこと。戻り値に `ok` を追加。
- **[DB-57] 🔴 watcher が黙って死ぬ** — `resp.json()` の `ValueError` を捕まえておらず main ループごと落ちる。タスクは logon/boot 起動のみなので落ちたら復帰しない。例外捕捉＋ループ全体の保護を追加。
- **[DB-58] 🟡 応答形式の検証なし** — dict/配列でない応答で `AttributeError`。
- **[DB-59] 🔴 `watch_support_mail.py` が存在しない関数を呼ぶ** — `claude_runner.run_analyze` / `run_execute` はどちらも未定義。全トリガーで `AttributeError` になるが広い `except Exception` に飲まれ「トリガー処理例外」とだけ記録される＝**動いているように見えて 100% 失敗**。現行 API に修正。旧 IMAP 経路である旨も明記。
- **[DB-60] 🟡 ログに生メアドが残る** — [support_center/reply_user.py](support_center/reply_user.py)。`mask_email` があるのに未使用で、`_short_email_label` は「短縮」を名乗りながら完全アドレスを返す。
- **[DB-61] 🟡 ライセンスキーのマスクがプランコード列挙** — [support_center/pii_masker.py](support_center/pii_masker.py)。現行キー（`CGFT-STD-...`）は一致するので**今は正しく動いている**が、`planCode()` が `SINGLE` を返すようになった瞬間にキーが素通しでサポートメールに載る。`[A-Z]{2,10}` に一般化。

### 第7バッチ: フロントエンド

- **[DB-62] 🔴 `static/index2.js` / `index2.css` が死んだ複製** — どのテンプレートからも読み込まれておらず、実体は `templates/index2.html` にインライン（2481 行目以降）。関数は 22 個中 21 個が重複し、稼働側だけが 50 個に育っていた。しかも `setup.iss` が `static\*` を再帰配布するため**全ユーザーに配られ auto_update で同期され続けていた**。片方だけ直しても何も起きない事故のもと（実際 DB-13 の調査でこの死んだ側を見かけた。稼働側にも同じ挙動があることを確認済みなので判断自体は正しかった）。**削除済み**（1436 行 + 443 行）。[CLAUDE.md](CLAUDE.md) の記述も実態に修正。
- **[DB-63] 🟡 波形ライブラリが CDN 依存でオフラインに弱い** — `peaks.js` を cdnjs から読み、`window.peaks.init()` はガードなし。ローカルで動くデスクトップアプリなのに、オフライン / CDN 遮断で `TypeError` になり呼び出し側の try/catch に握り潰され、**波形が出ない理由が画面に一切出ない**。ガードと説明表示を追加。
- **[DB-64] 🟡 クリップ名が innerHTML に直接埋め込まれている（9 箇所）** — `${clip.title}` はユーザーの自由入力。`A<B` のような題名でその行のレイアウトが壊れる。`escapeHtml()` を追加し全箇所＋`${e.message}` に適用。

### 第8バッチ: 依存 / ビルド / ネイティブ

- **[DB-65] 🔴 `.bak` 掃除がユーザーのファイルまで消す** — [auto_update.py](auto_update.py)
  - `_list_backups()` が BASE_DIR 配下の `.bak` を無差別に拾い、`_cleanup_backups()` が更新成功のたびに `os.remove` する。`.gitignore` に `*.bak` があるため追跡もされず**復元不能**。実際に `sns_automation/config/templates.yaml.v1.bak` が対象になっていた（`_is_excluded=True` なのに拾われていた）。rollback 側も原本を作り直してゴミを増やす。
  - 対応: 「除外対象でない」かつ「対応する原本が存在する」の 2 条件で自作 `.bak` に限定。
- **[DB-66] 🔴 `fontTools` が requirements.txt に無い** — [requirements.txt](requirements.txt)
  - 配布コードが使っている（`font_manager.get_font_japanese_name` / `mp4inchatnagasi.can_render_text`）。`setup.iss` の pip には元から入っていたが requirements.txt に漏れており、**`auto_update` は requirements.txt を見て pip 同期するのでこの経路でだけ入らない**。`pip install -r requirements.txt` で作った環境ではフォント一覧が 0 件になる（例外を握り潰すので原因が見えない）。追加。
- **[DB-67] 🟡 `pywebview` が死んだ依存** — launcher_window.py は v2.0.0 で Tauri に置換され `setup.iss` にも含まれていないのに、requirements.txt に残っている。auto_update の pip 同期で全ユーザーに不要なパッケージが入る。コメントで明示（削除は任意）。
- **[DB-68] 🟡 Tauri: Flask 起動失敗時に真っ白なウィンドウが残る** — [src-tauri/src/lib.rs](src-tauri/src/lib.rs)。`wait_for_server(30)` が false でもウィンドウを作り、監視ループにも入らないため終了もしない。案内文を表示するようにした。
- **[DB-69] 🟡 Tauri: 監視のコメントと実装が不一致** — コメントは「3 回連続で落ちたら終了」だが実装は `misses >= 2` かつ間隔 150ms ＝ **300ms で終了**。クリップ生成中に接続が詰まると誤終了しうる。500ms × 6 回（約 3 秒）に変更。`cargo build --release` で確認済み。

### 検証（全バッチ通し）

- `pytest` 120 pass / 専用スクリプト 8 本すべて ALL PASS / 実動画 E2E PASS
- **配布物の完全性検査**（`setup.iss` の `[Files]` を解析 → エントリポイントから import を再帰追跡）: **配布漏れゼロ**
- `license_server`: `tsc --noEmit` クリーン、vitest 14 pass
- Tauri: `cargo build --release` 成功
- フロント: 実際にアプリを起動しブラウザで `/page2` を描画、`escapeHtml` の動作（`<b>` が 0 個）と波形ガード（`window.peaks` を消して模擬）を実機確認

---

## 2026-08-03 ユーザー報告からの修正（v2.0.15）

- **[DB-70] 🔴 キーワードをスペース区切りで入れるとクリップが 0 本になる** — [app.py](app.py), [templates/index2.html](templates/index2.html)
  - 報告: 「`ｗ` だけ → 出る / `草` だけ → 出る / `ｗ 草` → 出ない」
  - 原因: `keywords_str.split(",")` と**カンマだけ**で分割していた。`ｗ 草` は分割されず
    **「w 草」という 1 つの語**として扱われ、その文字列を含むコメントは 0 件なので 0 本になる。
    UI のラベルが「キーワード（例：「草」「ｗ」など）」と単語を並べた表記で、
    カンマ必須と読み取れないため、スペース区切りで入力するのが自然だった。
  - 実データ（12847 件）での再現:
    `ｗ`→13 本 / `草`→5 本 / `ｗ 草`→**0 本** / `ｗ,草`→13 本
  - 対応: 区切りを `[,，、\s]+` に拡張（半角/全角カンマ・読点・半角/全角スペース）。
    UI ラベルも「スペースまたはカンマ区切りで複数指定できます」、プレースホルダを
    「例： 草 ｗ 神回」に変更。
  - 検証: 全区切り方で 13 本に一致、「キーワードを足すと減る」現象が解消したことを実データで確認。

---

## 2026-08-04 ユーザー報告からの修正（v2.0.16）

- **[DB-71] 🔴 通信が一瞬途切れるだけで全クリップが「失敗」表示になる** — [templates/index2.html](templates/index2.html)
  - 報告: 「`❌ 失敗: Failed to fetch` が出る。**でも動画は成功してる**」
  - 原因: クリップ生成はサーバー側の daemon スレッドで走り、HTTP 接続とは無関係に完走する。
    にもかかわらず進捗ポーリングは `fetchWithRetry`（3 回失敗で throw）が投げた瞬間に
    外側の catch へ落ち、**キュー全件を「失敗」に塗り替えて打ち切っていた**。
    動画だけ正常に出来上がるのはこのため。
  - 対応: ポーリング失敗を捕まえて継続し、**2 分間つながらないときだけ**諦める。
    断の間は「⏳ 通信が不安定です（再試行中 N 秒）」とオレンジ表示にして失敗扱いにしない。
    諦める場合も「動画は生成されている可能性があるので Downloads を確認してください」と案内。
  - 実証: 実データ（1080p60 / 1.5GB）で同じ 14 秒の通信断を与えて比較。
    **旧＝失敗扱い（ConnectTimeout） / 新＝21.3s に復帰して継続**。

- **[DB-72] 🔴 進捗ファイル消失で UI が永久に「処理中」のまま** — [templates/index2.html](templates/index2.html)
  - 現象: app.py は「all_done を書く → `COMPLETION_HOLD_SEC`(5 秒) 待つ → progress.json を削除」
    の順で動き、以降 `/progress` は `{"progress":0,"message":"未開始"}` を返し続ける。
    通信断が完了タイミングと重なって all_done の数秒間を取り逃すと、
    **動画は出来ているのに UI が終われない**（DB-71 を直して耐性を上げた結果、この経路に入りやすくなった）。
  - 対応: 一度でも実進捗を見たあとに「未開始」が 15 秒続いたら、処理は完了したものとして扱う。
    （進捗ファイルは完了後にしか消えないので、この判定で安全）
  - 実証: E2E で `完了扱い（進捗ファイル消失を検知 / 36.7s）` を確認。通常経路は旧新とも
    all_done を約 9.7s で受信し退行なし。

- **[DB-73] 🟡 コメント ON/OFF がモーダルの中に埋没していた** — [templates/index2.html](templates/index2.html)
  - 指摘: 「設計上美しくない。コメント ON か OFF 聞いて、ON なら詳細設定ボタンを押したら色々出る、が自然」
  - 現象: 一番大きい二択（流す / 流さない）がモーダル内にあり、外側のボタンには**フォント名だけ**が
    出ていた。そのため画面を見ても ON / OFF が分からず、OFF にして閉じてもボタン表示は変わらない。
  - 対応: ON/OFF トグルを条件設定（第 2 ステップ）へ移動。ボタンの主ラベルを「⚙ 詳細設定」にし、
    フォント名は副次情報として右に添える。OFF のときは詳細設定ボタンを無効化。
    モーダル側の重複トグルは削除して二重管理を解消。
    hidden input 経由の送信値は変えていないので**サーバー側は無変更**。

---

## 2026-08-04 機能追加・UI 調整（v2.0.17）

- **[DB-74] 機能追加: コメント量を % で指定できるようにした** — [templates/index2.html](templates/index2.html), [app.py](app.py), [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 要望: 「コメント量も % で減らせるようにしたい。**この量を全部画面に出すのが 100%** として設定できるように」
  - 実装: 詳細設定モーダルに「コメント量」スライダー（10〜100% / 5 刻み / 既定 100）。
    `comment_density` として `/process_clips` → `mp4inchatnagasi.py --comment-density` へ渡す。
  - **間引きは時刻順の等間隔**（ランダム抽出にしない）。ランダムだと時間の偏りが崩れて
    「盛り上がりが盛り上がりに見えなくなる」ため。薄い所は薄いまま、濃い所は濃いまま全体だけ薄くなる。
  - 基準の妥当性を実データで確認: 12847 件 / 67 分 / 平均 秒 3.19 件 / ピーク 秒 10.3 件。
    レーン修正（DB-14 系）後の上限 秒 11.8 件を **403 窓すべてが下回る**ので、
    この規模の配信なら 100% で本当に全部流せる。
  - 検証: 指定どおりの割合になること（誤差 1% 未満）、ピーク窓も同じ割合で減ること
    （50%→50% 残存 / 20%→19% 残存）、時刻順・内容が壊れないこと、
    サーバー側クランプ（10〜100、不正値は 100）を実データで確認。実生成でも
    `コメント量 30%: 195 件 → 58 件に間引き` を確認。

- **[DB-75] UI: 詳細設定ボタンからフォント名を外した** — [templates/index2.html](templates/index2.html)
  - 指摘: 「ここ詳細設定って表示だけでいい。フォントの種類書かなくて OK」
  - 対応: ボタンは `⚙ 詳細設定` のみに。ボタン幅も 200px → 130px。
    フォント選択自体は hidden の `fontSelect` に保持されるので機能は変わらない。

> 📌 開発メモ: Python 側（app.py / mp4inchatnagasi.py）を変更したら **必ずサーバーを再起動する**こと。
> テンプレートは `TEMPLATES_AUTO_RELOAD` で即反映されるが Python コードは反映されない。
> この取り違えで「修正が効いていない」と 2 回誤判定した（DB-70 / DB-74）。

---

## 2026-08-04 プレビュー実データ化・アップデートチェック修正（v2.0.18）

- **[DB-76] 機能追加: プレビューが実 CSV を使い、コメント量 % に連動する** — [templates/index2.html](templates/index2.html)
  - 要望: 「プレビューは渡した CSV を参考に作って、% によってプレビューの文字量も変わるように」
  - 実装:
    - チャット CSV を選んだ時点でクライアント側で解析し、**実コメント本文**をプレビューの素材にする
      （従来は `SAMPLE_COMMENTS` の固定文言）。
    - その配信の**実レート（秒あたり件数）**を測り、100% の基準にする。
      出現間隔は `1000 / (実レート × %)` で決める。従来は固定 600ms だったため
      **% を変えてもプレビューが変わらなかった**。
    - ヒント文も実データ基準（例: `秒 約1.6件（元は 秒 約3.2件）`）。
  - 負荷対策: 巨大 CSV は先頭 2MB のみ読む / 語彙は 400 種で打ち止め / レート上限 秒 20 件。
  - 検証（実データ 12847 件 / 4026 秒 / 秒 3.19 件）:
    100% → 313ms、50% → 627ms、30% → 1045ms、10% → 3134ms と間隔が連動。
    プレビュー語彙 400 種すべてがサンプル文言ではなく実コメントであることを確認。
  - ⚠️ 実際の**描画**は未確認（検証環境の Browser ペインが非表示で
    `requestAnimationFrame` が回らないため）。流量計算と CSV 取り込みまでは検証済み。

- **[DB-77] 🔴 更新の確認に失敗しても「最新バージョンです」と表示していた** — [templates/index2.html](templates/index2.html), [templates/index.html](templates/index.html)
  - 原因: `if (data.error || !data.has_update)` と**エラーと最新を同じ枝で処理**していた。
    `/check-update` は GitHub に繋がらないと `{"has_update": false, "error": "..."}` を返すので、
    確認できていないのに「最新です」と誤認させていた。
  - 対応: `data.error` を独立して扱い「更新を確認できませんでした」と表示。

- **[DB-78] 🔴 「アップデートチェックを押しても何も起きない」の原因** — [templates/index2.html](templates/index2.html), [templates/index.html](templates/index.html)
  - 報告: 「アップデートチェックが効いてない。押しても何もならん」
  - 原因: `/check-update` の fetch が失敗したときの処理が
    `.catch(() => closeUpdateOverlay())` で、**オーバーレイを黙って閉じるだけ**だった。
    ユーザーから見ると「押したのに何も起きない」になる。
  - 対応: 失敗理由を画面に出して閉じるボタンを表示。両ページ（index / index2）で同期。
  - 補足: 「チェックしたら更新された」は仕様どおり。UPDATE CHECK は更新があれば
    確認なしで即適用する（挙動は今回変えていない）。

---

## 2026-05-10 運用上の既知挙動（実害なし）

### E-1: Cloudflare Email Routing で kyohei さん自己宛メールが Dropped される（5/10 確認）

**現象**: `support@clipgift.org` に届いたメールを Email Routing が `nekodori0612@gmail.com` に転送する際、送信元も `nekodori0612@gmail.com` の場合（kyohei さんのテスト送信時）、Gmail 側の dedup（自己宛 Message-ID 重複排除）で Dropped 扱いになる。

**観測値**: Cloudflare Email Routing summary（Last 7 days）で Dropped 4 件 / Total 11 件（36%）。

**本番影響**: なし。
- 外部ユーザー → `support@clipgift.org` → `nekodori0612@gmail.com` の転送は **Forwarded で正常配送**（同期間 7 件確認）
- Gmail dedup は同一アカウント内自己宛のみで発生、外部由来では発生しない
- 受信失敗・サポート対応漏れには繋がらない

**運用ルール**:
- **テスト時は Gmail プラスエイリアス `nekodori0612+test@gmail.com` を使う**（dedup 回避）
- 万一 Phase 1 中に外部ユーザー由来 Dropped が観測されたら `wrangler secret put SUPPORT_REPLY_TO` で `clipgift.dev@gmail.com` に切り替え（案 B 昇格）
- Phase 2 の Tauri/Stripe 化と同時にメール基盤再設計予定なので根本対応は Phase 2 で

**判断**: 🟢 低優先、コード変更不要、現状維持で OK。詳細調査は `.company/secretary/notes/2026-05-10-decisions.md` 参照。
