import os
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
from font_manager import list_fonts, load_last_font, save_last_font
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

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

# --- アップロード制限・拡張子ホワイトリスト（config.py で定義） ---
MAX_UPLOAD_BYTES = config.MAX_UPLOAD_BYTES
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

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

def _heartbeat_watchdog():
    """ハートビートが途絶えたらサーバーを終了する"""
    while True:
        time.sleep(config.WATCHDOG_INTERVAL_SEC)
        with _heartbeat_lock:
            elapsed = time.time() - _last_heartbeat
        # 更新中はwatchdogを無効化
        try:
            state = auto_update.get_update_state()
            if state.get("status") == "updating":
                continue
        except Exception:
            pass
        # ダウンロード中はwatchdogを無効化
        with _is_downloading_lock:
            if _is_downloading:
                continue
        # クリップ処理中はwatchdogを無効化（フラグをロック内で原子的にチェック）
        with _state_lock:
            if _is_processing:
                continue
        if elapsed > config.HEARTBEAT_TIMEOUT_SEC:
            logger.info("💤 ブラウザが閉じられました。サーバーを終了します。")
            os._exit(0)

# watchdogスレッドをデーモンとして起動（起動直後のfalse positiveを防ぐため遅延）
def _start_watchdog_delayed():
    time.sleep(config.WATCHDOG_START_DELAY_SEC)
    _heartbeat_watchdog()

_watchdog_thread = threading.Thread(target=_start_watchdog_delayed, daemon=True)
_watchdog_thread.start()


@app.route('/favicon.ico')
def favicon():
    return '', 204


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




@app.route("/get-fonts", methods=["GET"])
def get_fonts():
    fonts = list_fonts()
    last = load_last_font()
    return jsonify({"fonts": fonts, "last_font": last})


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

    # 出力先
    downloads_dir = str(Path.home() / "Downloads")
    os.makedirs(downloads_dir, exist_ok=True)

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
        global _is_downloading
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
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
                text=True,
                encoding="utf-8",
                errors="backslashreplace",
            )
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
            if proc.returncode != 0:
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

        video_file = request.files.get("video")
        chat_file = request.files.get("chat")
        clips_json = request.form.get("clips")

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

        downloads_dir = str(Path.home() / "Downloads")
        os.makedirs(downloads_dir, exist_ok=True)

        temp_dir = tempfile.mkdtemp(prefix="clipgen_")
        logger.info("📝 一時ディレクトリ作成: %s", temp_dir)

        video_path = os.path.join(temp_dir, "input.mp4")
        chat_path = os.path.join(temp_dir, "chat.csv")
        progress_file = os.path.join(temp_dir, "progress.json")

        video_file.save(video_path)
        chat_file.save(chat_path)

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
                        error_output = "\n".join(output_lines)
                        logger.error("❌ サブプロセスがエラー終了しました:\n%s", error_output)
                        raise subprocess.CalledProcessError(
                            retcode, proc.args, output=error_output
                        )

                    if not is_cancelled:
                        logger.info("✅ %s: 処理完了", clip_title)

                with _state_lock:
                    is_cancelled = cancel_flag
                if not is_cancelled:
                    _append_log("✅ 全クリップ処理完了")
                    logger.info("✅ 全クリップ処理完了")
                    _write_progress({"progress": 100, "message": "全クリップ処理完了", "current_clip": len(clips), "all_done": True})
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


if __name__ == "__main__":

    # 前回の自動更新が中断していた場合は .bak からロールバックする
    auto_update.check_and_recover_from_failed_update()

    # サーバー起動回数チェック&一時ディレクトリ削除
    check_and_increment_start_count()

    # BASE_DIR直下の不要なprogress.jsonを削除
    stale_progress = os.path.join(BASE_DIR, "progress.json")
    if os.path.exists(stale_progress):
        try:
            os.remove(stale_progress)
        except Exception:
            pass

    # vbs経由でない（コマンドライン直接起動）場合はブラウザを開く
    if os.environ.get("LAUNCHED_BY_VBS") != "1":
        import webbrowser
        import threading
        def _open_browser():
            import time
            time.sleep(1.5)
            webbrowser.open(f"http://{config.SERVER_HOST}:{config.SERVER_PORT}")
        threading.Thread(target=_open_browser, daemon=False).start()

    app.run(debug=False, host=config.SERVER_HOST, port=config.SERVER_PORT)
