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

### 🔴 高
- **[S-1] アップロード検証なし** → **B-1 と同一**（セキュリティ観点でも高優先）。

### ✅ 対応済 (P2)
- **[S-2] `/downloads/<filename>` の明示的検証** — [app.py](app.py)
  - 対応: `os.path.realpath` で正規化後、`downloads_dir` 配下に収まっているか明示的にチェック。逸脱したら 400 を返す。

- **[S-3] `subprocess` への日本語タイトル・特殊文字** — [mp4inchatnagasi.py](mp4inchatnagasi.py)
  - 対応: `sanitize_filename` を強化。Windows 予約語（CON, PRN ほか）回避、末尾 `.`/空白除去、長さ上限 100、空文字なら `untitled` にフォールバック。

- **[S-4] デバッグ出力でファイルパスが大量に print** — [app.py:781-832](app.py:781)
  - 現状: ユーザー入力・一時パス・clips JSON の中身が標準出力に出る。
  - 影響: コンソールキャプチャやログファイル化時に情報漏洩。
  - 対応案: `logging` に移行し、debug レベルで抑制可能にする。

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

- **[Q-5] マジックナンバー** — [mp4inchatnagasi.py:398, 437-438](mp4inchatnagasi.py:398), [downloader.py:221](downloader.py:221)
  - 例: `7.0` (コメント表示秒数), `50` (進捗粒度), `0.08` (API sleep)。
  - 対応案: モジュール先頭で定数化。

---

## パフォーマンス

### 🟡 中
- **[P-1] YouTube チャット取得に再試行/バックオフなし** — [downloader.py:228 付近](downloader.py:228)
  - 現状: `time.sleep(0.08)` で緩和するのみ、429 エラー時は失敗。
  - 対応案: 指数バックオフ、`requests` の `Retry` アダプタ。

- **[P-2] 大量コメント時のメモリ・進捗更新粒度** — [mp4inchatnagasi.py:398-440](mp4inchatnagasi.py:398)
  - 現状: PNG 生成の進捗更新は 50 件ごと、1000 件超えると UI が数秒無反応に見える。
  - 対応案: 更新粒度を 10〜20 件に、`gc.collect()` をループ内に適切配置。

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

### 🟡 中
- **[D-1] `imageio_ffmpeg` と `bin/ffmpeg.exe` の二重管理**
  - 現状: setup.iss が `bin/` に ffmpeg を配置、pip 側にも `imageio_ffmpeg` が入る。参照経路が分岐する。
  - 対応案: どちらを優先するか明文化、`imageio_ffmpeg.get_ffmpeg_exe()` に統一するか、bin 優先にする。

### 🟢 低
- **[D-2] Python バージョン指定なし**
  - 対応案: `requirements.txt` 冒頭コメントか `pyproject.toml` で 3.10+ を明示。

---

## 取り下げた誤認項目（記録用）

- ~~`app.py:805` json.loads 失敗時のデッドロック~~ — 外側 except で release されるため実害なし
- ~~`pathlib.Path` 未 import~~ — [app.py:157](app.py:157) で import 済み

---

## 進め方の提案

1. **P0（完了）**: M-1（ドキュメント）→ B-1/S-1（アップロード検証）→ B-2（ロック管理）
2. **P1（完了）**: Q-1（`app.py` 分割）→ Q-2（logging 化）→ B-3/B-4（競合の整理）
3. **P2（完了）**: U-1/U-2（UX 改善）、M-2/M-3（設定外部化と更新復旧）、B-5/B-6（CSV/デコード）、S-2/S-3（パス検証/sanitize）
4. **残課題（任意）**: P-1/P-2（パフォーマンス・チャットDLバックオフ）、U-3（大容量検証）、Q-4/Q-5（命名統一・マジックナンバー）、D-1/D-2（依存ビルド整理）— いずれも 🟡中 〜 🟢低
