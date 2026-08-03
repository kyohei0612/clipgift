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
import gc
import re
import time
import shutil
import subprocess

# 注意: このスクリプトは app.py から「クリップ 1 本ごとに」subprocess で起動される。
# import は起動のたびに毎回コストを払うので、未使用のものを置かないこと。
# （以前 `from flask import ...` と `import random` が未使用のまま残っており、
#   Flask の import だけでクリップ 1 本あたり約 310ms を捨てていた。
#   乱数は np.random を使っているので標準 random も不要。）

print(f"🎯 実行中ファイル: {__file__}", flush=True)


# --- 定数（マジックナンバー撤廃） ---
DEFAULT_FONTSIZE = 50                   # コメント画像のデフォルトフォントサイズ
COMMENT_DISPLAY_DURATION_SEC = 7.0      # 各コメントを画面に表示し続ける秒数
VIDEO_EDGE_PADDING_PX = 50              # コメント配置時に画面端から確保する余白
PROGRESS_LOG_EVERY = 50                 # 何件ごとに進捗を更新/ログするか
CLIP_END_CUT_MARGIN_SEC = 2             # クリップ末尾から何秒分のコメントを捨てるか（リピート防止）
CLIP_RETRY_COUNT = 3                    # クリップ単位の失敗再試行回数
CLIP_RETRY_DELAY_SEC = 3                # 再試行前のウェイト秒数
LANE_SAFETY_GAP_SEC = 0.15              # 同一レーンで前のコメントとの間に空ける余裕
LANE_VERTICAL_GAP_PX = 10               # 上下のレーン間に空ける余白
# レーン間隔の測定に使う見本文字列。
# 上に伸びる字（ポ・ｗ）と下に伸びる字（ぐ・y・g）を必ず含めること。
# ここが実際のコメントより低いと、隣のレーンと文字が重なる。
_LANE_PROBE_TEXT = "あぐygポ草ｗ｜"


# === 進捗ファイル書き込み用 ===
def safe_write_progress(progress_path, progress, message, current_clip=0):
    """進捗ファイルを安全に書き込む"""
    if not progress_path:
        return

    try:
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


def create_text_image(text, font_path=None, fontsize=DEFAULT_FONTSIZE, color="white"):
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
    # bbox の原点オフセットを引く。
    # textbbox((0,0), ...) の左上は (0,0) ではなくフォントのアセント等でズレる
    # （meiryo/fontsize=100 で bbox[1]=19、Noto Serif JP で 30 等）。
    # キャンバス高は text_h + pad_y しか無いので、単純に (pad/2, pad/2) へ描くと
    # bbox[1] > pad_y/2 のフォントで下端がはみ出して**文字が切れる**。
    # 実測: UI で選べる 30 フォント中 4 つ（Noto Serif JP / はちまるポップ /
    # モッチーポップ One / レゲエ One）が fontsize=100 で 5〜16px 切れていた。
    # 原点を bbox 分ずらせば全フォント・全サイズで収まる（検算済み）。
    x = pad_x // 2 - bbox[0]
    y = pad_y // 2 - bbox[1]

    draw.text(
        (x, y),
        text,
        font=font,
        fill=color,
        stroke_width=sw,
        stroke_fill="black",
    )

    return np.array(img), text_w + pad_x, text_h + pad_y


def lane_spacing_for(fontsize, font_path=None):
    """このフォント / サイズで、上下のレーンが重ならない最小間隔(px)を返す。

    ⚠️ 旧実装はレーン間隔を **70px 固定** にしていた。
    実際の文字の高さはフォントサイズに比例するので、UI が許す fontsize 60 以上では
    隣のレーンと文字が重なる（実測: fontsize 100 で 44px 重なり = 読めない）。
    ここで実際に 1 枚描画して、不透明ピクセルの縦幅から必要間隔を決める。
    """
    try:
        arr, _tw, _th = create_text_image(
            _LANE_PROBE_TEXT, font_path=font_path, fontsize=fontsize, color="#FFFFFF"
        )
        alpha = arr[:, :, 3]
        rows = np.where(alpha.any(axis=1))[0]
        if len(rows) == 0:
            raise ValueError("描画結果が空")
        glyph_h = int(rows.max()) - int(rows.min()) + 1
    except Exception as e:
        # 測定できなければフォントサイズから概算（安全側に広めを取る）
        print(f"⚠️ レーン間隔の測定に失敗、概算で続行: {e}", flush=True)
        glyph_h = int(fontsize * 1.25)
    return max(glyph_h + LANE_VERTICAL_GAP_PX, 20)


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
    """CSV からコメントを読み、clip_ranges のいずれかに入るものだけ返す。

    旧実装は「CSV が時刻の昇順に並んでいる」ことを前提に、レンジのカーソルを
    前方へ進めながら読む方式だった。downloader.py が書く CSV は整列済みなので
    通常は動くが、ユーザーが手で用意した CSV や複数配信を結合したログのように
    時刻が前後すると、カーソルが進んだ後の行が**無言で捨てられて**いた
    （その状態でも「Comments loaded: N 件」と出るので気付けない）。

    どのみち main() 側でクリップ範囲による再フィルタが走るため、
    ここは並び順に一切依存しない素直な判定にする。clip_ranges は実運用で
    1 件（app.py がクリップ 1 本ごとに起動する）なのでコストも問題にならない。
    """
    comments = []
    ranges = sorted(clip_ranges, key=lambda x: x[0]) if clip_ranges else None
    skipped_unparsable = 0

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = time_str_to_seconds(row.get("time", ""))
            txt = (row.get("comment") or "").strip()
            if ts is None:
                skipped_unparsable += 1
                continue
            if not txt:
                continue

            if ranges is None:
                if ts >= base:
                    comments.append({"time": ts, "text": txt})
                continue

            if any(s <= ts <= e for s, e in ranges):
                comments.append({"time": ts, "text": txt})

    if skipped_unparsable:
        # ヘッダー名の不一致（time/comment 列が無い）だと全行がここに落ちる。
        # 黙って 0 件になると原因が分からないので必ず出す。
        print(
            f"⚠️ 時刻を解釈できずスキップした行: {skipped_unparsable} 件"
            f"（CSV のヘッダーが time,user,comment 形式か確認してください）",
            flush=True,
        )
    print(f"[INFO] Comments loaded: {len(comments)} 件")
    return comments


class CommentTrack:
    """コメントを流す y 座標（レーン）ごとに「最後に流したコメント」を覚えておく入れ物。

    以前は同じ内容の find_y() を持っていたが、gen_clip 側に同じロジックが
    インラインで書かれていて呼び出されておらず、二重実装になっていたため削除した。
    """

    def __init__(self, w, h):
        self.video_w = w
        self.video_h = h
        # y -> (開始時刻, コメント画像の幅)
        self.lane_last = {}

    def next_free_time(self, y, tw_new, dur):
        """レーン y に幅 tw_new のコメントを流せるようになる時刻を返す。

        コメントは右端 x=W から左端 x=-tw まで dur 秒かけて流れる
        （overlay の x 式: W-((W+tw)*(t-start)/dur)）。
        つまり速度は (W+tw)/dur で、**幅が広いコメントほど速い**。

        前のコメントに追突しない条件を解くと、必要な間隔は
            dur * TW / (W + TW)      TW = max(前の幅, 今回の幅)
        になる（前のコメントの末尾が画面に入りきる時刻と、
        速い後続が前に追いつく時刻の、厳しい方）。

        旧実装は「表示時間 dur そのもの」レーンを占有していた。
        1920px 幅・幅 300px のコメントなら本来 1.1 秒で次を流せるのに 7 秒
        ふさいでいたことになり、**盛り上がりの瞬間ほどコメントが捨てられていた**
        （実測: 秒 4 件で 51%、秒 10 件で 78% が画面に出ない）。
        """
        prev = self.lane_last.get(y)
        if prev is None:
            return -999.0
        prev_start, prev_tw = prev
        tw = max(prev_tw, tw_new)
        gap = dur * tw / max(self.video_w + tw, 1)
        # 画素単位で接触しないよう少しだけ余裕を持たせる
        return prev_start + gap + LANE_SAFETY_GAP_SEC

    def occupy(self, y, start_sec, tw):
        self.lane_last[y] = (start_sec, tw)


# === ffmpegパス（system_utils で一本化） ===
from system_utils import get_ffmpeg_path, get_ffprobe_path
from paths import BIN_DIR
_ffmpeg_path = get_ffmpeg_path()


# エンコーダ検出結果のキャッシュ。
# このスクリプトは「クリップ 1 本ごとに」subprocess 起動されるため、毎回 ffmpeg を
# 起動して検出すると純粋な無駄になる（実測 288ms/回、HW エンコーダが無い環境では
# 3 回失敗するので 1 秒前後）。GPU 構成はそう変わらないのでファイルにキャッシュする。
_ENCODER_CACHE_FILE = os.path.join(BIN_DIR, "video_encoder.json")
_ENCODER_PROBE_TIMEOUT_SEC = 20


def _encoder_cache_key():
    """ffmpeg 実体が入れ替わったらキャッシュを捨てるためのキー。"""
    try:
        st = os.stat(_ffmpeg_path)
        return f"{_ffmpeg_path}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        return _ffmpeg_path


def _load_cached_encoder():
    try:
        with open(_ENCODER_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("key") == _encoder_cache_key() and data.get("encoder"):
            return data["encoder"]
    except Exception:
        pass
    return None


def _save_cached_encoder(name):
    try:
        os.makedirs(os.path.dirname(_ENCODER_CACHE_FILE), exist_ok=True)
        tmp = _ENCODER_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"key": _encoder_cache_key(), "encoder": name}, f)
        os.replace(tmp, _ENCODER_CACHE_FILE)
    except Exception as e:
        print(f"⚠️ エンコーダーキャッシュ保存に失敗（動作には影響なし）: {e}", flush=True)


def _detect_encoder():
    """使用可能なハードウェアエンコーダーを検出して返す（結果はキャッシュされる）"""
    cached = _load_cached_encoder()
    if cached:
        print(f"✅ エンコーダー: {cached}（キャッシュ）", flush=True)
        return cached

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
                timeout=_ENCODER_PROBE_TIMEOUT_SEC,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if ret.returncode == 0:
                print(f"✅ エンコーダー: {name}", flush=True)
                _save_cached_encoder(name)
                return name
        except subprocess.TimeoutExpired:
            # ドライバ不整合で probe が固まる環境があるため、次の候補へ進む
            print(f"⚠️ {name} の検出がタイムアウト、次の候補へ", flush=True)
        except Exception:
            pass
    print("⚠️ ハードウェアエンコーダーなし → libx264 (CPU) を使用", flush=True)
    _save_cached_encoder("libx264")
    return "libx264"

_VIDEO_ENCODER = _detect_encoder()


_DEFAULT_VIDEO_INFO = (1920, 1080, 30.0)


def get_video_info(video_path):
    """ffprobeで動画のfps/width/heightを取得。取得できなければ既定値にフォールバックする。

    旧実装は `json.loads(result.stdout)` を無防備に呼んでいたため、
    ffprobe が失敗して stdout が空だと JSONDecodeError で落ち、
    末尾のフォールバック (1920,1080,30) に到達できなかった。
    さらに一部コンテナは r_frame_rate に "0/0" を返すため 0 除算していた。
    """
    try:
        ffprobe_path = get_ffprobe_path()
    except Exception as e:
        print(f"⚠️ ffprobe が見つかりません（既定値で続行）: {e}", flush=True)
        return _DEFAULT_VIDEO_INFO

    cmd = [
        ffprobe_path, "-v", "quiet", "-print_format", "json",
        "-show_streams", video_path
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        print(f"⚠️ ffprobe 実行に失敗（既定値で続行）: {e}", flush=True)
        return _DEFAULT_VIDEO_INFO

    if result.returncode != 0 or not (result.stdout or "").strip():
        print(
            f"⚠️ ffprobe が動画情報を返しませんでした (rc={result.returncode})。既定値で続行します",
            flush=True,
        )
        return _DEFAULT_VIDEO_INFO

    try:
        info = json.loads(result.stdout)
    except ValueError as e:
        print(f"⚠️ ffprobe 出力の解析に失敗（既定値で続行）: {e}", flush=True)
        return _DEFAULT_VIDEO_INFO

    for stream in info.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        try:
            w = int(stream["width"])
            h = int(stream["height"])
        except (KeyError, TypeError, ValueError):
            continue

        fps = _DEFAULT_VIDEO_INFO[2]
        fps_str = stream.get("r_frame_rate") or "30/1"
        try:
            num, den = fps_str.split("/")
            num, den = float(num), float(den)
            # "0/0" を返すコンテナがある。0 除算を避け、既定 fps にフォールバック
            if den > 0 and num > 0:
                fps = num / den
            else:
                print(f"⚠️ 不正な r_frame_rate ({fps_str}) → fps={fps} で続行", flush=True)
        except (ValueError, TypeError):
            print(f"⚠️ r_frame_rate を解釈できません ({fps_str}) → fps={fps} で続行", flush=True)

        if w > 0 and h > 0:
            return w, h, fps

    print("⚠️ 動画ストリームが見つかりませんでした。既定値で続行します", flush=True)
    return _DEFAULT_VIDEO_INFO


# （削除済み）build_ffmpeg_overlay_filter():
#   PNG を `-i` で 1 枚ずつ入力する旧方式のフィルタ組み立て関数。
#   コマンドライン長制限 (WinError 206) を避けるため gen_clip 内の
#   `movie=` フィルタ方式に置き換えられたが、関数だけ残って誰も呼んでいなかった。
#   （dur = end - start の 0 除算ガードも無い状態だったので、復活させないこと）


# ffmpeg が h264 デコード時に出す「無害だがエラーに見える」定型警告。
# 出力には一切影響しないが、ログに大量に出るとユーザーが「エラーだ」と誤解するため
# ログ表示から除外する（例: Late SEI is not implemented ...）。
_BENIGN_FFMPEG_NOISE = (
    "Late SEI is not implemented",
    "If you want to help, upload a sample",
    "ffmpeg-devel mailing list",
)


def _is_benign_ffmpeg_noise(line):
    """出力に影響しない ffmpeg の定型警告行なら True。"""
    return any(token in line for token in _BENIGN_FFMPEG_NOISE)


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
        # 無害な定型警告はログに流さない（ユーザーの誤解防止）
        if not _is_benign_ffmpeg_noise(line):
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
    comment_overlay_enabled=True,
    comment_color="white",
    comment_fontsize=DEFAULT_FONTSIZE,
    comment_density=100,
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
    if not comment_overlay_enabled:
        # コメント流し OFF: overlay 一切作らず、既存「コメントなし → そのままコピー」パスに合流
        queue = []
        print("▶ コメント流し OFF（オーバーレイ生成スキップ）", flush=True)
    else:
        queue = [c for c in comments if start <= c["time"] <= end]
        queue.sort(key=lambda c: c["time"])

    # コメント量の間引き（100% = 元のコメントを全部使う）。
    #
    # ランダム抽出にはしない。時間の偏りが崩れて「盛り上がりが盛り上がりに見えない」
    # ためで、時刻順に一定間隔で残す方式にする。こうすると
    # 密なところは密なまま、薄いところは薄いまま、全体だけが薄くなる。
    density = max(1, min(100, int(comment_density or 100)))
    if density < 100 and queue:
        ratio = density / 100.0
        thinned = []
        acc = 0.0
        for c in queue:
            acc += ratio
            if acc >= 1.0:
                acc -= 1.0
                thinned.append(c)
        print(
            f"▶ コメント量 {density}%: {len(queue)} 件 → {len(thinned)} 件に間引き",
            flush=True,
        )
        queue = thinned

    total_count = len(queue)
    print(f"▶ コメント数: {total_count}件", flush=True)

    print(f"▶ コメント処理開始 ({len(queue)} 件)", flush=True)
    safe_write_progress(progress_path, 5, f"{clip_title}: コメント画像生成中", clip_idx)

    # 一時ディレクトリにPNG画像を書き出す
    tmp_dir = tempfile.mkdtemp(prefix="mp4chat_")
    overlay_items = []
    track_y = CommentTrack(w, h)
    skipped_no_lane = 0
    skipped_unrenderable = 0

    # レーン間隔はフォントサイズから実測で決める（固定値だと大きい文字で重なる）
    lane_spacing = lane_spacing_for(comment_fontsize, font_path)
    _lane_count = len(range(
        VIDEO_EDGE_PADDING_PX,
        max(h - VIDEO_EDGE_PADDING_PX, VIDEO_EDGE_PADDING_PX + 1),
        lane_spacing,
    ))
    print(
        f"▶ レーン間隔: {lane_spacing}px（fontsize={comment_fontsize} の実測から算出）"
        f" / 目安レーン数: {_lane_count}",
        flush=True,
    )

    try:
        for ci, c in enumerate(queue):
            rel = c["time"] - start
            dur = COMMENT_DISPLAY_DURATION_SEC

            # フォントで描画できない文字が多い場合はスキップ
            if font_path and not can_render_text(c["text"], font_path):
                print(f"⚠️ スキップ（描画不可）: {c['text'][:20]}", flush=True)
                skipped_unrenderable += 1
                continue

            img_arr, tw, th = create_text_image(
                c["text"],
                font_path=font_path,
                fontsize=comment_fontsize,
                color=comment_color,
            )

            min_y = VIDEO_EDGE_PADDING_PX
            max_y = h - th - VIDEO_EDGE_PADDING_PX
            candidates = list(range(min_y, max_y + 1, lane_spacing))
            if not candidates:
                # 動画が低解像度（またはフォントが大きすぎ）で、余白 50px を引くと
                # レーンが 1 本も取れないケース。旧実装は candidates が空 → y=None →
                # 全コメントを **1 行のログも出さずに** 捨てていた
                # （例: 240p の動画では 1 件もコメントが乗らない）。
                # 最低 1 レーンは確保し、状況を必ずログに出す。
                candidates = [max(0, min(min_y, max(0, h - th)))]
                print(
                    f"⚠️ 動画高 {h}px に対しコメント高 {th}px が大きく、"
                    f"通常のレーンを確保できません（y={candidates[0]} に固定して継続）",
                    flush=True,
                )
            np.random.shuffle(candidates)

            y = None
            for cand_y in candidates:
                if c["time"] >= track_y.next_free_time(cand_y, tw, dur):
                    y = cand_y
                    break

            if y is None:
                # 全レーンが先行コメントで埋まっている（同時刻に大量のコメント）。
                # 捨てること自体は仕様だが、件数は必ず可視化する。
                skipped_no_lane += 1
                continue

            track_y.occupy(y, c["time"], tw)

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
        if skipped_no_lane or skipped_unrenderable:
            print(
                f"   └ 内訳: 表示枠が空かず除外 {skipped_no_lane} 件 / "
                f"フォント未対応で除外 {skipped_unrenderable} 件 "
                f"(対象 {total_count} 件)",
                flush=True,
            )
        safe_write_progress(progress_path, 20, f"{clip_title}: 動画書き出し開始", clip_idx)

        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        # ffmpegコマンド構築
        # 重要: 大量コメント時に -i で PNG を 1 枚ずつ追加していくと
        # Windows のコマンドライン長制限 (~32K 文字, WinError 206) を超えてしまう。
        # PNG は filter_complex 内の `movie` フィルタで読み込むことで -i を元動画 1 個に抑える。
        cmd = [_ffmpeg_path, "-y"]

        # 入力: 元動画 (trim して切り出し) のみ
        cmd += ["-ss", str(start), "-t", str(clip_duration), "-i", video_path]

        if overlay_items:
            # filter graph を構築。各 PNG は `movie=path` で filter graph 内に読み込む。
            # ffmpeg の filter parser では:
            #   1段目 (filter arg value 内): ':' は値区切りなので '\:' でエスケープ
            #   2段目 (filtergraph 全体): その '\' をさらに '\\' でエスケープ
            # → 結果として ':' は '\\:' (バックスラッシュ2 個 + コロン) で記述する必要がある。
            # 単一引用符による wrap は filter_complex_script ファイル経由では効かないので
            # 必ず escape する。Windows パスの '\' は '/' に置換しておく。
            def _escape_movie_path(p):
                # ':' 以外にも filtergraph のメタ文字がある。
                # 一時ディレクトリは %TEMP%（= C:\Users\<ユーザー名>\...）配下なので、
                # ユーザー名に ' や , を含むアカウントだと filter graph が壊れて
                # クリップ生成が丸ごと失敗していた。まとめてエスケープする。
                p = p.replace("\\", "/")
                for ch in (":", "'", ",", ";", "[", "]"):
                    p = p.replace(ch, "\\\\" + ch)
                return p

            filter_parts = []
            prev = "[0:v]"
            n = len(overlay_items)
            for i, item in enumerate(overlay_items):
                label_mov = f"[mov{i}]"
                label_out = f"[v{i}]" if i < n - 1 else "[vout]"
                s = item["start_sec"]
                e = item["end_sec"]
                d = e - s
                tw = item["tw"]
                y = item["y"]
                x_expr = f"W-((W+{tw})*(t-{s:.3f})/{d:.3f})"
                enable_expr = f"between(t,{s:.3f},{e:.3f})"
                img_path_esc = _escape_movie_path(item["img_path"])
                # movie で PNG をロードして label_mov に出力 → overlay の入力として消費
                filter_parts.append(f"movie={img_path_esc}{label_mov}")
                filter_parts.append(
                    f"{prev}{label_mov}overlay=x='{x_expr}':y={y}:enable='{enable_expr}'{label_out}"
                )
                prev = label_out

            filter_complex = ";\n".join(filter_parts)
            filter_script_path = os.path.join(tmp_dir, "filter.txt")
            with open(filter_script_path, "w", encoding="utf-8") as ff:
                ff.write(filter_complex)
            print(
                f"📝 filter_complex: {len(filter_complex)} 文字 / overlay {n} 件 → ファイルで渡します",
                flush=True,
            )
            # "0:a?" の '?' は「音声ストリームが無ければ黙って省略」の意味。
            # '?' 無しだと無音の録画（音声トラックなし）で
            # "Stream map '0:a' matches no streams" となりクリップ生成が失敗していた。
            cmd += ["-filter_complex_script", filter_script_path, "-map", "[vout]", "-map", "0:a?"]
        else:
            # コメントなし: そのままコピー
            cmd += ["-map", "0:v", "-map", "0:a?"]

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
        # ⚠️ ここで progress=-1 を書いてはいけない。
        # UI の 2 つのポーリング経路（index2.js の pollProgress と解析画面のループ）は
        # どちらも progress < 0 を見た瞬間にポーリングを打ち切って「エラー終了」扱いにする。
        # gen_clip は main() 側で最大 3 回リトライされるので、1 回目の失敗で -1 を書くと
        # **リトライが成功しても UI はエラー表示のまま二度と更新されない**。
        # 進捗の最終判定は main() に任せ、ここではログと例外伝搬だけ行う。
        traceback.print_exc()
        # 例外を呼び出し元に伝搬させる（main() のリトライ＆失敗判定に必須）。
        # 以前はここで握り潰していたため「処理完了」扱いになりファイル無生成のまま終了する致命バグになっていた。
        raise

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
    parser.add_argument("--comment-overlay", dest="comment_overlay", default="true")  # コメント流し ON/OFF（"true"/"false"）
    parser.add_argument("--comment-color", dest="comment_color", default="#FFFFFF")   # コメント色 #RRGGBB
    parser.add_argument("--comment-fontsize", dest="comment_fontsize", type=int, default=DEFAULT_FONTSIZE)  # コメントフォントサイズ px
    # コメント量（%）。100 = CSV のコメントを全部使う。下げると時刻順に等間隔で間引く
    parser.add_argument("--comment-density", dest="comment_density", type=int, default=100)
    # --is-last は app.py から渡されておらず、本体でも参照されていない。
    # 外部から叩かれた場合に「unrecognized arguments」で落ちないよう受け口だけ残す。
    parser.add_argument("--is-last", default="False", help=argparse.SUPPRESS)
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
                    comment_overlay_enabled=(args.comment_overlay.strip().lower() == "true"),
                    comment_color=args.comment_color,
                    comment_fontsize=args.comment_fontsize,
                    comment_density=args.comment_density,
                )
                # 多層防御: gen_clip が成功と返しても、実際にファイルが生成されたか念押しで確認する
                if not os.path.exists(out_file):
                    raise RuntimeError(
                        f"処理完了と判定されたが出力ファイルが存在しません: {out_file}"
                    )
                print(f"✅ Clip {i} 成功 (試行 {attempt})", flush=True)
                success = True
                break
            except Exception as e:
                print(f"⚠️ Clip {i} 失敗 (試行 {attempt}/{MAX_RETRY}): {e}", flush=True)
                traceback.print_exc()
                if attempt < MAX_RETRY:
                    # リトライ余地がある間は progress を負にしない。
                    # UI は progress < 0 でポーリングを止めてしまうため、途中経過として
                    # -1 を書くと「この後リトライで成功しても UI は死んだまま」になる。
                    safe_write_progress(
                        progress_path,
                        0,
                        f"{display_title}: 失敗したので再試行します ({attempt}/{MAX_RETRY})",
                        clip_idx,
                    )
                    print(f"⏳ {RETRY_DELAY}秒後にリトライします...", flush=True)
                    time.sleep(RETRY_DELAY)

        if not success:
            print(f"❌ Clip {i} は {MAX_RETRY} 回失敗 → スキップ", flush=True)
            # ここでも progress を負にしない。
            # 親（app.py の run_process）は retcode != 0 を見た直後に必ず
            # 「⚠️ クリップ N の生成に失敗しました（次のクリップへ進みます）」を
            # progress.json へ書き、残りのクリップを続行する。
            # 子が先に -1 を書くと、その上書きが起きるまでの数百ms の隙に UI が
            # ポーリングを打ち切り、後続クリップの進捗が一切出なくなる。
            # 最終的な状態の決定権は親に持たせ、子は exit code 1 で異常を伝える。
            safe_write_progress(
                progress_path, 0, f"{display_title}: 失敗（スキップして続行）", clip_idx
            )
            all_success = False
            continue

        # 完了を確実に書き込む
        safe_write_progress(progress_path, 100, f"{display_title}: 完了", clip_idx)
        print(f"📝 進捗ファイル更新: 100% - {display_title}: 完了", flush=True)

        print(f"🧹 Clip {i} 処理完了後のメモリクリーンアップ...", flush=True)
        gc.collect()
        print(f"✅ メモリクリーンアップ完了", flush=True)

    if all_success:
        print("✅ 全クリップ処理完了", flush=True)
        return 0
    else:
        # 1 件でも失敗していたら non-zero で終わり、親（app.py）に異常を伝える。
        # 以前はここを 0 で抜けていたので「処理完了」と誤認されていた。
        print("❌ 一部のクリップが失敗しました", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
