import sys
import os
import re
import subprocess
import tempfile
import shutil
import json
import unicodedata
import time
import requests
import csv as csv_module

from system_utils import get_ffmpeg_path, get_ffprobe_path

# === youtubeChatdl.py インライン ===

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

# --- ネットワーク設定（ハードコード撤廃 + 指数バックオフ） ---
HTML_FETCH_TIMEOUT_SEC = 20
HTML_FETCH_RETRIES = 3

CHAT_FETCH_TIMEOUT_SEC = 60
CHAT_FETCH_RETRIES = 5
CHAT_FETCH_BACKOFF_BASE_SEC = 1.0
CHAT_FETCH_BACKOFF_CAP_SEC = 30.0

# 各チャットバッチ取得後のスロットリング（YouTube 側への過剰アクセス防止）
CHAT_BATCH_SLEEP_SEC = 0.08

# 再試行しても意味がない（恒久的な）HTTP ステータス
_NON_RETRYABLE_STATUS = {400, 401, 403, 404, 410}


def _compute_backoff(attempt, retry_after=None,
                     base=CHAT_FETCH_BACKOFF_BASE_SEC,
                     cap=CHAT_FETCH_BACKOFF_CAP_SEC):
    """
    指数バックオフの待機秒数を計算する。
    - attempt: 0 始まり（1 回目の再試行は attempt=0）
    - retry_after: サーバーが返した Retry-After 値（秒）。あれば優先
    - 上限は cap 秒
    """
    if retry_after is not None:
        try:
            return min(cap, max(0.0, float(retry_after)))
        except (TypeError, ValueError):
            pass
    return min(cap, base * (2 ** attempt))


def _fetch_html(url):
    """HTML を取得する。503/タイムアウト等で指数バックオフ再試行する。"""
    headers = {"User-Agent": USER_AGENT}
    last_err = None
    for attempt in range(HTML_FETCH_RETRIES):
        try:
            r = requests.get(url, headers=headers, timeout=HTML_FETCH_TIMEOUT_SEC)
            if r.status_code in _NON_RETRYABLE_STATUS:
                r.raise_for_status()  # 即例外（再試行しない）
            r.raise_for_status()
            return r.text
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in _NON_RETRYABLE_STATUS:
                raise
            last_err = e
        except requests.exceptions.RequestException as e:
            last_err = e
        if attempt < HTML_FETCH_RETRIES - 1:
            delay = _compute_backoff(attempt)
            print(f"⚠️ HTML 取得失敗（{type(last_err).__name__}）{delay:.1f}s 待って再試行 {attempt+1}/{HTML_FETCH_RETRIES}", flush=True)
            time.sleep(delay)
    raise RuntimeError(f"HTML 取得に失敗（{HTML_FETCH_RETRIES} 回試行）: {last_err}")


def _extract_params(html):
    key_m = re.search(r'INNERTUBE_API_KEY["\']\s*:\s*"([^"]+)"', html)
    ver_m = re.search(r'INNERTUBE_CONTEXT_CLIENT_VERSION["\']\s*:\s*"([^"]+)"', html)
    yid_m = re.search(r'ytInitialData["\']?\s*[:=]\s*(\{.*?\})[;\n]', html, flags=re.DOTALL)
    api_key = key_m.group(1) if key_m else None
    version = ver_m.group(1) if ver_m else "2.20201021.03.00"
    yid = json.loads(yid_m.group(1)) if yid_m else None
    return api_key, version, yid


def _find_continuation(ytInitialData):
    continuations = []

    def walk(d):
        if isinstance(d, dict):
            if "continuation" in d:
                continuations.append(d["continuation"])
            for v in d.values():
                walk(v)
        elif isinstance(d, list):
            for i in d:
                walk(i)

    walk(ytInitialData)
    for c in continuations:
        if '"playerSeekStartTimeMs":"0"' in str(c):
            return c
    for c in continuations:
        if "liveChatReplayContinuationData" in str(c):
            return c
    return continuations[0] if continuations else None


def _fetch_chat(api_key, version, continuation, retries=CHAT_FETCH_RETRIES):
    """
    チャット取得リクエスト。指数バックオフ（1, 2, 4, 8, 16... 秒、上限 30s）で再試行する。
    429 の場合は Retry-After ヘッダを優先して尊重する。
    """
    url = f"https://www.youtube.com/youtubei/v1/live_chat/get_live_chat_replay?key={api_key}"
    data = {
        "context": {"client": {"clientName": "WEB", "clientVersion": version}},
        "continuation": continuation,
    }
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, json=data, timeout=CHAT_FETCH_TIMEOUT_SEC)
            # 恒久的な 4xx は即失敗（再試行しても無駄）
            if r.status_code in _NON_RETRYABLE_STATUS:
                r.raise_for_status()
            # 429 は Retry-After を尊重して待つ（後段で処理するため HTTPError として扱う）
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status in _NON_RETRYABLE_STATUS:
                raise
            retry_after = e.response.headers.get("Retry-After") if e.response is not None else None
            last_err = e
            if attempt < retries - 1:
                delay = _compute_backoff(attempt, retry_after=retry_after)
                print(f"⚠️ HTTP {status} — {delay:.1f}s 待って再試行 {attempt+1}/{retries}", flush=True)
                time.sleep(delay)
        except requests.exceptions.RequestException as e:
            last_err = e
            if attempt < retries - 1:
                delay = _compute_backoff(attempt)
                print(f"⚠️ {type(e).__name__} — {delay:.1f}s 待って再試行 {attempt+1}/{retries}", flush=True)
                time.sleep(delay)
    raise RuntimeError(f"❌ チャット取得に失敗（{retries} 回試行）: {last_err}")


def _ms_to_timestamp(ms):
    try:
        s = int(ms) // 1000
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"
    except:
        return "0:00"


def _parse_messages(actions):
    messages = []
    latest_offset = 0
    for a in actions or []:
        if "replayChatItemAction" in a:
            item = a["replayChatItemAction"].get("actions", [{}])[0]
            chat = item.get("addChatItemAction", {}).get("item", {})
            for t in ("liveChatTextMessageRenderer", "liveChatPaidMessageRenderer"):
                if t in chat:
                    r = chat[t]
                    author = r.get("authorName", {}).get("simpleText", "").replace("@", "").strip()
                    if not author:
                        continue
                    msg_runs = r.get("message", {}).get("runs", [])
                    msg = "".join([x.get("text", "") for x in msg_runs]).strip()
                    if not msg:
                        continue
                    offset = 0
                    time_text = "0:00"
                    if "videoOffsetTimeMsec" in r:
                        try:
                            offset = int(float(r["videoOffsetTimeMsec"]))
                            if offset < 0:
                                continue
                            time_text = _ms_to_timestamp(offset)
                        except:
                            pass
                    elif "timestampText" in r:
                        time_text = r["timestampText"].get("simpleText", "0:00").strip()
                        if time_text.startswith("-"):
                            continue
                    msg = re.sub(r"[\x00-\x1F\x7F]", "", msg)
                    messages.append((time_text, author, msg, offset))
                    if offset > latest_offset:
                        latest_offset = offset
    return messages, latest_offset


def _extract_next_cont(json_data):
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "continuation":
                    return v
                res = walk(v)
                if res:
                    return res
        elif isinstance(obj, list):
            for i in obj:
                res = walk(i)
                if res:
                    return res
        return None
    return walk(json_data)


def download_chat(url, progress_path=None, out_path=None):
    """YouTubeチャットログをcsvとして保存する"""
    print(f"▶ Fetching chat: {url}")

    html = _fetch_html(url)

    # duration取得（ytInitialPlayerResponseから）
    duration = 0
    dur_m = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', html)
    if dur_m:
        duration = int(dur_m.group(1))
    print(f"📏 動画の長さ: {duration} 秒")

    api_key, version, yid = _extract_params(html)
    if not yid:
        print("❌ ytInitialData が見つかりません。")
        return
    continuation = _find_continuation(yid)
    if not continuation:
        print("❌ continuation が見つかりません。")
        return

    out = out_path if out_path else "chatlog.csv"
    open(out, "w", encoding="utf-8").close()
    total = 0
    max_seen_offset = 0
    seen_continuations = set()

    with open(out, "a", encoding="utf-8") as f:
        f.write("time,user,comment\n")

    start_time = time.time()
    for i in range(3000):
        if continuation in seen_continuations:
            print("🔁 同じ continuation が繰り返されたため終了します。")
            break
        seen_continuations.add(continuation)

        data = _fetch_chat(api_key, version, continuation)
        actions = data.get("actions") or data.get("continuationContents", {}).get(
            "liveChatContinuation", {}
        ).get("actions")
        msgs, latest_offset = _parse_messages(actions)

        if latest_offset > max_seen_offset:
            max_seen_offset = latest_offset
        if max_seen_offset / 1000 >= duration:
            print(f"🏁 動画時間（{duration}s）に到達したため終了します。")
            break

        with open(out, "a", encoding="utf-8") as f:
            for t, author, msg, offset in msgs:
                total += 1
                print(f"{t},{author},{msg}", flush=True)
                f.write(f"{t},{author},{msg}\n")
            f.flush()
            os.fsync(f.fileno())

        next_c = _extract_next_cont(data)
        if not next_c:
            print("🟢 continuation が無くなったため終了します。")
            break
        continuation = next_c

        if i % 20 == 0:
            elapsed = int(time.time() - start_time)
            print(f"⏳ {elapsed}s経過 / {total}件取得 / 現在 {max_seen_offset//1000}s")

        # 進捗をファイルに書き込む
        if progress_path and duration > 0:
            offset_pct = min((max_seen_offset / 1000) / duration, 1.0)
            # 経過時間ベースの進捗（動画1秒≒0.08s処理と仮定、上限は offset_pct を超えない）
            elapsed = time.time() - start_time
            time_pct = min(elapsed / max(duration * 0.08, 1), 1.0)
            # offset_pctが動いていればそちら優先、止まっているときは time_pct で補完
            local_pct = max(offset_pct, min(time_pct, offset_pct + 0.1))
            chat_progress = int(45 + local_pct * 40)  # 45〜85%
            safe_write_json(progress_path, {
                "progress": chat_progress,
                "message": f"チャットダウンロード {int(offset_pct * 100)}% ({total}件取得)",
                "phase": "チャットダウンロード"
            })

        time.sleep(CHAT_BATCH_SLEEP_SEC)

    print(f"✅ 完了: {total} 件のコメントを {out} に保存しました。")

    # 重複削除
    try:
        with open(out, "r", encoding="utf-8") as f:
            lines = f.readlines()
        seen = set()
        unique_lines = [l for l in lines if l not in seen and not seen.add(l)]
        with open(out, "w", encoding="utf-8") as f:
            f.writelines(unique_lines)
        removed = len(lines) - len(unique_lines)
        if removed > 0:
            print(f"🧽 重複 {removed} 行を削除しました。")
    except Exception as e:
        print(f"⚠️ 重複削除中にエラー: {e}")

    # 時間順ソート
    try:
        with open(out, "r", encoding="utf-8") as f:
            reader = csv_module.reader(f)
            header = next(reader)
            rows = [r for r in reader if len(r) >= 3]

        def parse_time(t):
            try:
                parts = list(map(int, t.split(":")))
                if len(parts) == 3:
                    return parts[0] * 3600 + parts[1] * 60 + parts[2]
                elif len(parts) == 2:
                    return parts[0] * 60 + parts[1]
                else:
                    return int(parts[0])
            except:
                return 0

        rows.sort(key=lambda x: parse_time(x[0]))
        with open(out, "w", encoding="utf-8", newline="") as f:
            writer = csv_module.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        print(f"🔁 並び替え完了: {len(rows)} 件を時間順に整列しました。")
    except Exception as e:
        print(f"⚠️ 並び替え中にエラー: {e}")

# === youtubeChatdl.py インライン終わり ===
from pytubefix import YouTube

# ffmpeg / ffprobe のパス（system_utils で一本化）
ffmpeg_path = get_ffmpeg_path()
_BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ffprobe_path = get_ffprobe_path()
audiowaveform_path = os.path.join(_BASE_DIR, "bin", "audiowaveform.exe")

# 標準出力をUTF-8に
sys.stdout.reconfigure(encoding="utf-8")


def safe_write_json(path, data):
    dir_name = os.path.dirname(path)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=dir_name, delete=False
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False)
        tmp.flush()
        os.fsync(tmp.fileno())
    shutil.move(tmp.name, path)




def sanitize_filename(title):
    """Windows用にファイル名を安全化"""
    title = unicodedata.normalize("NFKC", title)
    title = "".join(
        c for c in title if not unicodedata.category(c).startswith(("So", "Sk"))
    )
    title = re.sub(r'[\\/*?:"<>|#:/]', "_", title)
    title = re.sub(r"[（）【】［］『』「」]", "", title)
    title = re.sub(r"[\r\n\t]", "", title)
    title = re.sub(r"[，、。！!？?]", "", title)
    title = re.sub(r"\s+", "_", title)
    title = re.sub(r"_+", "_", title)
    title = title.strip("_")
    return title if title else "video"


def make_progress_callback(progress_path, phase_label, base_pct, range_pct):
    """
    phase_label: 表示名（例: "動画ダウンロード"）
    base_pct: この段階の開始%
    range_pct: この段階の幅%
    """
    def callback(stream, chunk, bytes_remaining):
        total_size = stream.filesize
        if total_size <= 0:
            return
        bytes_downloaded = total_size - bytes_remaining
        local_pct = bytes_downloaded / total_size  # 0.0〜1.0
        overall = int(base_pct + local_pct * range_pct)
        print(f"[PROGRESS] {phase_label} {int(local_pct * 100)}%", flush=True)
        safe_write_json(progress_path, {
            "progress": overall,
            "message": f"{phase_label} {int(local_pct * 100)}%",
            "phase": phase_label,
        })
    return callback


def download_with_pytubefix(url, output_folder, max_resolution=720, progress_path=None):
    """
    pytubefixを使用して動画をダウンロード
    """
    print(f"[INFO] pytubefix でダウンロード開始...", flush=True)

    # 動画DL用コールバック（0〜30%）
    video_cb = make_progress_callback(progress_path, "動画ダウンロード", 0, 30) if progress_path else None
    yt = YouTube(url, on_progress_callback=video_cb)

    title = sanitize_filename(yt.title)[:30]
    print(f"[INFO] タイトル: {yt.title}", flush=True)
    print(f"[INFO] 長さ: {yt.length}秒", flush=True)

    # max_resolution以下のストリームを探す
    video_stream = None

    # 全ての動画ストリームを取得
    all_video_streams = list(yt.streams.filter(type="video"))

    # max_resolution以下を手動でフィルタリング
    suitable_streams = []
    for s in all_video_streams:
        if s.resolution:
            try:
                res = int(s.resolution.replace("p", ""))
                if res <= max_resolution:
                    suitable_streams.append(s)
            except ValueError:
                continue

    # 解像度でソート（高い順）
    suitable_streams.sort(
        key=lambda s: int(s.resolution.replace("p", "")), reverse=True
    )

    if suitable_streams:
        video_stream = suitable_streams[0]
        print(f"[INFO] {max_resolution}p以下が見つかりました: {video_stream.resolution}", flush=True)
    else:
        # 720p以下がない場合、利用可能な解像度を表示してユーザーに確認
        available_resolutions = []
        for s in all_video_streams:
            if s.resolution and s.resolution not in available_resolutions:
                available_resolutions.append(s.resolution)

        # 解像度でソート
        available_resolutions.sort(key=lambda r: int(r.replace("p", "")), reverse=True)

        print(f"[WARN] {max_resolution}p以下が見つかりません。", flush=True)
        print(
            f"[INFO] 利用可能な解像度: {', '.join(available_resolutions[:5])}",
            flush=True,
        )

        # 最高画質を取得
        all_video_streams.sort(
            key=lambda s: int(s.resolution.replace("p", "")) if s.resolution else 0,
            reverse=True,
        )
        best_stream = all_video_streams[0] if all_video_streams else None

        if best_stream:
            print(
                f"[QUESTION] {best_stream.resolution} でダウンロードしますか？ (Y/N): ",
                end="",
                flush=True,
            )
            response = input().strip().upper()
            if response != "Y":
                raise Exception("ユーザーによりキャンセルされました")
            video_stream = best_stream
        else:
            raise Exception("利用可能な動画ストリームがありません")

    # 最高音質の音声を取得（MP4優先、なければwebm）
    audio_stream = (
        yt.streams.filter(only_audio=True, mime_type="audio/mp4")
        .order_by("abr")
        .desc()
        .first()
    )
    if not audio_stream:
        audio_stream = yt.streams.filter(only_audio=True).order_by("abr").desc().first()

    if not video_stream or not audio_stream:
        raise Exception("ストリームが見つかりませんでした")

    print(
        f"[INFO] 映像: {video_stream.resolution} ({video_stream.mime_type})", flush=True
    )
    print(f"[INFO] 音声: {audio_stream.abr}", flush=True)

    # 出力フォルダ作成（既存の場合は削除して再作成）
    title_folder = os.path.join(output_folder, title)
    if os.path.exists(title_folder):
        shutil.rmtree(title_folder)
        print(f"[INFO] 既存フォルダを削除して再ダウンロード: {title_folder}", flush=True)
    os.makedirs(title_folder, exist_ok=True)

    video_file = os.path.join(title_folder, "video_temp.mp4")
    audio_file = os.path.join(title_folder, "audio_temp.mp4")
    output_file = os.path.join(title_folder, f"{title}.mp4")

    # 動画ダウンロード
    print("[INFO] 動画ダウンロード中...", flush=True)
    if progress_path:
        safe_write_json(progress_path, {"progress": 0, "message": "動画ダウンロード開始", "phase": "動画ダウンロード"})
    video_stream.download(output_path=title_folder, filename="video_temp.mp4")

    # 音声DL用にコールバック差し替え（30〜45%）
    if progress_path:
        audio_cb = make_progress_callback(progress_path, "音声ダウンロード", 30, 15)
        # pytubefixのコールバックリストをクリアして音声用に差し替え
        for attr in ("_progress_hooks", "progress_hooks", "_on_progress_callbacks"):
            if hasattr(yt, attr):
                try:
                    getattr(yt, attr).clear()
                except Exception:
                    pass
        yt.register_on_progress_callback(audio_cb)
    print("[INFO] 音声ダウンロード中...", flush=True)
    audio_stream.download(output_path=title_folder, filename="audio_temp.mp4")

    # ffmpegで結合
    print(
        f"[INFO] 結合中... ({video_file} + {audio_file} -> {output_file})", flush=True
    )
    cmd = [
        ffmpeg_path,
        "-i",
        video_file,
        "-i",
        audio_file,
        "-c",
        "copy",
        "-y",
        output_file,
    ]
    result = subprocess.run(cmd, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)

    if result.returncode == 0:
        # 一時ファイル削除
        if os.path.exists(video_file):
            os.remove(video_file)
        if os.path.exists(audio_file):
            os.remove(audio_file)
        print(f"[INFO] pytubefix ダウンロード完了: {output_file}", flush=True)
        return title_folder, title
    else:
        raise Exception(f"ffmpeg結合エラー: {result.stderr.decode('utf-8', errors='replace')}")


def download_video_and_chat(url, base_output_folder, progress_path):
    output_folder = os.path.abspath(base_output_folder)
    os.makedirs(output_folder, exist_ok=True)

    safe_write_json(progress_path, {"progress": 0, "message": "動画ダウンロード開始", "phase": "動画ダウンロード"})

    # pytubefixでダウンロード（動画0〜30%、音声30〜45%）
    title_folder, safe_title = download_with_pytubefix(
        url, output_folder, max_resolution=1080, progress_path=progress_path
    )

    safe_write_json(
        progress_path, {"progress": 45, "message": "チャットダウンロード中", "phase": "チャットダウンロード"}
    )

    # チャットをtitle_folderに直接保存
    try:
        dst_csv = os.path.join(title_folder, "comments_cleaned.csv")
        download_chat(url, progress_path=progress_path, out_path=dst_csv)
        if os.path.exists(dst_csv):
            print(f"[INFO] チャットログを保存: {dst_csv}", flush=True)
        else:
            print("[WARN] chatlog.csv が見つかりません。", flush=True)
    except Exception as e:
        print(f"[ERROR] youtubeChatdl失敗: {e}", flush=True)

    # === 波形生成 ===
    try:
        safe_write_json(progress_path, {"progress": 85, "message": "波形生成中", "phase": "波形生成"})

        mp4_path = os.path.join(title_folder, f"{safe_title}.mp4")
        wav_path = os.path.join(title_folder, "waveform.wav")
        json_path = os.path.join(title_folder, "waveform.json")

        cmd_wav = [
            ffmpeg_path,
            "-y",
            "-i",
            mp4_path,
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            wav_path,
        ]
        subprocess.run(cmd_wav, check=True, creationflags=subprocess.CREATE_NO_WINDOW)

        cmd_probe = [
            ffprobe_path,
            "-i",
            mp4_path,
            "-show_entries",
            "format=duration",
            "-v",
            "quiet",
            "-of",
            "csv=p=0",
        ]
        duration_output = subprocess.check_output(
            cmd_probe, creationflags=subprocess.CREATE_NO_WINDOW
        ).decode("utf-8", errors="replace").strip()
        duration_sec = int(float(duration_output))
        print(f"[INFO] 動画長さ: {duration_sec} 秒", flush=True)

        def choose_pixels_per_second(duration_sec: int) -> int:
            if duration_sec <= 600:
                return 500
            elif duration_sec <= 3600:
                return 800
            elif duration_sec <= 3 * 3600:
                return 1000
            elif duration_sec <= 6 * 3600:
                return 1500
            else:
                return 2000

        pps = choose_pixels_per_second(duration_sec)
        print(f"[INFO] pixels-per-second = {pps}", flush=True)

        cmd_json = [
            audiowaveform_path,
            "-i",
            wav_path,
            "-o",
            json_path,
            "--pixels-per-second",
            str(pps),
            "--bits",
            "8",
        ]
        result = subprocess.run(cmd_json, check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.stderr:
            print("[WARN] audiowaveform stderr:", result.stderr.decode("utf-8", errors="replace"))

        if os.path.exists(wav_path):
            os.remove(wav_path)
            print(f"[INFO] 一時wav削除: {wav_path}", flush=True)

        print(f"[INFO] 波形データ生成完了: {json_path}", flush=True)

    except Exception as e:
        print(f"[ERROR] 波形生成失敗: {e}", flush=True)

    safe_write_json(
        progress_path, {"progress": 100, "message": f"{safe_title} のダウンロード完了"}
    )
    print(f"[INFO] {safe_title} のすべての処理が完了しました", flush=True)


def main():
    if len(sys.argv) != 4:
        print(
            "Usage: python download_video.py <YouTube_URL> <output_folder> <progress_path>"
        )
        sys.exit(1)

    video_url = sys.argv[1]
    base_output_folder = sys.argv[2]
    progress_path = sys.argv[3]

    os.makedirs(base_output_folder, exist_ok=True)

    safe_write_json(progress_path, {"progress": 0, "message": "開始"})

    try:
        download_video_and_chat(
            video_url, base_output_folder, progress_path
        )
    except Exception as e:
        safe_write_json(progress_path, {"progress": -1, "message": f"エラー: {e}"})
        print("エラー:", e, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
