#!/usr/bin/env python3
import os
import sys

# Windows コンソールを UTF-8 モードに切り替え
sys.stdout.reconfigure(encoding="utf-8")

import csv
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import argparse
import json
import tempfile
import traceback
import random
import gc
import re
import time
import shutil
import subprocess

# Flask はサーバー側で利用
from flask import Flask, request, jsonify, url_for

print(f"🎯 実行中ファイル: {__file__}", flush=True)


# --- 定数（マジックナンバー撤廃） ---
DEFAULT_FONTSIZE = 50                   # コメント画像のデフォルトフォントサイズ
COMMENT_DISPLAY_DURATION_SEC = 7.0      # 各コメントを画面に表示し続ける秒数
VIDEO_EDGE_PADDING_PX = 50              # コメント配置時に画面端から確保する余白
PROGRESS_LOG_EVERY = 50                 # 何件ごとに進捗を更新/ログするか
CLIP_END_CUT_MARGIN_SEC = 2             # クリップ末尾から何秒分のコメントを捨てるか（リピート防止）
CLIP_RETRY_COUNT = 3                    # クリップ単位の失敗再試行回数
CLIP_RETRY_DELAY_SEC = 3                # 再試行前のウェイト秒数


# === 進捗ファイル書き込み用 ===
def safe_write_progress(progress_path, progress, message, current_clip=0):
    """進捗ファイルを安全に書き込む"""
    if not progress_path:
        return

    try:
        dir_name = os.path.dirname(progress_path)
        tmp_path = progress_path + ".tmp"

        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "progress": progress,
                    "message": message,
                    "current_clip": current_clip,
                },
                f,
                ensure_ascii=False,
            )
            f.flush()
            os.fsync(f.fileno())

        # アトミックに置き換え
        for attempt in range(3):
            try:
                shutil.move(tmp_path, progress_path)
                return
            except PermissionError:
                if attempt < 2:
                    time.sleep(0.1)
                else:
                    raise
    except Exception as e:
        print(f"⚠️ 進捗ファイル書き込みエラー: {e}", flush=True)


def find_font(filename):
    """システムフォントフォルダとユーザーフォントフォルダを順に検索"""
    search_dirs = [
        os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
    ]
    for d in search_dirs:
        path = os.path.join(d, filename)
        if os.path.exists(path):
            return path
    return None


def can_render_text(text, font_path, fontsize=DEFAULT_FONTSIZE):
    """フォントがテキストを正しく描画できるか確認する。
    文字化け（豆腐文字 □ や .notdef）が多い場合はFalseを返す"""
    if not font_path:
        return True
    try:
        from fontTools.ttLib import TTFont
        font = TTFont(font_path, fontNumber=0)
        cmap = font.getBestCmap()
        if not cmap:
            return True
        missing = 0
        total = 0
        for ch in text:
            cp = ord(ch)
            if cp <= 0x20:  # 制御文字・スペースはスキップ
                continue
            total += 1
            if cp not in cmap:
                missing += 1
        if total == 0:
            return True
        # 半分以上描画できない場合はスキップ
        return (missing / total) < 0.5
    except Exception:
        return True


def create_text_image(text, font_path=None, fontsize=DEFAULT_FONTSIZE):
    if font_path is None:
        found = find_font("keifont.ttf")
        if found:
            try:
                font = ImageFont.truetype(found, fontsize)
            except Exception:
                font = ImageFont.load_default()
        else:
            try:
                font = ImageFont.truetype("meiryo.ttc", fontsize, index=1)
            except Exception:
                font = ImageFont.load_default()
    else:
        try:
            font = ImageFont.truetype(font_path, fontsize)
        except Exception:
            font = ImageFont.load_default()

    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)

    sw = max(1, min(2, int(fontsize * 0.04)))

    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=sw)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    pad_x, pad_y = 60, 40
    img = Image.new("RGBA", (text_w + pad_x, text_h + pad_y), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x, y = pad_x // 2, pad_y // 2

    draw.text(
        (x, y),
        text,
        font=font,
        fill="white",
        stroke_width=sw,
        stroke_fill="black",
    )

    return np.array(img), text_w + pad_x, text_h + pad_y


def time_str_to_seconds(t):
    try:
        if t.startswith("-"):
            t = t[1:]
        parts = t.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        else:
            return float(t)
    except:
        return None


def read_comments(csv_path, base=0, clip_ranges=None):
    comments = []
    current_range_index = 0

    if clip_ranges:
        clip_ranges = sorted(clip_ranges, key=lambda x: x[0])
        current_start, current_end = clip_ranges[current_range_index]
    else:
        current_start, current_end = None, None

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = time_str_to_seconds(row.get("time", ""))
            txt = row.get("comment", "").strip()
            print(f"[DEBUG] ts={ts}, txt={txt}")
            if ts is None or not txt:
                continue

            if not clip_ranges:
                if ts >= base:
                    comments.append({"time": ts, "text": txt})
                continue

            if current_range_index >= len(clip_ranges):
                break

            if ts < current_start:
                continue

            if current_start <= ts <= current_end:
                comments.append({"time": ts, "text": txt})
                continue

            while ts > current_end:
                current_range_index += 1
                if current_range_index >= len(clip_ranges):
                    break
                current_start, current_end = clip_ranges[current_range_index]

            if current_range_index >= len(clip_ranges):
                break

            if current_start <= ts <= current_end:
                comments.append({"time": ts, "text": txt})

    return comments


class CommentTrack:
    def __init__(self, w, h):
        self.video_w = w
        self.video_h = h
        self.y_line_end_times = {}

    def find_y(self, new_start, dur, h, w, margin=40):
        min_y = VIDEO_EDGE_PADDING_PX
        max_y = self.video_h - h - VIDEO_EDGE_PADDING_PX

        candidates = list(range(min_y, max_y + 1, 70))
        np.random.shuffle(candidates)

        for y in candidates:
            y_end_time = self.y_line_end_times.get(y, -999)
            if new_start >= y_end_time + 0.1:
                return y

        return None


# === ffmpegパス（system_utils で一本化） ===
from system_utils import get_ffmpeg_path, get_ffprobe_path
_ffmpeg_path = get_ffmpeg_path()


def _detect_encoder():
    """使用可能なハードウェアエンコーダーを検出して返す"""
    candidates = [
        ("h264_nvenc",  ["-f", "lavfi", "-i", "nullsrc", "-t", "0.1", "-c:v", "h264_nvenc", "-f", "null", "-"]),
        ("h264_amf",    ["-f", "lavfi", "-i", "nullsrc", "-t", "0.1", "-c:v", "h264_amf",   "-f", "null", "-"]),
        ("h264_qsv",    ["-f", "lavfi", "-i", "nullsrc", "-t", "0.1", "-c:v", "h264_qsv",   "-f", "null", "-"]),
    ]
    for name, args in candidates:
        try:
            ret = subprocess.run(
                [_ffmpeg_path] + args,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if ret.returncode == 0:
                print(f"✅ エンコーダー: {name}", flush=True)
                return name
        except Exception:
            pass
    print("⚠️ ハードウェアエンコーダーなし → libx264 (CPU) を使用", flush=True)
    return "libx264"

_VIDEO_ENCODER = _detect_encoder()


def get_video_info(video_path):
    """ffprobeで動画のfps/width/heightを取得"""
    ffprobe_path = get_ffprobe_path()
    cmd = [
        ffprobe_path, "-v", "quiet", "-print_format", "json",
        "-show_streams", video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    info = json.loads(result.stdout)
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            w = stream["width"]
            h = stream["height"]
            fps_str = stream.get("r_frame_rate", "30/1")
            num, den = fps_str.split("/")
            fps = float(num) / float(den)
            return w, h, fps
    return 1920, 1080, 30.0


def build_ffmpeg_overlay_filter(overlay_items, video_duration):
    """
    overlay_items: list of {img_path, x_expr, y, start_sec, end_sec}
    ffmpegのfilter_complexを構築して返す
    """
    # ベース: [0:v] → 各overlayを順番に重ねていく
    filter_parts = []
    inputs = []
    n = len(overlay_items)

    if n == 0:
        return None, []

    prev = "[0:v]"
    for i, item in enumerate(overlay_items):
        inputs.append(item["img_path"])
        label_in = f"[ov{i}]"
        label_out = f"[v{i}]" if i < n - 1 else "[vout]"

        # スクロール: x = W-(W+tw)*(t-start)/dur  (右→左)
        start = item["start_sec"]
        end = item["end_sec"]
        dur = end - start
        tw = item["tw"]
        y = item["y"]

        # ffmpegのoverlay x式: W-based scrolling
        x_expr = f"W-((W+{tw})*(t-{start:.3f})/{dur:.3f})"
        # enable: startからendまでの間だけ表示
        enable_expr = f"between(t,{start:.3f},{end:.3f})"

        filter_parts.append(
            f"{prev}[{i+1}:v]overlay=x='{x_expr}':y={y}:enable='{enable_expr}'{label_out}"
        )
        prev = label_out

    return ";".join(filter_parts), inputs


def run_ffmpeg_with_progress(cmd, progress_path, clip_title, clip_idx, total_frames):
    """ffmpegをsubprocessで実行し、stderrから進捗を読んでファイルに書く"""
    last_written = -1
    process = subprocess.Popen(
        cmd,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        encoding="utf-8",
        errors="backslashreplace",
    )
    frame_re = re.compile(r"frame=\s*(\d+)")
    for line in process.stderr:
        print(line, end="", flush=True)
        m = frame_re.search(line)
        if m and total_frames > 0:
            frame = int(m.group(1))
            percent = min(int(frame / total_frames * 100), 100)
            if percent != last_written:
                last_written = percent
                safe_write_progress(
                    progress_path,
                    percent,
                    f"{clip_title}: 書き出し中...",
                    clip_idx,
                )
    process.wait()
    return process.returncode


def gen_clip(
    clip_info,
    video_path,
    comments,
    out_path,
    progress_path=None,
    clip_idx=1,
    clip_title="",
    font_path=None,
):
    """
    PILで各コメントをPNG画像に書き出し、ffmpegのoverlayフィルタで合成する高速実装。
    moviepyは使用しない。
    """
    start, end = clip_info["start"], clip_info["end"]
    clip_duration = end - start

    print(f"\n🎬 クリップ生成開始: {start}s～{end}s", flush=True)
    safe_write_progress(progress_path, 0, f"{clip_title}: 準備中", clip_idx)

    # 動画情報取得
    w, h, fps = get_video_info(video_path)
    total_frames = int(clip_duration * fps)
    print(f"📐 動画サイズ: {w}x{h}, fps={fps:.2f}, 総フレーム数={total_frames}", flush=True)

    # コメントフィルタ・上限250件
    queue = [c for c in comments if start <= c["time"] <= end]
    queue.sort(key=lambda c: c["time"])

    total_count = len(queue)
    print(f"▶ コメント数: {total_count}件", flush=True)

    print(f"▶ コメント処理開始 ({len(queue)} 件)", flush=True)
    safe_write_progress(progress_path, 5, f"{clip_title}: コメント画像生成中", clip_idx)

    # 一時ディレクトリにPNG画像を書き出す
    tmp_dir = tempfile.mkdtemp(prefix="mp4chat_")
    overlay_items = []
    track_y = CommentTrack(w, h)

    try:
        for ci, c in enumerate(queue):
            rel = c["time"] - start
            dur = COMMENT_DISPLAY_DURATION_SEC

            # フォントで描画できない文字が多い場合はスキップ
            if font_path and not can_render_text(c["text"], font_path):
                print(f"⚠️ スキップ（描画不可）: {c['text'][:20]}", flush=True)
                continue

            img_arr, tw, th = create_text_image(c["text"], font_path=font_path)

            min_y = VIDEO_EDGE_PADDING_PX
            max_y = h - th - VIDEO_EDGE_PADDING_PX
            candidates = list(range(min_y, max_y + 1, 70))
            np.random.shuffle(candidates)

            y = None
            for cand_y in candidates:
                y_end_time = track_y.y_line_end_times.get(cand_y, -999)
                if c["time"] >= y_end_time + 0.1:
                    y = cand_y
                    break

            if y is None:
                continue

            track_y.y_line_end_times[y] = c["time"] + dur

            # PNG保存
            img_path = os.path.join(tmp_dir, f"c{ci:05d}.png")
            img = Image.fromarray(img_arr, "RGBA")
            img.save(img_path, "PNG")

            overlay_items.append({
                "img_path": img_path,
                "tw": tw,
                "y": y,
                "start_sec": rel,
                "end_sec": rel + dur,
            })

            if (ci + 1) % PROGRESS_LOG_EVERY == 0 or (ci + 1) == len(queue):
                prog = 5 + int((ci + 1) / max(len(queue), 1) * 15)
                safe_write_progress(
                    progress_path, prog,
                    f"{clip_title}: コメント {ci+1}/{len(queue)}",
                    clip_idx,
                )

        print(f"▶ オーバーレイ画像生成完了: {len(overlay_items)} 件", flush=True)
        safe_write_progress(progress_path, 20, f"{clip_title}: 動画書き出し開始", clip_idx)

        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        # ffmpegコマンド構築
        cmd = [_ffmpeg_path, "-y"]

        # 入力1: 元動画(trimして切り出し)
        cmd += ["-ss", str(start), "-t", str(clip_duration), "-i", video_path]

        # 入力2以降: 各コメント画像
        for item in overlay_items:
            cmd += ["-i", item["img_path"]]

        if overlay_items:
            # filter_complexをファイルに書き出してコマンドライン長制限を回避
            filter_parts = []
            prev = "[0:v]"
            n = len(overlay_items)
            for i, item in enumerate(overlay_items):
                label_out = f"[v{i}]" if i < n - 1 else "[vout]"
                s = item["start_sec"]
                e = item["end_sec"]
                d = e - s
                tw = item["tw"]
                y = item["y"]
                x_expr = f"W-((W+{tw})*(t-{s:.3f})/{d:.3f})"
                enable_expr = f"between(t,{s:.3f},{e:.3f})"
                filter_parts.append(
                    f"{prev}[{i+1}:v]overlay=x='{x_expr}':y={y}:enable='{enable_expr}'{label_out}"
                )
                prev = label_out

            filter_complex = ";\n".join(filter_parts)
            filter_script_path = os.path.join(tmp_dir, "filter.txt")
            with open(filter_script_path, "w", encoding="utf-8") as ff:
                ff.write(filter_complex)
            print(f"📝 filter_complex: {len(filter_complex)} 文字 → ファイルで渡します", flush=True)
            cmd += ["-filter_complex_script", filter_script_path, "-map", "[vout]", "-map", "0:a"]
        else:
            # コメントなし: そのままコピー
            cmd += ["-map", "0:v", "-map", "0:a"]

        if _VIDEO_ENCODER in ("h264_nvenc", "h264_amf"):
            enc_opts = ["-c:v", _VIDEO_ENCODER, "-preset", "p4", "-cq", "18"]
        elif _VIDEO_ENCODER == "h264_qsv":
            enc_opts = ["-c:v", _VIDEO_ENCODER, "-preset", "medium", "-global_quality", "18"]
        else:
            enc_opts = ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
        cmd += enc_opts + [
            "-c:a", "aac",
            out_path,
        ]

        print(f"💾 ffmpeg書き出し開始...", flush=True)
        ret = run_ffmpeg_with_progress(cmd, progress_path, clip_title, clip_idx, total_frames)

        if ret != 0:
            raise RuntimeError(f"ffmpeg が終了コード {ret} で失敗しました")

        print(f"✅ 書き出し完了: {out_path}", flush=True)
        safe_write_progress(progress_path, 100, f"{clip_title}: 完了", clip_idx)

    except Exception as e:
        print(f"❌ 書き出しエラー: {e}", flush=True)
        safe_write_progress(progress_path, -1, f"{clip_title}: エラー - {e}", clip_idx)
        traceback.print_exc()

    finally:
        # 一時PNG削除
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass
        gc.collect()
        print("✅ リソース解放完了", flush=True)


_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def sanitize_filename(s, max_len=100):
    """
    Windows ファイル名として安全な文字列に正規化する。
    - 英数字 + アンダースコア + 日本語文字以外を _ に置換
    - 末尾の `.` と空白を除去（Windows のファイル名規則）
    - Windows 予約語（CON, PRN ほか）を回避
    - 長さを max_len で打ち切り
    - 空文字列なら "untitled" にフォールバック
    """
    cleaned = re.sub(r"[^\w一-龯ぁ-んァ-ンー]", "_", s)
    cleaned = cleaned.strip(". ")
    if cleaned.upper() in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned or "untitled"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--clips", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--progress", default=None)  # 進捗ファイルパス追加
    parser.add_argument("--clip-idx", type=int, default=1)  # クリップ番号
    parser.add_argument("--clip-title", default="")  # クリップタイトル
    parser.add_argument("--font", default="")          # フォントパス
    parser.add_argument("--is-last", default="False")
    args = parser.parse_args()

    progress_path = args.progress
    clip_idx = args.clip_idx
    clip_title_arg = args.clip_title

    # clips.json を読み込む
    clips = json.load(open(args.clips, encoding="utf-8"))
    clip_ranges = [(c["start"], c["end"]) for c in clips]

    print("▶ 解析対象の範囲:")
    for idx, (s, e) in enumerate(clip_ranges, 1):
        print(f"  {idx}: {s}秒 ～ {e}秒")

    comments = read_comments(args.csv, clip_ranges=clip_ranges)
    print(f"▶ CSVから読み込んだコメント数 = {len(comments)} 件", flush=True)

    # 定数の別名（関数ローカルで読みやすくするため）
    CUT_MARGIN = CLIP_END_CUT_MARGIN_SEC
    MAX_RETRY = CLIP_RETRY_COUNT
    RETRY_DELAY = CLIP_RETRY_DELAY_SEC

    video_path = args.video
    all_success = True

    for i, ci in enumerate(clips, 1):
        title = ci.get("title", "").strip()
        if title:
            title_safe = sanitize_filename(title)
            filename = f"{title_safe}.mp4"
        else:
            filename = f"clip_{i}.mp4"

        # クリップタイトル（進捗表示用）
        display_title = (
            clip_title_arg
            if clip_title_arg
            else (title if title else f"クリップ{clip_idx}")
        )

        base_name, ext = os.path.splitext(filename)
        candidate = filename
        counter = 1
        while os.path.exists(os.path.join(args.outdir, candidate)):
            candidate = f"{base_name}({counter}){ext}"
            counter += 1

        out_file = os.path.join(args.outdir, candidate)

        print(f"▶ Clip {i}/{len(clips)} 開始: {ci} → {out_file}", flush=True)
        safe_write_progress(progress_path, 0, f"{display_title}: 開始", clip_idx)

        start, end = ci.get("start", 0), ci.get("end", 0)
        comments_end = max(start, end - CUT_MARGIN)
        comments_for_clip = [c for c in comments if start <= c["time"] < comments_end]
        print(
            f"  └ コメント数（末尾{CUT_MARGIN}秒カット後）: {len(comments_for_clip)} 件",
            flush=True,
        )

        success = False
        for attempt in range(1, MAX_RETRY + 1):
            try:
                gen_clip(
                    ci,
                    args.video,
                    comments_for_clip,
                    out_file,
                    progress_path=progress_path,
                    clip_idx=clip_idx,
                    clip_title=display_title,
                    font_path=args.font if args.font else None,
                )
                print(f"✅ Clip {i} 成功 (試行 {attempt})", flush=True)
                success = True
                break
            except Exception as e:
                print(f"⚠️ Clip {i} 失敗 (試行 {attempt}/{MAX_RETRY}): {e}", flush=True)
                traceback.print_exc()
                safe_write_progress(
                    progress_path,
                    -1,
                    f"{display_title}: エラー (試行 {attempt})",
                    clip_idx,
                )
                if attempt < MAX_RETRY:
                    print(f"⏳ {RETRY_DELAY}秒後にリトライします...", flush=True)
                    time.sleep(RETRY_DELAY)

        if not success:
            print(f"❌ Clip {i} は {MAX_RETRY} 回失敗 → スキップ", flush=True)
            safe_write_progress(progress_path, -1, f"{display_title}: 失敗", clip_idx)
            all_success = False
            continue

        # 完了を確実に書き込む
        safe_write_progress(progress_path, 100, f"{display_title}: 完了", clip_idx)
        print(f"📝 進捗ファイル更新: 100% - {display_title}: 完了", flush=True)

        print(f"🧹 Clip {i} 処理完了後のメモリクリーンアップ...", flush=True)
        gc.collect()
        print(f"✅ メモリクリーンアップ完了", flush=True)

    print("✅ 全クリップ処理完了", flush=True)


if __name__ == "__main__":
    main()
