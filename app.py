import os
import glob
import subprocess
import sys
import re
from flask import Flask, request, jsonify, render_template, send_from_directory, send_file
import traceback
import threading
import platform
import json
import io
import csv
import tempfile
import time
import shutil
import hashlib
import webbrowser
import auto_update
# === chatbunseki.py インライン ===
import unicodedata
from datetime import timedelta


def parse_time_to_seconds(t):
    try:
        parts = t.strip().split(":")
        if len(parts) == 2:
            m, s = map(int, parts)
            return m * 60 + s
        elif len(parts) == 3:
            h, m, s = map(int, parts)
            return h * 3600 + m * 60 + s
        else:
            raise ValueError(f"不正な時間形式: {t}")
    except Exception as e:
        print(f"[parse_time_to_seconds] エラー: {e} ({t})")
        return None


def format_seconds_to_time(s):
    if isinstance(s, str):
        s = float(s)
    td = timedelta(seconds=int(s))
    total_seconds = int(td.total_seconds())
    m, s = divmod(total_seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h > 0 else f"{m}:{s:02}"


def normalize_comment(comment):
    comment = comment.replace("ｗ", "w")
    comment = unicodedata.normalize("NFKC", comment)
    return comment.lower()


def analyze_chat_single_keyword(lines, keyword, start_threshold, end_threshold, clip_offset):
    print(f"🎯 キーワード: {keyword}")
    normalized_kw = normalize_comment(keyword)
    pattern = re.compile(re.escape(normalized_kw), re.IGNORECASE)

    hit_times = []
    for time_str, comment in lines:
        sec = parse_time_to_seconds(time_str)
        if sec is None:
            continue
        if pattern.search(normalize_comment(comment)):
            hit_times.append({"sec": sec, "comment": comment})

    if not hit_times:
        return []

    time_list = [t["sec"] for t in hit_times]
    clips = []
    max_time = time_list[-1]

    i = 0
    while i <= max_time:
        count = sum(1 for t in time_list if i <= t < i + 10)
        if count >= start_threshold:
            clip_start = max(0, i - clip_offset)
            zero_count = 0
            j = i + 10
            found_end = False
            while j <= max_time + 10:
                c = sum(1 for t in time_list if j <= t < j + 10)
                if c <= end_threshold:
                    zero_count += 1
                    if zero_count >= 3:
                        clip_end = j + 10
                        found_end = True
                        break
                else:
                    zero_count = 0
                j += 10
            if not found_end:
                clip_end = time_list[-1] + 10
            hit_logs = [
                f"{format_seconds_to_time(t['sec'])} → {t['comment']}"
                for t in hit_times
                if clip_start <= t["sec"] <= clip_end
            ]
            clips.append({"start": clip_start, "end": clip_end, "hitLogs": hit_logs})
            i = clip_end
        else:
            i += 1

    return clips


def merge_clips(clips):
    if not clips:
        return []
    clips.sort(key=lambda x: x["start"])
    merged = [clips[0]]
    for clip in clips[1:]:
        last_clip = merged[-1]
        if clip["start"] > clip["end"]:
            clip["start"], clip["end"] = clip["end"], clip["start"]
        if clip["start"] <= last_clip["end"]:
            last_clip["end"] = max(last_clip["end"], clip["end"])
            last_clip["hitLogs"].extend(clip.get("hitLogs", []))
        else:
            merged.append(clip)
    return merged


def analyze_chat(lines, keywords, start_threshold, end_threshold, clip_offset, video_duration_sec=None):
    print("📦 キーワード:", keywords)
    print("📈 コメント総数:", len(lines))
    print("🎥 動画長さ(秒):", video_duration_sec)

    all_clips = []
    for kw in keywords:
        all_clips.extend(
            analyze_chat_single_keyword(lines, kw, start_threshold, end_threshold, clip_offset)
        )

    merged = merge_clips(all_clips)

    if video_duration_sec is not None:
        for clip in merged:
            if clip["end"] > video_duration_sec:
                clip["end"] = video_duration_sec

    return [
        {
            "start": c["start"],
            "end": c["end"],
            "start_str": format_seconds_to_time(c["start"]),
            "end_str": format_seconds_to_time(c["end"]),
            "hitLogs": c.get("hitLogs", []),
        }
        for c in merged
    ]
# === chatbunseki.py インライン終わり ===
import imageio_ffmpeg

from pathlib import Path
from werkzeug.utils import secure_filename

# BASE_DIRを先頭で定義
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
os.makedirs(BIN_DIR, exist_ok=True)

START_COUNT_FILE = os.path.join(BASE_DIR, "server_start_count.txt")
LAST_FONT_FILE = os.path.join(BIN_DIR, "last_font.json")


def get_python_exe():
    """コンソールなしのpythonw.exeを優先して返す"""

    def _resolve(path):
        """python.exe → pythonw.exe に変換して存在すれば返す"""
        pythonw = path.replace("python.exe", "pythonw.exe")
        if os.path.exists(pythonw):
            return pythonw
        if os.path.exists(path):
            return path
        return None

    # ① インストール時に記録したパスを最優先
    python_path_file = os.path.join(BIN_DIR, "python_path.txt")
    if os.path.exists(python_path_file):
        try:
            with open(python_path_file, "r", encoding="utf-8") as pf:
                recorded = pf.read().strip()
            if recorded:
                result = _resolve(recorded)
                if result:
                    return result
        except Exception:
            pass

    # ② sys.executableがpython*.exeなら使う（通常の.py実行時）
    exe = sys.executable
    if "python" in os.path.basename(exe).lower() and exe.endswith(".exe"):
        result = _resolve(exe)
        if result:
            return result

    # ③ exe化されている場合: PYTHONHOME環境変数から探す
    pythonhome = os.environ.get("PYTHONHOME", "")
    if pythonhome:
        candidate = os.path.join(pythonhome, "python.exe")
        result = _resolve(candidate)
        if result:
            return result

    # ④ exe化されている場合: sys.executableと同じフォルダにpython.exeがあるか確認
    exe_dir = os.path.dirname(exe)
    candidate = os.path.join(exe_dir, "python.exe")
    result = _resolve(candidate)
    if result:
        return result

    # ⑤ PATHからpythonを探す（最終フォールバック）
    python_in_path = shutil.which("pythonw") or shutil.which("python")
    if python_in_path:
        return python_in_path

    # ⑥ 何も見つからなければsys.executableをそのまま返す
    return exe


def get_font_dirs():
    """システム・ユーザーフォントフォルダを返す"""
    dirs = []
    windir = os.environ.get("WINDIR", "C:/Windows")
    dirs.append(os.path.join(windir, "Fonts"))
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        dirs.append(os.path.join(localappdata, "Microsoft", "Windows", "Fonts"))
    return dirs


def get_font_japanese_name(path):
    """フォントファイルから日本語表示名を取得する。日本語名がなければNoneを返す"""
    try:
        from fontTools.ttLib import TTFont
        font = TTFont(path, fontNumber=0)
        name_table = font["name"]
        # nameID=4: Full name, nameID=1: Family name
        # langID=0x411(日本語) または platformID=1+langID=11(Mac日本語) を優先
        for target_id in (4, 1):
            # Windows日本語(platformID=3, langID=0x411)
            for record in name_table.names:
                if record.nameID == target_id and record.platformID == 3 and record.langID == 0x411:
                    try:
                        name = record.toUnicode()
                        if name:
                            return name
                    except Exception:
                        pass
            # Mac日本語(platformID=1, langID=11)
            for record in name_table.names:
                if record.nameID == target_id and record.platformID == 1 and record.langID == 11:
                    try:
                        name = record.toUnicode()
                        if name:
                            return name
                    except Exception:
                        pass
    except Exception:
        pass
    return None


def list_fonts():
    """日本語名を持つフォントのみ返す"""
    fonts = []
    for d in get_font_dirs():
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if fname.lower().endswith((".ttf", ".otf", ".ttc")):
                path = os.path.join(d, fname)
                display_name = get_font_japanese_name(path)
                if display_name:  # 日本語名があるもののみ
                    fonts.append({"name": fname, "display_name": display_name, "path": path})
    # 重複除去（ファイル名優先）
    seen = set()
    unique = []
    for f in fonts:
        if f["name"] not in seen:
            seen.add(f["name"])
            unique.append(f)
    # 表示名でソート
    unique.sort(key=lambda x: x["display_name"].lower())
    return unique


def load_last_font():
    try:
        with open(LAST_FONT_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("font_name", "")
    except Exception:
        return ""


def save_last_font(font_name):
    try:
        with open(LAST_FONT_FILE, "w", encoding="utf-8") as f:
            json.dump({"font_name": font_name}, f, ensure_ascii=False)
    except Exception as e:
        print(f"[save_last_font] エラー: {e}")


def cleanup_temp_files_and_dirs():
    """
    不要な一時ファイル・ディレクトリをすべて削除（clipgen_*系や.mp3/.wavなど）
    """
    temp_root = tempfile.gettempdir()

    # 削除対象（拡張子ファイル + clipgen_*系フォルダ）
    patterns = [
        os.path.join(temp_root, "clipgen_*"),
        os.path.join(temp_root, "*.mp3"),
        os.path.join(temp_root, "*.wav"),
        os.path.join(temp_root, "*.tmp"),
        os.path.join(temp_root, "*.json"),
    ]

    deleted_count = 0

    for pattern in patterns:
        for path in glob.glob(pattern):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                    print(f"🗑️ 一時ディレクトリ削除: {path}")
                else:
                    os.remove(path)
                    print(f"🗑️ 一時ファイル削除: {path}")
                deleted_count += 1
            except PermissionError as e:
                if hasattr(e, "winerror") and e.winerror == 32:
                    # 他プロセスが使用中 → ログを出さずスキップ
                    continue
                else:
                    print(f"⚠️ PermissionError: {path}")
                    traceback.print_exc()
            except Exception as e:
                print(f"⚠️ 削除エラー: {path}: {e}")
                traceback.print_exc()

    print(f"✅ 合計削除数: {deleted_count}")


def check_and_increment_start_count():
    """
    起動回数をカウントし、2回に達したら一時ファイル削除
    """
    count = 0

    if os.path.exists(START_COUNT_FILE):
        try:
            with open(START_COUNT_FILE, "r", encoding="utf-8") as f:
                count = int(f.read().strip())
        except Exception:
            print("⚠️ 起動回数読み取りエラー。初期化します")
            count = 0

    count += 1
    print(f"🔢 起動回数: {count}/2")

    if count >= 2:
        print("🔄 起動回数2回に達したため一時ファイルを全削除します")
        cleanup_temp_files_and_dirs()
        count = 0  # カウントをリセット

    try:
        with open(START_COUNT_FILE, "w", encoding="utf-8") as f:
            f.write(str(count))
    except Exception as e:
        print(f"⚠️ 起動回数の保存に失敗: {e}")



# Flaskアプリ初期化 (テンプレート・スタティック指定)
import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)




def generate_temp_audio_name(filename, clip_start, clip_end):
    """
    同じファイル・範囲なら同じ名前を返す
    """
    base = f"{filename}_{clip_start}_{clip_end}"
    hashed = hashlib.md5(base.encode("utf-8")).hexdigest()
    return f"clipgen_{hashed}.mp3"


# ハートビート管理
_last_heartbeat = time.time()
_heartbeat_lock = threading.Lock()

def _heartbeat_watchdog():
    """ハートビートが途絶えたらサーバーを終了する"""
    while True:
        time.sleep(1)
        with _heartbeat_lock:
            elapsed = time.time() - _last_heartbeat
        if elapsed > 3:
            print("💤 ブラウザが閉じられました。サーバーを終了します。", flush=True)
            os._exit(0)

# watchdogスレッドをデーモンとして起動
_watchdog_thread = threading.Thread(target=_heartbeat_watchdog, daemon=True)
_watchdog_thread.start()


@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    global _last_heartbeat
    with _heartbeat_lock:
        _last_heartbeat = time.time()
    return jsonify({"ok": True})


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
        # ① ファイル存在チェック
        if "chatFile" not in request.files:
            return jsonify({"error": "チャットファイルが見つかりません"}), 400

        file = request.files["chatFile"]

        # ② CSVデコード（UTF-8優先 → Shift-JISフォールバック）
        try:
            stream = io.StringIO(file.stream.read().decode("utf-8"))
        except UnicodeDecodeError:
            file.stream.seek(0)
            stream = io.StringIO(file.stream.read().decode("shift_jis"))

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
        print(f"[analyze_chat_csv] エラー: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/extract_audio", methods=["POST"])
def extract_audio():
    try:
        if "video" not in request.files:
            return jsonify({"error": "動画ファイルがありません"}), 400

        video_file = request.files["video"]
        clip_start = float(request.form.get("start", 0))
        clip_end = float(request.form.get("end", 60))

        temp_dir = tempfile.gettempdir()
        audio_name = generate_temp_audio_name(video_file.filename, clip_start, clip_end)
        audio_path = os.path.join(temp_dir, audio_name)

        if os.path.exists(audio_path):
            print(f"🎵 キャッシュヒット: {audio_path}")
            return send_file(audio_path, mimetype="audio/mpeg")

        fd, temp_video_path = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        video_file.save(temp_video_path)

        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
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
            print("FFmpegエラー:", result.stderr)
            return jsonify({"error": "音声抽出失敗", "detail": result.stderr}), 500

        try:
            os.remove(temp_video_path)
        except Exception:
            pass

        return send_file(audio_path, mimetype="audio/mpeg")

    except Exception as e:
        print(f"[extract_audio] エラー: {e}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/downloads/<path:filename>")
def serve_downloads(filename):
    downloads_dir = str(Path.home() / "Downloads")
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
                errors="replace",
            )
            output_lines = []
            for line in iter(proc.stdout.readline, ""):
                line = line.rstrip()
                if not line:
                    continue
                print(f"[DL] {line}", flush=True)
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
            # フロントが完了を読み取るまで5秒待ってから削除
            time.sleep(5)
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

    thread = threading.Thread(target=run_download, daemon=True)
    thread.start()

    return jsonify({
        "success": True,
        "message": "ダウンロードを開始しました",
        "progress_path": dl_progress_path,
    })


@app.route("/progress", methods=["GET"])
def get_progress():
    progress_path = request.args.get("path")
    if not progress_path or not os.path.exists(progress_path):
        return jsonify({"progress": 0, "message": "未開始"})
    try:
        with open(progress_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"progress": -1, "message": f"エラー: {str(e)}"})


@app.route("/get-progress-file", methods=["GET"])
def get_progress_file():
    progress_path = request.args.get("path")
    if not progress_path or not os.path.exists(progress_path):
        return jsonify({"progress": 0, "message": "未開始"})
    # atomic renameと競合しないようリトライ最大3回
    last_err = None
    for _ in range(3):
        try:
            with open(progress_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # logsはメモリから注入（ファイルへの競合書き込みを完全回避）
            with _dl_logs_global_lock:
                data["logs"] = list(_dl_logs_global)
            return jsonify(data)
        except (json.JSONDecodeError, OSError) as e:
            last_err = e
            time.sleep(0.05)  # 50ms待ってリトライ
    return jsonify({"progress": 0, "message": "読み取り待機中"})


processing_lock = threading.Lock()
current_process = None
cancel_flag = False
current_clip_index = 0
_process_logs = []
_process_logs_lock = threading.Lock()
_PROCESS_LOG_MAX = 200

# ダウンロードログ（グローバル管理）
_dl_logs_global = []
_dl_logs_global_lock = threading.Lock()


@app.route("/process_clips", methods=["POST"])
def process_clips():
    global current_process, cancel_flag, current_clip_index
    print("🚀 リクエスト受信時間:", time.strftime("%Y-%m-%d %H:%M:%S"))
    print("💡 request.content_length:", request.content_length)

    if not processing_lock.acquire(blocking=False):
        return (
            jsonify(
                {"success": False, "message": "現在処理中です。完了までお待ちください"}
            ),
            200,
        )

    try:
        print("✅ /process_clips エンドポイント呼び出し", flush=True)

        video_file = request.files.get("video")
        chat_file = request.files.get("chat")
        clips_json = request.form.get("clips")

        print("💡 受信したclips_json文字列:", clips_json)

        if not video_file or not chat_file or not clips_json:
            processing_lock.release()
            return jsonify({"error": "必要なデータが不足しています"}), 400

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
        print(f"📝 一時ディレクトリ作成: {temp_dir}")

        video_path = os.path.join(temp_dir, "input.mp4")
        chat_path = os.path.join(temp_dir, "chat.csv")
        progress_file = os.path.join(temp_dir, "progress.json")

        video_file.save(video_path)
        chat_file.save(chat_path)

        print("💡 clipsの数:", len(clips))
        print("💡 clipsの中身:", json.dumps(clips, ensure_ascii=False))

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
            global current_process, cancel_flag, current_clip_index
            with _process_logs_lock:
                _process_logs.clear()
            try:
                for idx, clip in enumerate(clips, 1):
                    _append_log(f"▶ クリップ {idx}/{len(clips)} 開始")
                    print(f"▶ ループ開始 idx={idx}, cancel_flag={cancel_flag}")

                    if cancel_flag:
                        _write_progress({"progress": -1, "message": "キャンセルされました", "current_clip": idx})
                        print("🛑 キャンセル検知、処理中断")
                        break

                    current_clip_index = idx
                    clip_title = clip.get("title", f"クリップ{idx}")
                    clip_path = os.path.join(temp_dir, f"clip_{idx}.json")

                    with open(clip_path, "w", encoding="utf-8") as f:
                        json.dump([clip], f, ensure_ascii=False)

                    print(f"▶ {clip_title}: サブプロセス開始")

                    current_process = subprocess.Popen(
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
                        errors="replace",
                    )

                    output_lines = []
                    for line in iter(current_process.stdout.readline, ""):
                        if cancel_flag:
                            current_process.terminate()
                            _write_progress({"progress": -1, "message": "キャンセルにより終了", "current_clip": idx})
                            print("🛑 subprocessを強制終了")
                            break
                        line = line.strip()
                        if not line:
                            continue
                        print("📢 mp4inchatnagasi:", line)
                        _append_log(line)
                        output_lines.append(line)

                    retcode = current_process.wait()
                    print(f"✅ wait()終了 retcode={retcode}")

                    if retcode != 0 and not cancel_flag:
                        error_output = "\n".join(output_lines)
                        print("❌ サブプロセスがエラー終了しました:")
                        print(error_output)
                        raise subprocess.CalledProcessError(
                            retcode, current_process.args, output=error_output
                        )

                    if not cancel_flag:
                        print(f"✅ {clip_title}: 処理完了")

                if not cancel_flag:
                    _append_log("✅ 全クリップ処理完了")
                    print("✅ 全クリップ処理完了")
                    _write_progress({"progress": 100, "message": "全クリップ処理完了", "current_clip": len(clips), "all_done": True})
                    time.sleep(5)
                    try:
                        os.remove(progress_file)
                    except Exception:
                        pass

            except Exception as e:
                print("❌ run_process全体でエラー:", e)
                try:
                    _write_progress({"progress": -1, "message": f"全体失敗: {str(e)}", "current_clip": 0})
                except Exception as e2:
                    print("⚠️ 進捗ファイル書き込みで再エラー:", e2)
            finally:
                current_process = None
                current_clip_index = 0
                processing_lock.release()

        threading.Thread(target=run_process, daemon=True).start()

        return jsonify({"success": True, "progress_path": progress_file})

    except Exception:
        processing_lock.release()
        return (
            jsonify(
                {
                    "error": "サーバー内部でエラーが発生しました",
                    "details": traceback.format_exc(),
                }
            ),
            500,
        )


@app.route("/cancel_process", methods=["POST"])
def cancel_process():
    global cancel_flag, current_process
    if current_process and current_process.poll() is None:
        cancel_flag = True
        current_process.terminate()
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
            webbrowser.open("http://127.0.0.1:5000")
        threading.Thread(target=_open_browser, daemon=False).start()

    app.run(debug=False, port=5000)
