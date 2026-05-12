import os
import re
import subprocess
import sys
import shutil
import logging
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file
import traceback
import threading
import json
import io
import csv
import tempfile
import time
import hashlib
import webbrowser
import auto_update

# 分離済みモジュール
from paths import BASE_DIR, BIN_DIR
from chat_analyzer import analyze_chat
from font_manager import list_fonts, load_last_font, save_last_font, pick_default_font_name
from system_utils import (
    get_python_exe,
    get_ffmpeg_path,
    cleanup_temp_files_and_dirs,
    check_and_increment_start_count,
)
import config


from pathlib import Path


# Flaskアプリ初期化 (テンプレート・スタティック指定)
# ルートロガーの設定。個別モジュールは logging.getLogger(__name__) を使用する。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logger = logging.getLogger(__name__)

# === Phase 1 (BOOTH 買い切り版): ライセンス認証 OFF ===
# 2026-05-13 kyohei さん指示で BOOTH 公開時に honor system 運用に切替。
# 利用規約で再配布・転売禁止を謳い、技術的制限は Phase 2 サブスク移行時に復活する。
# licensing/ モジュール本体は温存（このフラグを False にすれば即復活可能）。
PHASE_1_HONOR_SYSTEM = True

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

# --- アップロード制限・拡張子ホワイトリスト（config.py で定義） ---
MAX_UPLOAD_BYTES = config.MAX_UPLOAD_BYTES
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
# ホットリロード: テンプレ編集 → アプリ再起動なしで次リクエストに反映（debug=False でもオン）
app.config["TEMPLATES_AUTO_RELOAD"] = True

ALLOWED_VIDEO_EXTS = config.ALLOWED_VIDEO_EXTS
ALLOWED_CSV_EXTS = config.ALLOWED_CSV_EXTS


def _validate_upload(file_storage, allowed_exts, label="ファイル"):
    """
    アップロードファイルを検証する。
    - 空ファイル拒否
    - 拡張子ホワイトリスト
    問題があれば (flask Response, status_code) のタプルを、OK なら None を返す。
    """
    if file_storage is None or not file_storage.filename:
        return jsonify({"error": f"{label}が指定されていません"}), 400

    filename = file_storage.filename
    ext = os.path.splitext(filename)[1].lower()
    if ext not in allowed_exts:
        allowed_str = ", ".join(sorted(allowed_exts))
        return (
            jsonify({
                "error": f"{label}の拡張子が不正です（許可: {allowed_str}）",
                "filename": filename,
            }),
            400,
        )
    return None


@app.errorhandler(413)
def _handle_too_large(_e):
    limit_gb = MAX_UPLOAD_BYTES / (1024 ** 3)
    return (
        jsonify({
            "error": f"アップロードサイズが大きすぎます（上限 {limit_gb:.0f}GB）",
        }),
        413,
    )


def generate_temp_audio_name(filename, clip_start, clip_end):
    """
    同じファイル・範囲なら同じ名前を返す
    """
    base = f"{filename}_{clip_start}_{clip_end}"
    hashed = hashlib.md5(base.encode("utf-8")).hexdigest()
    return f"clipgen_{hashed}.mp3"


def _terminate_then_kill(proc, timeout=None):
    """
    プロセスを SIGTERM 相当で停止し、timeout 内に終わらなければ kill する。
    キャンセル時の応答性を確保するためのヘルパー。
    """
    if proc is None:
        return
    if timeout is None:
        timeout = config.TERMINATE_TIMEOUT_SEC
    try:
        if proc.poll() is not None:
            return  # 既に終了
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("terminate でも止まらないため kill にエスカレート (pid=%s)", proc.pid)
            proc.kill()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.error("kill しても応答なし (pid=%s)", proc.pid)
    except Exception as e:
        logger.warning("プロセス停止中に例外: %s", e)


# ハートビート管理
_last_heartbeat = time.time()
_heartbeat_lock = threading.Lock()
_is_downloading = False  # ダウンロード中フラグ
_is_downloading_lock = threading.Lock()

# 動画ダウンロード subprocess の参照（キャンセル用）
# - _current_dl_proc: run_download 内の subprocess.Popen を保持。/cancel_download から terminate するため。
# - _dl_proc_lock: _current_dl_proc の参照取得・更新を保護（短時間のみ保持、subprocess 操作はロック外）
_current_dl_proc = None
_dl_proc_lock = threading.Lock()


# === ディスク容量チェックヘルパー ===

# しきい値: DL 開始前（推定動画サイズ事前不明、10h 1080p 配信を想定）
# 10h 1080p は 8〜15 GB に達するケースが多いため 10 GiB（≒ 10.7 GB）を最低ラインに設定
_DL_MIN_FREE_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB（GiB）

# しきい値: クリップ生成（一時ディレクトリ + Downloads 出力で動画サイズ × 倍率）
# 一時 + 出力で約 1.8 倍が現実的（2.5 倍は過大評価で空き少なめのユーザーに誤検知が出ていた）
_CLIP_DISK_MULTIPLIER = 1.8


def _check_disk_space(required_bytes, target_dir):
    """
    target_dir が属するボリュームの空き容量が required_bytes 以上かチェックする。

    Args:
        required_bytes: 必要な空き容量（bytes）
        target_dir: チェック対象ディレクトリ（str / Path）

    Returns:
        十分なら None、不足ならユーザー向け日本語エラーメッセージ（str）。
        shutil.disk_usage 自体が例外を投げた場合は None を返す（チェック不能 = 通す側に倒す）。
    """
    try:
        usage = shutil.disk_usage(str(target_dir))
        free = usage.free
    except Exception as e:
        # 容量取得自体が失敗した場合はブロックしない（ネットワークドライブ等の特殊ケースを想定）
        logger.warning("disk_usage 取得失敗 (target=%s): %s", target_dir, e)
        return None

    if free >= required_bytes:
        return None

    # 計算は 1024**3（GiB）ベース。ユーザー向けには「GB（GiB）」併記で意味を取りやすくする
    required_gib = required_bytes / (1024 ** 3)
    free_gib = free / (1024 ** 3)
    return (
        f"空き容量不足: 推定 {required_gib:.1f} GB（GiB）必要 / 残り {free_gib:.1f} GB（GiB）。"
        f"不要なファイルを削除してから再実行してください。"
    )

def _heartbeat_watchdog():
    """
    ハートビート監視（自動終了は廃止、ユーザー明示の閉じる操作のみで終了）

    ハートビートのタイムアウトでアプリを勝手に終了させない方針。
    ブラウザを閉じてもサーバーは起動したまま、ユーザーが明示的に
    「閉じる」ボタン → 確認ダイアログ → /api/shutdown を叩いた時のみ終了。

    （historic: 以前は HEARTBEAT_TIMEOUT_SEC 経過で os._exit(0) してたが、
       長時間使用中に勝手に落ちるので 2026-05-06 廃止）
    """
    return  # ループ自体不要、廃止


# watchdog スレッドは起動しない（自動終了廃止のため）
# 念のため関数は残してあるが、呼び出さない


@app.route('/favicon.ico')
def favicon():
    return '', 204


@app.route("/api/shutdown", methods=["POST"])
def shutdown_route():
    """
    ユーザー明示のサーバー終了 API。

    UI の「閉じる」ボタン → 確認ダイアログ → ここを叩く → サーバー停止。
    勝手な watchdog 終了はないので、これだけが正規の停止経路。
    """
    logger.info("👋 ユーザー操作によるサーバー終了")
    # レスポンスを返してから少し遅延して終了
    def _delayed_exit():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=_delayed_exit, daemon=True).start()
    return jsonify({"success": True, "message": "サーバーを終了します"})


@app.route("/api/restart", methods=["POST"])
def restart_route():
    """
    アップデート後の自動再起動 API。

    別プロセスを spawn → 自プロセスは os._exit。
    多重起動防止が干渉しないよう、spawn 側は数秒待ってから python を起動する。
    """
    logger.info("🔄 アップデート完了、自動再起動を実行")

    def _spawn_restart():
        # まずレスポンスを返すための短い遅延
        time.sleep(0.2)
        try:
            python_exe = get_python_exe()
            app_py = os.path.join(BASE_DIR, "app.py")
            port = config.SERVER_PORT
            # bat にコマンドメタ文字を含むパスが混入してないか防御チェック
            unsafe_chars = '%&^"<>|'
            for p in (python_exe, app_py):
                if any(c in p for c in unsafe_chars):
                    logger.error("再起動パスに不正文字が含まれます: %s", p)
                    return
            # 自プロセスは os._exit 直後で port 解放されるはずだが、
            # 念のため: 1 秒待機 → port 5000 を握ってる PID を taskkill → 新規起動
            # これで多重起動防止に引っかからず確実に立ち上がる
            bat_lines = [
                "@echo off",
                "timeout /t 2 /nobreak > nul",
                f'for /f "tokens=5" %%a in (\'netstat -ano ^| findstr ":{port} " ^| findstr "LISTENING"\') do taskkill /F /PID %%a >nul 2>&1',
                "timeout /t 1 /nobreak > nul",
                "set LAUNCHED_BY_RESTART=1",
                f'start "" /b "{python_exe}" "{app_py}"',
                "exit /b 0",
            ]
            bat_path = os.path.join(BASE_DIR, "_clipgift_restart.bat")
            with open(bat_path, "w", encoding="cp932", errors="replace") as f:
                f.write("\r\n".join(bat_lines) + "\r\n")
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            subprocess.Popen(
                ["cmd", "/c", bat_path],
                creationflags=subprocess.CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
        except Exception as e:
            logger.exception("再起動 spawn 失敗: %s", e)
        # spawn 後に自プロセスを即 exit（port 解放）
        time.sleep(0.3)
        os._exit(0)

    threading.Thread(target=_spawn_restart, daemon=True).start()
    return jsonify({"success": True, "message": "再起動します"})


@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    global _last_heartbeat
    with _heartbeat_lock:
        _last_heartbeat = time.time()
    return jsonify({"ok": True})


@app.route("/is_downloading", methods=["GET"])
def is_downloading_route():
    with _is_downloading_lock:
        return jsonify({"downloading": _is_downloading})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/page2")
def page2():
    return render_template("index2.html")




@app.route("/save-last-font", methods=["POST"])
def save_last_font_route():
    """フォント設定モーダルで「決定」した時点で即デフォルト保存。
    これによりクリップ生成しなくても次回起動から選択フォントが既定になる。"""
    data = request.get_json(silent=True) or {}
    font_name = (data.get("font_name") or "").strip()
    if not font_name:
        return jsonify({"success": False, "message": "font_name が空です"}), 400
    # ホワイトリスト照合: 実在するフォント名以外は受け付けない
    if not any(f["name"] == font_name for f in list_fonts()):
        return jsonify({"success": False, "message": "存在しないフォントです"}), 400
    save_last_font(font_name)
    return jsonify({"success": True, "last_font": font_name})


@app.route("/get-fonts", methods=["GET"])
def get_fonts():
    fonts = list_fonts()
    last = load_last_font()
    # 別 PC で「保存済みフォントが入っていない」「未設定」ケースは
    # 優先候補（メイリオ → 游ゴシック → MS ゴシック）でフォールバック。
    if not last or not any(f["name"] == last for f in fonts):
        last = pick_default_font_name(fonts)
    return jsonify({"fonts": fonts, "last_font": last})


@app.route("/font-preview/<path:font_name>", methods=["GET"])
def font_preview_route(font_name):
    """フォント選択モーダル用：実フォントファイルを @font-face プレビュー用に配信。
    パストラバーサル防止のため list_fonts() の結果（システム/ユーザーフォントフォルダ）
    でホワイトリスト照合し、一致した path のみ返す。"""
    matching = next((f for f in list_fonts() if f["name"] == font_name), None)
    if not matching:
        return ("Not found", 404)
    lower = font_name.lower()
    if lower.endswith(".ttf"):
        mime = "font/ttf"
    elif lower.endswith(".otf"):
        mime = "font/otf"
    elif lower.endswith(".ttc"):
        mime = "font/collection"
    else:
        mime = "application/octet-stream"
    # max_age=3600: プレビュー用なのでキャッシュ可
    return send_file(matching["path"], mimetype=mime, max_age=3600)


@app.route("/analyze_chat_csv", methods=["POST"])
def analyze_chat_csv():
    try:
        # ① ファイル存在チェック＋検証
        if "chatFile" not in request.files:
            return jsonify({"error": "チャットファイルが見つかりません"}), 400

        file = request.files["chatFile"]
        err = _validate_upload(file, ALLOWED_CSV_EXTS, "チャットファイル")
        if err is not None:
            return err

        # ② CSVデコード（UTF-8優先 → Shift-JIS / CP932 フォールバック）
        # 先にバイト列で取り切ってから複数エンコードを順次試す（seek 不可ストリーム対策）
        raw_bytes = file.stream.read()
        text = None
        for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
            try:
                text = raw_bytes.decode(enc)
                logger.debug("CSV デコード成功: %s", enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            # 最後の手段: 不正バイトを置換しつつ utf-8 で読む
            text = raw_bytes.decode("utf-8", errors="replace")
            logger.warning("CSV デコード fallback: utf-8 + errors=replace")
        stream = io.StringIO(text)

        reader = csv.reader(stream)
        next(reader, None)  # ヘッダー行をスキップ
        lines = []
        for row in reader:
            if len(row) < 3:
                continue
            time_str = row[0].strip()
            if ":" not in time_str:
                continue
            comment = row[2].strip()
            lines.append((time_str, comment))

        keywords_str = request.form.get("keywords", "")
        start_threshold = int(request.form.get("start_threshold", 5))
        end_threshold = int(request.form.get("end_threshold", 5))
        clip_offset = int(request.form.get("clip_offset", 30))
        video_duration = int(request.form.get("videoDuration", 0))

        keywords = [k.strip() for k in keywords_str.split(",") if k.strip()]
        if not keywords:
            return jsonify({"error": "キーワードが空です"}), 400

        result = analyze_chat(
            lines,
            keywords,
            start_threshold=start_threshold,
            end_threshold=end_threshold,
            clip_offset=clip_offset,
            video_duration_sec=video_duration,
        )
        return jsonify({"success": True, "clips": result})

    except Exception as e:
        logger.error("analyze_chat_csv エラー: %s", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/extract_audio", methods=["POST"])
def extract_audio():
    try:
        if "video" not in request.files:
            return jsonify({"error": "動画ファイルがありません"}), 400

        video_file = request.files["video"]
        err = _validate_upload(video_file, ALLOWED_VIDEO_EXTS, "動画ファイル")
        if err is not None:
            return err

        clip_start = float(request.form.get("start", 0))
        clip_end = float(request.form.get("end", 60))

        temp_dir = tempfile.gettempdir()
        audio_name = generate_temp_audio_name(video_file.filename, clip_start, clip_end)
        audio_path = os.path.join(temp_dir, audio_name)

        if os.path.exists(audio_path):
            logger.info("🎵 キャッシュヒット: %s", audio_path)
            return send_file(audio_path, mimetype="audio/mpeg")

        fd, temp_video_path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        video_file.save(temp_video_path)

        ffmpeg_path = get_ffmpeg_path()
        duration = clip_end - clip_start

        cmd = [
            ffmpeg_path,
            "-y",
            "-ss",
            str(clip_start),
            "-i",
            temp_video_path,
            "-t",
            str(duration),
            "-vn",
            "-acodec",
            "libmp3lame",
            "-q:a",
            "5",
            audio_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)

        if result.returncode != 0:
            logger.error("FFmpegエラー: %s", result.stderr)
            return jsonify({"error": "音声抽出失敗", "detail": result.stderr}), 500

        try:
            os.remove(temp_video_path)
        except Exception:
            pass

        return send_file(audio_path, mimetype="audio/mpeg")

    except Exception as e:
        logger.error("extract_audio エラー: %s", e)
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/downloads/<path:filename>")
def serve_downloads(filename):
    """
    Downloads ディレクトリ配下のファイルを返す。
    send_from_directory は内部で safe_join するが、念のため realpath で
    Downloads 配下に収まっているか明示的にチェックする（多層防御）。
    """
    downloads_dir = os.path.realpath(str(Path.home() / "Downloads"))
    requested = os.path.realpath(os.path.join(downloads_dir, filename))
    # ディレクトリ自体やその外側を要求された場合は拒否（必ず配下のファイルである必要がある）
    if not requested.startswith(downloads_dir + os.sep):
        logger.warning("不正な downloads パスを拒否: %s", filename)
        return jsonify({"error": "invalid path"}), 400
    return send_from_directory(downloads_dir, filename)


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


@app.route("/download-yt-video-chat", methods=["POST"])
def download_yt_video_chat():
    """YouTubeの動画とチャットをダウンロード"""
    data = request.get_json()
    video_url = data.get("videoUrl", "").strip()

    if not video_url:
        return jsonify({"success": False, "message": "URLが指定されていません"}), 400

    # 画質指定（page1 の <select id="resolutionSelect"> 由来）。
    # 許可値以外は 1080（既定）にフォールバック。9999 は「自動＝最高画質」のセンチネル。
    _ALLOWED_RES = (360, 480, 720, 1080, 9999)
    try:
        max_resolution = int(data.get("resolution", 1080))
    except (TypeError, ValueError):
        max_resolution = 1080
    if max_resolution not in _ALLOWED_RES:
        max_resolution = 1080

    # 出力先: 固定で ~/Downloads。
    # Path.home() は USERPROFILE 環境変数から解決するので全 Windows で正しく動く。
    # Downloads フォルダが存在しないレアケースは makedirs で自動作成。
    downloads_dir = str(Path.home() / "Downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    # ディスク容量チェック（事前推定不可なので最低 5 GB を要求）
    disk_err = _check_disk_space(_DL_MIN_FREE_BYTES, downloads_dir)
    if disk_err is not None:
        logger.warning("DL 開始前の容量チェック失敗: %s", disk_err)
        return jsonify({"success": False, "message": disk_err}), 400

    # 進捗ファイル
    dl_progress_path = os.path.join(BASE_DIR, "dl_progress.json")
    with open(dl_progress_path, "w", encoding="utf-8") as f:
        json.dump({"progress": 0, "message": "ダウンロード開始"}, f, ensure_ascii=False)

    with _dl_logs_global_lock:
        _dl_logs_global.clear()

    dl_logs = []
    dl_logs_lock = threading.Lock()
    _DL_LOG_MAX = 200

    def _dl_append_log(line):
        with dl_logs_lock:
            dl_logs.append(line)
            if len(dl_logs) > _DL_LOG_MAX:
                del dl_logs[: len(dl_logs) - _DL_LOG_MAX]
        with _dl_logs_global_lock:
            _dl_logs_global.append(line)
            if len(_dl_logs_global) > _DL_LOG_MAX:
                del _dl_logs_global[: len(_dl_logs_global) - _DL_LOG_MAX]

    def run_download():
        global _is_downloading, _current_dl_proc
        with _is_downloading_lock:
            _is_downloading = True
        try:
            proc = subprocess.Popen(
                [
                    get_python_exe(),
                    os.path.join(BASE_DIR, "downloader.py"),
                    video_url,
                    downloads_dir,
                    dl_progress_path,
                    str(max_resolution),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
                text=True,
                encoding="utf-8",
                errors="backslashreplace",
            )
            # キャンセル可能にするためグローバルへ参照を保存
            with _dl_proc_lock:
                _current_dl_proc = proc
            output_lines = []
            for line in iter(proc.stdout.readline, ""):
                line = line.rstrip()
                if not line:
                    continue
                logger.info("[DL] %s", line)
                _dl_append_log(line)
                output_lines.append(line)
                # progress.jsonへの書き込みはdownloader.pyに完全に任せる

            proc.wait()
            if proc.returncode == 2:
                # downloader が ChatNotAvailableError 等のユーザー向け親切メッセージを
                # progress.json に書き込んで exit(2) しているため、ここでは上書きしない。
                # （ライブ配信中 URL / リプレイ無効動画等の友好的エラー表示）
                pass
            elif proc.returncode != 0:
                tail = "\n".join(output_lines[-10:]) if output_lines else "出力なし"
                # エラー時だけapp.pyが書く（downloaderが書けていないケース）
                try:
                    with open(dl_progress_path, "w", encoding="utf-8") as f:
                        json.dump({"progress": -1, "message": f"エラー:\n{tail}"}, f, ensure_ascii=False)
                except Exception:
                    pass
            # 正常終了時はdownloader.pyが100%を書いているので何もしない
            # フロントが完了を読み取るまで待ってから削除
            time.sleep(config.COMPLETION_HOLD_SEC)
            try:
                os.remove(dl_progress_path)
            except Exception:
                pass
        except Exception as e:
            try:
                with open(dl_progress_path, "w", encoding="utf-8") as f:
                    json.dump({"progress": -1, "message": f"起動エラー: {str(e)}"}, f, ensure_ascii=False)
            except Exception:
                pass
        finally:
            with _dl_proc_lock:
                _current_dl_proc = None
            with _is_downloading_lock:
                _is_downloading = False

    thread = threading.Thread(target=run_download, daemon=True)
    thread.start()

    return jsonify({
        "success": True,
        "message": "ダウンロードを開始しました",
        "progress_path": dl_progress_path,
    })


@app.route("/reset-progress", methods=["POST"])
def reset_progress():
    dl_progress_path = os.path.join(BASE_DIR, "dl_progress.json")
    try:
        with open(dl_progress_path, "w", encoding="utf-8") as f:
            json.dump({"progress": 0, "message": ""}, f, ensure_ascii=False)
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/progress", methods=["GET"])
def get_progress():
    progress_path = request.args.get("path")
    if not progress_path or not os.path.exists(progress_path):
        return jsonify({"progress": 0, "message": "未開始"})
    abs_path = os.path.abspath(progress_path)
    temp_dir = os.path.abspath(tempfile.gettempdir())
    base_dir = os.path.abspath(BASE_DIR)
    if not (abs_path.startswith(temp_dir) or abs_path.startswith(base_dir)):
        return jsonify({"progress": -1, "message": "不正なパスです"}), 400
    if not abs_path.endswith(".json"):
        return jsonify({"progress": -1, "message": "不正なファイル形式です"}), 400
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"progress": -1, "message": f"エラー: {str(e)}"})


@app.route("/get-progress-file", methods=["GET"])
def get_progress_file():
    progress_path = request.args.get("path")
    if not progress_path or not os.path.exists(progress_path):
        return jsonify({"progress": 0, "message": "未開始"})
    abs_path = os.path.abspath(progress_path)
    temp_dir = os.path.abspath(tempfile.gettempdir())
    base_dir = os.path.abspath(BASE_DIR)
    if not (abs_path.startswith(temp_dir) or abs_path.startswith(base_dir)):
        return jsonify({"progress": -1, "message": "不正なパスです"}), 400
    if not abs_path.endswith(".json"):
        return jsonify({"progress": -1, "message": "不正なファイル形式です"}), 400
    # atomic renameと競合しないようリトライ
    last_err = None
    for _ in range(config.PROGRESS_READ_RETRIES):
        try:
            with open(progress_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # logsはメモリから注入（ファイルへの競合書き込みを完全回避）
            with _dl_logs_global_lock:
                data["logs"] = list(_dl_logs_global)
            return jsonify(data)
        except (json.JSONDecodeError, OSError) as e:
            last_err = e
            time.sleep(config.PROGRESS_READ_RETRY_INTERVAL_SEC)
    return jsonify({"progress": 0, "message": "読み取り待機中"})


processing_lock = threading.Lock()

# 共有状態。すべて _state_lock で保護して読み書きする。
# - _is_processing:  クリップ処理中かどうか（watchdog の無効化判定に使用）
# - current_process: 実行中の subprocess.Popen。キャンセル時に terminate するため参照
# - current_clip_index: UI 表示用の現在クリップ番号
# - cancel_flag: キャンセル要求フラグ
_state_lock = threading.Lock()
_is_processing = False
current_process = None
current_clip_index = 0
cancel_flag = False

_process_logs = []
_process_logs_lock = threading.Lock()
_PROCESS_LOG_MAX = config.PROCESS_LOG_MAX

# ダウンロードログ（グローバル管理）
_dl_logs_global = []
_dl_logs_global_lock = threading.Lock()


@app.route("/process_clips", methods=["POST"])
def process_clips():
    global current_process, cancel_flag, current_clip_index, _is_processing
    logger.info("🚀 リクエスト受信時間: %s", time.strftime("%Y-%m-%d %H:%M:%S"))
    logger.debug("request.content_length: %s", request.content_length)

    if not processing_lock.acquire(blocking=False):
        return (
            jsonify(
                {"success": False, "message": "現在処理中です。完了までお待ちください"}
            ),
            200,
        )

    # watchdog から「処理中」と見えるようにフラグを立てる。
    with _state_lock:
        _is_processing = True

    # Thread に処理を委譲したかどうか。委譲したら lock や temp_dir の解放は run_process の finally に任せる。
    # 委譲前に early return / 例外で抜ける場合は、末尾 finally で解放する。
    thread_started = False
    temp_dir = None  # mkdtemp 後に値が入る

    try:
        logger.info("✅ /process_clips エンドポイント呼び出し")

        # === パスベースモード（C 案: ローカルファイル直接参照） ===
        # フォーム値 video_path / chat_path がある場合、そのまま使う
        # ブラウザでは事前に /api/resolve-local-file で確認済みのパスを送る想定
        video_path_arg = request.form.get("video_path", "").strip()
        chat_path_arg = request.form.get("chat_path", "").strip()
        is_path_mode = bool(video_path_arg and chat_path_arg)

        clips_json = request.form.get("clips")

        if is_path_mode:
            # path mode: ファイル存在 + Downloads/ 配下チェック
            downloads_dir = os.path.realpath(str(Path.home() / "Downloads"))
            for p, label in ((video_path_arg, "動画"), (chat_path_arg, "チャット")):
                real = os.path.realpath(p)
                if not real.startswith(downloads_dir + os.sep) and real != downloads_dir:
                    return jsonify({"error": f"{label}パスが Downloads 配下でない: {p}"}), 400
                if not os.path.isfile(real):
                    return jsonify({"error": f"{label}ファイルが存在しない: {p}"}), 400
            video_file = None
            chat_file = None
            logger.info("⚡ path mode: アップロード省略 (video=%s)", video_path_arg)
        else:
            # 従来: アップロードモード
            video_file = request.files.get("video")
            chat_file = request.files.get("chat")

            logger.debug("受信したclips_json文字列: %s", clips_json)

            if not video_file or not chat_file or not clips_json:
                return jsonify({"error": "必要なデータが不足しています"}), 400

            for _f, _exts, _label in (
                (video_file, ALLOWED_VIDEO_EXTS, "動画ファイル"),
                (chat_file, ALLOWED_CSV_EXTS, "チャットファイル"),
            ):
                err = _validate_upload(_f, _exts, _label)
                if err is not None:
                    return err

        if not clips_json:
            return jsonify({"error": "clips データが不足しています"}), 400

        clips = json.loads(clips_json)
        font_name = request.form.get("font_name", "")
        if font_name:
            save_last_font(font_name)
            # フォントのフルパスを解決
            font_path = ""
            for f_info in list_fonts():
                if f_info["name"] == font_name:
                    font_path = f_info["path"]
                    break
        else:
            font_path = ""

        # コメント流し ON/OFF（フォント設定モーダルのトグル）。
        # "true" 以外は false 扱いにして mp4inchatnagasi.py 側に渡す。
        _overlay_raw = request.form.get("comment_overlay_enabled", "true").strip().lower()
        comment_overlay_enabled = "true" if _overlay_raw in ("true", "1", "on", "yes") else "false"

        # コメント色（#RRGGBB / 任意。フォーマット不正なら white にフォールバック）
        _color_raw = (request.form.get("comment_color", "") or "").strip()
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", _color_raw):
            comment_color = _color_raw
        else:
            comment_color = "#FFFFFF"

        # コメントフォントサイズ（20-100 整数。範囲外/不正は 50）
        try:
            _size_int = int(request.form.get("comment_fontsize", "50"))
        except (TypeError, ValueError):
            _size_int = 50
        comment_fontsize = str(max(20, min(100, _size_int)))

        downloads_dir = str(Path.home() / "Downloads")
        os.makedirs(downloads_dir, exist_ok=True)

        temp_dir = tempfile.mkdtemp(prefix="clipgen_")
        logger.info("📝 一時ディレクトリ作成: %s", temp_dir)

        progress_file = os.path.join(temp_dir, "progress.json")

        if is_path_mode:
            # ローカルファイルそのまま参照（コピー不要）
            video_path = video_path_arg
            chat_path = chat_path_arg
        else:
            # アップロードを一時ディレクトリへ保存
            video_path = os.path.join(temp_dir, "input.mp4")
            chat_path = os.path.join(temp_dir, "chat.csv")
            video_file.save(video_path)
            chat_file.save(chat_path)

        # ディスク容量チェック: 動画サイズ × 2.5 を一時ディレクトリ側ボリュームに要求
        # （tempfile.gettempdir と Downloads は別ボリュームの可能性があるため、より厳しい temp 側で見る）
        try:
            video_size = os.path.getsize(video_path)
        except OSError as e:
            logger.warning("動画サイズ取得失敗（容量チェックスキップ）: %s", e)
            video_size = 0
        if video_size > 0:
            required_clip_bytes = int(video_size * _CLIP_DISK_MULTIPLIER)
            disk_err = _check_disk_space(required_clip_bytes, temp_dir)
            if disk_err is not None:
                logger.warning("クリップ生成前の容量チェック失敗: %s", disk_err)
                return jsonify({"error": disk_err}), 400

        logger.info("💡 clipsの数: %d", len(clips))
        logger.debug("clipsの中身: %s", json.dumps(clips, ensure_ascii=False))

        with _state_lock:
            cancel_flag = False
            current_clip_index = 0

        with open(progress_file, "w", encoding="utf-8") as f:
            json.dump(
                {"progress": 0, "message": "処理を開始しました", "current_clip": 0},
                f,
                ensure_ascii=False,
            )

        def _append_log(line):
            """ログをキューに追記し、上限超えたら古い行を削除"""
            with _process_logs_lock:
                _process_logs.append(line)
                if len(_process_logs) > _PROCESS_LOG_MAX:
                    del _process_logs[: len(_process_logs) - _PROCESS_LOG_MAX]

        def _write_progress(data: dict):
            """progress.jsonに現在のlogsを付加して書き込む"""
            with _process_logs_lock:
                data["logs"] = list(_process_logs)
            with open(progress_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)

        # 失敗クリップのインデックス記録用（クロージャ経由で run_process と共有）
        _shared_failed = {"failed_clip_indices": []}

        def run_process():
            global current_process, current_clip_index, _is_processing
            with _process_logs_lock:
                _process_logs.clear()
            try:
                for idx, clip in enumerate(clips, 1):
                    _append_log(f"▶ クリップ {idx}/{len(clips)} 開始")
                    with _state_lock:
                        is_cancelled = cancel_flag
                    logger.debug("▶ ループ開始 idx=%d, cancel_flag=%s", idx, is_cancelled)

                    if is_cancelled:
                        _write_progress({"progress": -1, "message": "キャンセルされました", "current_clip": idx})
                        logger.info("🛑 キャンセル検知、処理中断")
                        break

                    clip_title = clip.get("title", f"クリップ{idx}")
                    clip_path = os.path.join(temp_dir, f"clip_{idx}.json")

                    with open(clip_path, "w", encoding="utf-8") as f:
                        json.dump([clip], f, ensure_ascii=False)

                    logger.info("▶ %s: サブプロセス開始", clip_title)

                    # Popen はロック外で実行（ロック中に長時間処理を抱えない）。
                    # 起動後にロック内で current_process と current_clip_index を更新し、
                    # 他スレッド（cancel_process）が一貫した状態を見られるようにする。
                    proc = subprocess.Popen(
                        [
                            get_python_exe(),
                            os.path.join(BASE_DIR, "mp4inchatnagasi.py"),
                            "--video",
                            video_path,
                            "--csv",
                            chat_path,
                            "--clips",
                            clip_path,
                            "--outdir",
                            downloads_dir,
                            "--progress",
                            progress_file,
                            "--clip-idx",
                            str(idx),
                            "--clip-title",
                            clip_title,
                            "--font",
                            font_path,
                            "--comment-overlay",
                            comment_overlay_enabled,
                            "--comment-color",
                            comment_color,
                            "--comment-fontsize",
                            comment_fontsize,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        text=True,
                        bufsize=1,
                        encoding="utf-8",
                        errors="backslashreplace",
                    )
                    with _state_lock:
                        current_process = proc
                        current_clip_index = idx

                    output_lines = []
                    # ローカル参照 proc を使うことで、他スレッドが current_process を入れ替えても影響を受けない
                    for line in iter(proc.stdout.readline, ""):
                        with _state_lock:
                            is_cancelled = cancel_flag
                        if is_cancelled:
                            _terminate_then_kill(proc)
                            _write_progress({"progress": -1, "message": "キャンセルにより終了", "current_clip": idx})
                            logger.info("🛑 subprocessを強制終了")
                            break
                        line = line.strip()
                        if not line:
                            continue
                        logger.debug("📢 mp4inchatnagasi: %s", line)
                        _append_log(line)
                        output_lines.append(line)

                    retcode = proc.wait()
                    logger.debug("✅ wait()終了 retcode=%s", retcode)

                    with _state_lock:
                        is_cancelled = cancel_flag
                    if retcode != 0 and not is_cancelled:
                        # ★ 1 クリップ失敗してもスキップして次のクリップへ進む
                        # 失敗 clip の idx を progress.json に記録、UI 側で再 DL 対象に
                        error_output = "\n".join(output_lines[-20:])  # 末尾 20 行のみ
                        logger.error(
                            "❌ クリップ %s 失敗 (retcode=%s)、スキップして続行:\n%s",
                            clip_title, retcode, error_output,
                        )
                        if "failed_clip_indices" not in _shared_failed:
                            _shared_failed["failed_clip_indices"] = []
                        _shared_failed["failed_clip_indices"].append(idx)
                        _write_progress({
                            "progress": int((idx + 1) / max(len(clips), 1) * 100),
                            "message": f"⚠️ クリップ {idx + 1} の生成に失敗しました（次のクリップへ進みます）",
                            "current_clip": idx,
                            "failed_clip_indices": list(_shared_failed["failed_clip_indices"]),
                        })
                        # raise しない、次のクリップへ
                        continue

                    if not is_cancelled:
                        logger.info("✅ %s: 処理完了", clip_title)

                with _state_lock:
                    is_cancelled = cancel_flag
                if not is_cancelled:
                    failed_count = len(_shared_failed.get("failed_clip_indices", []))
                    success_count = len(clips) - failed_count
                    if failed_count > 0:
                        msg = f"完了（成功 {success_count} / 失敗 {failed_count}）"
                        _append_log(f"⚠️ {msg}")
                        logger.info("⚠️ %s", msg)
                    else:
                        _append_log("✅ 全クリップ処理完了")
                        logger.info("✅ 全クリップ処理完了")
                    _write_progress({
                        "progress": 100,
                        "message": "全クリップ処理完了" if failed_count == 0 else f"完了（失敗 {failed_count} 件）",
                        "current_clip": len(clips),
                        "all_done": True,
                        "failed_clip_indices": list(_shared_failed.get("failed_clip_indices", [])),
                    })
                    time.sleep(config.COMPLETION_HOLD_SEC)
                    try:
                        os.remove(progress_file)
                    except Exception:
                        pass

            except Exception as e:
                logger.error("❌ run_process全体でエラー: %s", e)
                try:
                    _write_progress({"progress": -1, "message": f"全体失敗: {str(e)}", "current_clip": 0})
                except Exception as e2:
                    logger.warning("⚠️ 進捗ファイル書き込みで再エラー: %s", e2)
                # 失敗状態を UI に確実に見せるため、成功パスと同じ COMPLETION_HOLD_SEC 秒だけ progress.json を保持。
                # 直後の finally で temp_dir ごと消えてしまうと「失敗 → 即未開始」のチラつきが発生するため。
                try:
                    time.sleep(config.COMPLETION_HOLD_SEC)
                except Exception:
                    pass
            finally:
                # 状態をまとめてクリア（watchdog の誤検知を防ぐため _is_processing も落とす）
                with _state_lock:
                    current_process = None
                    current_clip_index = 0
                    _is_processing = False
                # 一時ディレクトリの掃除（残骸を残さない）
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.debug("🗑️ temp_dir 削除: %s", temp_dir)
                except Exception as e:
                    logger.warning("temp_dir 削除に失敗: %s", e)
                processing_lock.release()

        threading.Thread(target=run_process, daemon=True).start()
        thread_started = True  # 以降の lock 解放は run_process.finally に委譲

        return jsonify({"success": True, "progress_path": progress_file})

    except Exception:
        return (
            jsonify(
                {
                    "error": "サーバー内部でエラーが発生しました",
                    "details": traceback.format_exc(),
                }
            ),
            500,
        )
    finally:
        # Thread に委譲できなかった経路（検証エラー・例外など）で状態とロックを解放する
        if not thread_started:
            with _state_lock:
                _is_processing = False
            processing_lock.release()
            # temp_dir が作成済みなら掃除する（Thread に委譲したケースは run_process.finally が処理する）
            if temp_dir is not None:
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception as e:
                    logger.warning("temp_dir 削除に失敗 (early return): %s", e)


@app.route("/cancel_process", methods=["POST"])
def cancel_process():
    global cancel_flag
    # current_process を _state_lock 下でスナップショットしてから判定する
    with _state_lock:
        proc = current_process
        cancel_flag = True
    if proc is not None and proc.poll() is None:
        _terminate_then_kill(proc)
        return jsonify({"success": True, "message": "処理をキャンセルしました"})
    else:
        return jsonify({"success": False, "message": "処理中のプロセスがありません"})


@app.route("/cancel_download", methods=["POST"])
def cancel_download():
    """
    実行中の動画ダウンロード subprocess をキャンセルする。

    /cancel_process と同じパターン:
      1. ロック内で proc 参照をスナップショット
      2. ロック外で _terminate_then_kill（subprocess 操作にロックを巻き込まない）
      3. _is_downloading フラグも明示的に False に戻す
         （run_download.finally でも落とすが、UI が即座に状態取得できるよう先回り）
    """
    global _is_downloading, _current_dl_proc
    with _dl_proc_lock:
        proc = _current_dl_proc
    if proc is None or proc.poll() is not None:
        # フラグだけ落とす（プロセスは既に無いので安全）
        with _is_downloading_lock:
            _is_downloading = False
        return jsonify({"success": False, "message": "ダウンロード中のプロセスがありません"})

    _terminate_then_kill(proc)
    # フラグを先回りで落とす（run_download の finally でも落ちるが、UI 応答性のため）
    with _is_downloading_lock:
        _is_downloading = False
    logger.info("🛑 ダウンロードをキャンセルしました")
    return jsonify({"success": True, "message": "ダウンロードをキャンセルしました"})


@app.route("/check-update", methods=["GET"])
def check_update_route():
    result = auto_update.check_update()
    return jsonify(result)


@app.route("/start-update", methods=["POST"])
def start_update_route():
    state = auto_update.get_update_state()
    if state["status"] == "updating":
        return jsonify({"success": False, "message": "すでに更新中です"})
    auto_update.run_update_async()
    return jsonify({"success": True})


@app.route("/update-state", methods=["GET"])
def update_state_route():
    return jsonify(auto_update.get_update_state())


# === ローカルファイル逆探知 API（C 案: アップロード省略のため） ===

@app.route("/api/resolve-local-file", methods=["POST"])
def resolve_local_file_route():
    """
    ブラウザが選択したファイルが Downloads/ に既にあるか調べて、見つかればパスを返す

    Request:
        { "name": "TOPを教えてください.mp4", "size": 4509200000 }

    Response (見つかった場合):
        { "found": true, "path": "C:/Users/kyohei/Downloads/TOPを教えてください/TOPを教えてください.mp4" }

    Response (見つからない場合):
        { "found": false }

    セキュリティ: Downloads/ フォルダ内のみ検索（path traversal 防止）
    """
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    size = data.get("size")

    if not name or size is None:
        return jsonify({"found": False, "error": "name と size が必須"}), 400

    downloads_dir = os.path.realpath(str(Path.home() / "Downloads"))
    if not os.path.isdir(downloads_dir):
        return jsonify({"found": False})

    try:
        target_size = int(size)
    except (TypeError, ValueError):
        return jsonify({"found": False, "error": "size が整数じゃない"}), 400

    # Downloads/ 配下を再帰的に走査して名前 + サイズ一致を探す
    for root, _dirs, files in os.walk(downloads_dir):
        for fname in files:
            if fname != name:
                continue
            full_path = os.path.join(root, fname)
            try:
                if os.path.getsize(full_path) != target_size:
                    continue
            except OSError:
                continue
            # path traversal 防止: realpath が downloads_dir 配下か確認
            real = os.path.realpath(full_path)
            if not real.startswith(downloads_dir + os.sep) and real != downloads_dir:
                continue
            return jsonify({"found": True, "path": real})

    return jsonify({"found": False})


# === ライセンス情報 API（UI 表示用）===

@app.route("/api/license-info", methods=["GET"])
def license_info_route():
    """
    現在のライセンス情報を返す（UI ヘッダー表示用）

    Returns:
        {
            "authenticated": bool,
            "plan": "lite" | "std" | "ext" | None,
            "plan_label": "ライト版" | "スタンダード版" | "拡張無料版" | "未認証",
            "support_active": bool,
            "support_remaining_days": int | None,
            "extension_enabled": bool,
            "machine_slot": int,
            "needs_verification": bool
        }
    """
    # Phase 1 honor system: 認証なしでも「買い切り版」として返す
    # UI ヘッダで「未認証」と出るとユーザーが不安になるため
    if PHASE_1_HONOR_SYSTEM:
        return jsonify({
            "authenticated": True,
            "plan": "single",
            "plan_label": "ClipGift 買い切り版",
            "support_active": True,
            "support_remaining_days": None,
            "extension_enabled": True,
            "machine_slot": 1,
            "needs_verification": False,
        })
    try:
        from licensing import get_plan_gate
        gate = get_plan_gate()
        if gate is None:
            return jsonify({
                "authenticated": False,
                "plan": None,
                "plan_label": "未認証",
                "support_active": False,
                "support_remaining_days": None,
                "extension_enabled": False,
                "machine_slot": 0,
                "needs_verification": True,
            })
        info = gate.to_display_dict()
        info["authenticated"] = True
        return jsonify(info)
    except ImportError:
        # 開発時 licensing モジュール未配置
        return jsonify({
            "authenticated": False,
            "plan": None,
            "plan_label": "開発モード",
            "support_active": False,
            "support_remaining_days": None,
            "extension_enabled": False,
            "machine_slot": 0,
            "needs_verification": False,
        })
    except Exception as e:
        logger.error("license_info 取得エラー: %s", e)
        return jsonify({
            "authenticated": False,
            "plan": None,
            "plan_label": "エラー",
            "error": str(e),
        }), 500


@app.route("/api/license-deactivate", methods=["POST"])
def license_deactivate_route():
    """
    このマシンのライセンスを解除する（再アクティベーション用）

    解除後、credential ファイルを削除して次回起動時に再認証フローへ。
    """
    try:
        from licensing import activation_client, credential_store, get_plan_gate
        gate = get_plan_gate()
        if gate is None:
            return jsonify({
                "success": False,
                "message": "現在ライセンス未認証です",
            }), 400

        # サーバー側でマシンスロット解放
        try:
            remaining = activation_client.deactivate(
                gate.key, gate.machine_fingerprint
            )
        except Exception as e:
            logger.warning("サーバー側 deactivate 失敗（ローカルは削除する）: %s", e)
            remaining = None

        # ローカル credential 削除
        credential_store.delete_credential()

        return jsonify({
            "success": True,
            "message": "ライセンスを解除しました。次回起動時に再認証が必要です。",
            "remaining_slots": remaining,
        })
    except ImportError:
        return jsonify({
            "success": False,
            "message": "licensing モジュールが利用できません",
        }), 503
    except Exception as e:
        logger.error("license_deactivate エラー: %s", e)
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/report_error", methods=["POST"])
def report_error_route():
    """エラー報告ボタンから呼ばれる。Cloudflare Workers /support/report に転送。

    リクエスト JSON:
      - user_email (任意): 返信を希望する場合のメアド
      - user_comment (任意): ユーザーが書いた状況説明

    自動収集する情報:
      - app_version: version.json から
      - error_log: _process_logs から最新 200 行
      - license_tail: ライセンス credential から末尾 4 桁
      - 環境情報: Python / OS / ffmpeg
    """
    try:
        body = request.get_json(silent=True) or {}
        user_email = (body.get("user_email") or "").strip()
        user_comment = (body.get("user_comment") or "").strip()
        attachments = body.get("attachments") or []
        report_type = (body.get("report_type") or "error").strip().lower()
        if report_type not in ("error", "request"):
            report_type = "error"

        # 添付ファイル: 任意（type=error / request どちらも 0 枚 OK）。
        # 上限のみ強制（最大 3 枚 / 各 5MB / 画像のみ）。
        if not isinstance(attachments, list):
            return jsonify({"success": False, "message": "添付ファイル形式が不正です。"}), 400

        if len(attachments) > 3:
            return jsonify({
                "success": False,
                "message": "添付ファイルは最大 3 枚までです。",
            }), 400

        for att in attachments:
            if not isinstance(att, dict):
                return jsonify({"success": False, "message": "添付ファイル形式が不正です。"}), 400
            content_type = att.get("content_type", "")
            if not content_type.startswith("image/"):
                return jsonify({
                    "success": False,
                    "message": "添付できるのは画像ファイルのみです。",
                }), 400

        # バージョン取得（auto_update 経由）
        local_version = auto_update.get_local_version()
        app_version = local_version.get("version", "unknown")

        # 現在のフェーズを判定（ログ送信元の区別用）
        # downloading: DL 中 / processing: クリップ生成中 / idle: 待機中
        # サポート対応で「DL 失敗直後の報告に古いクリップ生成ログが混在する」混乱を避ける目的。
        with _is_downloading_lock:
            is_dl_now = _is_downloading
        with _state_lock:
            is_proc_now = _is_processing
        if is_dl_now:
            current_phase = "downloading"
        elif is_proc_now:
            current_phase = "processing"
        else:
            current_phase = "idle"

        # 直近のプロセスログを取得（フェーズに応じて採取元を切り替え）
        # - processing: クリップ生成ログ
        # - downloading: ダウンロードログ
        # - idle: 両方をマージ（直近の操作からの再現を補助）
        if current_phase == "downloading":
            with _dl_logs_global_lock:
                log_lines = list(_dl_logs_global)
        elif current_phase == "processing":
            with _process_logs_lock:
                log_lines = list(_process_logs)
        else:
            with _process_logs_lock:
                proc_lines = list(_process_logs)
            with _dl_logs_global_lock:
                dl_lines = list(_dl_logs_global)
            # idle 時は両方を採取（区切りを入れて混在をマーク）
            log_lines = []
            if dl_lines:
                log_lines.append("--- [download logs] ---")
                log_lines.extend(dl_lines)
            if proc_lines:
                log_lines.append("--- [process logs] ---")
                log_lines.extend(proc_lines)
        error_log = "\n".join(log_lines)

        # ライセンスキー（credential 化された値、なければ空）
        license_key = ""
        try:
            from licensing import credential_store
            credential = credential_store.load_credential()
            if credential:
                license_key = credential.get("key", "")
        except Exception:
            # 認証情報取得失敗は致命ではない（末尾なしで送信継続）
            pass

        # 追加環境情報
        extra_env: dict = {}
        # フェーズ情報を env_info に同梱（既存 Workers 側のスキーマを壊さず、
        # サポート確認時にどのフェーズで報告されたかを判別できるようにする）
        extra_env["phase"] = current_phase
        try:
            import imageio_ffmpeg
            extra_env["ffmpeg_path"] = "<bundled>"
            extra_env["ffmpeg_pkg"] = imageio_ffmpeg.__version__
        except Exception:
            pass

        # support_center 経由で Workers に送信
        # B-1 修正: 同期送信は最大 15 秒 Flask メインループを占有するため、
        # スレッドで非同期化し、即座に受付応答（accepted）を返す。
        # 送信失敗はログのみ（UI へのリアルタイム通知は行わない＝シンプル路線）。
        from support_center.error_reporter import report_error, ErrorReportError

        def _send_report_async():
            try:
                report_error(
                    app_version=app_version,
                    license_key=license_key,
                    user_email=user_email,
                    user_comment=user_comment,
                    error_log=error_log,
                    extra_env=extra_env,
                    attachments=attachments,
                    report_type=report_type,
                )
            except ErrorReportError as send_err:
                logger.warning("エラー報告送信失敗（非同期）: %s", send_err)
            except Exception as send_err:
                logger.warning("エラー報告送信中に予期せぬ例外（非同期）: %s", send_err)

        threading.Thread(target=_send_report_async, daemon=True).start()

        return jsonify({
            "success": True,
            "accepted": True,
            "message": "エラー報告を受け付けました。サポートチームが内容を確認します。",
            "report_id": "",
        }), 202

    except Exception as e:
        logger.error("report_error_route 例外: %s", e)
        logger.error(traceback.format_exc())
        return jsonify({
            "success": False,
            "message": "エラー報告の処理中に内部エラーが発生しました。",
        }), 500


def _check_python_executable_or_exit():
    """起動時に Python 実行ファイルの可用性を検証する。
    クリップ生成は subprocess.Popen で別 Python プロセスを起動するため、
    パス解決が失敗すると WinError 2 で握り潰される。
    ここで早期に検出してユーザーに分かりやすく案内する。
    """
    try:
        python_exe = get_python_exe()
    except Exception as exc:  # pragma: no cover - 防御的
        logger.error("Python パス解決中に例外: %s", exc)
        python_exe = None

    ok = False
    if python_exe:
        try:
            result = subprocess.run(
                [python_exe, "-c", "print('ok')"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            ok = (result.returncode == 0 and "ok" in (result.stdout or ""))
            if not ok:
                logger.error(
                    "Python 動作確認 NG: rc=%s stdout=%s stderr=%s",
                    result.returncode,
                    (result.stdout or "")[:200],
                    (result.stderr or "")[:200],
                )
        except FileNotFoundError as exc:
            logger.error("Python 実行ファイルが見つかりません: %s (%s)", python_exe, exc)
        except subprocess.TimeoutExpired:
            logger.error("Python 動作確認がタイムアウトしました: %s", python_exe)
        except Exception as exc:
            logger.error("Python 動作確認中に予期せぬエラー: %s", exc)

    if ok:
        logger.info("Python 動作確認 OK: %s", python_exe)
        return

    # NG → ユーザーへダイアログ表示してから終了
    msg_title = "ClipGift 起動エラー"
    msg_body = (
        "Python が正しくインストールされていません。\n"
        "ClipGift のインストーラーを再実行してください。\n\n"
        "問題が解決しない場合は support@clipgift.app までご連絡ください。"
    )
    # tkinter.messagebox を使う（Windows 標準で追加依存なし）
    # licensing/ui_dialogs はブラウザ前提の対話 UI で重いため、起動失敗の単純な通知には不向き。
    try:
        import tkinter as _tk
        from tkinter import messagebox as _mb
        _root = _tk.Tk()
        _root.withdraw()
        _mb.showerror(msg_title, msg_body)
        _root.destroy()
    except Exception as dialog_err:
        logger.error("ダイアログ表示に失敗: %s", dialog_err)
        # フォールバック: Windows の msg コマンド
        try:
            subprocess.run(
                ["msg", "*", f"{msg_title}: {msg_body}"],
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            # それも失敗したらログのみ（既に上で error 出力済み）
            pass

    sys.exit(1)


if __name__ == "__main__":

    # 前回の自動更新が中断していた場合は .bak からロールバックする
    auto_update.check_and_recover_from_failed_update()

    # Python 実行ファイルの可用性チェック（NG ならダイアログ表示して終了）
    # ライセンス認証より先に行い、subprocess 起動不能な状態で先へ進ませない。
    _check_python_executable_or_exit()

    # ライセンス認証チェック
    # - Phase 1 (BOOTH 買い切り版) は PHASE_1_HONOR_SYSTEM=True で常に bypass（honor system 運用）
    # - 開発用バイパス: 環境変数 CLIPGIFT_SKIP_LICENSE=1 でもスキップ可能
    # - Phase 2 (サブスク移行時) は PHASE_1_HONOR_SYSTEM=False に戻して既存ロジック復活
    if PHASE_1_HONOR_SYSTEM or os.environ.get("CLIPGIFT_SKIP_LICENSE") == "1":
        logger.info("ライセンス認証: OFF (Phase 1 honor system / BOOTH 買い切り版)")
    else:
        try:
            from licensing import authenticate_or_block
            _auth_result = authenticate_or_block()
            if not _auth_result.ok:
                logger.warning("ライセンス認証 NG: %s", _auth_result.message)
                sys.exit(0)
            logger.info("ライセンス認証 OK: plan=%s", _auth_result.plan)
        except ImportError:
            # licensing モジュール未配置の開発時はスキップ
            logger.warning("licensing モジュールが見つかりません、認証スキップ")
        except Exception as _auth_err:
            logger.error("ライセンス認証中にエラー: %s", _auth_err)
            sys.exit(1)

    # サーバー起動回数チェック&一時ディレクトリ削除
    check_and_increment_start_count()

    # ClipGiftLog.ico の自動復元（消失していたら installer_assets から復元）
    # 過去にショートカットアイコン参照エラーが発生したため起動時に保証する
    try:
        _icon_target = os.path.join(BASE_DIR, "ClipGiftLog.ico")
        if not os.path.exists(_icon_target):
            _icon_source = os.path.join(BASE_DIR, "installer_assets", "ClipGiftLog.ico")
            if os.path.exists(_icon_source):
                import shutil as _sh
                _sh.copy2(_icon_source, _icon_target)
                logger.info("ClipGiftLog.ico を installer_assets から復元しました")
            else:
                logger.warning("ClipGiftLog.ico が消失していますが、復元元も見つかりません")
    except Exception as _e:
        logger.warning("ClipGiftLog.ico の復元に失敗: %s", _e)

    # BASE_DIR直下の不要なprogress.jsonを削除
    stale_progress = os.path.join(BASE_DIR, "progress.json")
    if os.path.exists(stale_progress):
        try:
            os.remove(stale_progress)
        except Exception:
            pass

    # === 多重起動防止 ===
    # 既に同 port で稼働中の ClipGift があればブラウザを開いて自プロセスは終了する。
    # （古いプロセス累積による 500 系の不整合を防ぐ + 二重起動でユーザーが混乱する事故を防ぐ）
    def _is_already_running(host: str, port: int) -> bool:
        import socket
        try:
            with socket.create_connection((host, port), timeout=0.8):
                return True
        except (OSError, socket.timeout):
            return False

    if _is_already_running(config.SERVER_HOST, config.SERVER_PORT):
        logger.warning(
            "⚠️ 既に ClipGift が %s:%s で稼働中です。既存ウィンドウへブラウザを開きます。",
            config.SERVER_HOST, config.SERVER_PORT,
        )
        if os.environ.get("LAUNCHED_BY_VBS") != "1":
            import webbrowser
            webbrowser.open(f"http://{config.SERVER_HOST}:{config.SERVER_PORT}")
        sys.exit(0)

    # vbs経由 / 再起動経由でない（コマンドライン直接起動）場合はブラウザを開く
    # LAUNCHED_BY_RESTART=1 の場合は古いタブが location.reload() で接続するので新タブ不要
    if (
        os.environ.get("LAUNCHED_BY_VBS") != "1"
        and os.environ.get("LAUNCHED_BY_RESTART") != "1"
    ):
        import webbrowser
        import threading
        def _open_browser():
            import time
            time.sleep(1.5)
            webbrowser.open(f"http://{config.SERVER_HOST}:{config.SERVER_PORT}")
        threading.Thread(target=_open_browser, daemon=False).start()

    app.run(debug=False, host=config.SERVER_HOST, port=config.SERVER_PORT)
