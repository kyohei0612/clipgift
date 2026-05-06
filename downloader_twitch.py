"""
Twitch VOD ダウンローダー（メインエントリ）

Twitch の VOD（録画されたライブ配信）から動画 + チャットを取得し、
既存 YouTube 用フォルダ構造（mp4 + comments_cleaned.csv + waveform.json）と互換の出力を生成する。

設計方針（整合君監修）:
- 既存 downloader.py には触らない（リスク最小化）
- チャット取得は twitch_chat モジュールに分離
- 動画取得は yt-dlp（Twitch VOD は枯れた対応）
- 波形生成は既存 downloader.py と同じロジック（DRY 違反は v1.x で system_utils 統合予定）

使い方（subprocess 経由）:
    downloader.py が URL 判別して、Twitch の場合に当モジュールへ振り分ける。
"""

import os
import shutil
import subprocess
from typing import Optional

from downloader import safe_write_json
from system_utils import get_ffmpeg_path, get_ffprobe_path
from twitch_chat import (
    extract_twitch_video_id,
    fetch_twitch_chat,
    fetch_twitch_video_title,
    is_twitch_url,  # noqa: F401  -- downloader.py から import される
    write_chat_csv,
)
from twitch_video import download_twitch_video_native

# === 波形生成設定（既存 downloader.py と統一） ===
_BASE_DIR = os.path.abspath(os.path.dirname(__file__))
_AUDIOWAVEFORM_PATH = os.path.join(_BASE_DIR, "bin", "audiowaveform.exe")


def download_twitch_video(
    video_id: str,
    output_folder: str,
    title: str,
    progress_path: Optional[str] = None,
) -> str:
    """
    自前 HLS ダウンローダーで Twitch VOD を取得（1080p 優先、yt-dlp 非依存）

    Returns:
        出力ファイルパス
    """
    output_file = os.path.join(output_folder, f"{title}.mp4")

    if progress_path:
        safe_write_json(progress_path, {
            "progress": 5,
            "message": "Twitch 動画ダウンロード開始",
            "phase": "動画ダウンロード",
        })

    def _on_segment_progress(completed: int, total: int) -> None:
        # 動画 DL は 5-45% 帯
        pct = 5 + int((completed / max(total, 1)) * 40)
        if progress_path:
            safe_write_json(progress_path, {
                "progress": pct,
                "message": f"Twitch 動画 DL 中 ({completed}/{total} セグメント)",
                "phase": "動画ダウンロード",
            })

    download_twitch_video_native(
        video_id,
        output_file,
        max_height=1080,
        progress_callback=_on_segment_progress,
    )

    if not os.path.exists(output_file):
        raise RuntimeError(f"動画ファイルが生成されませんでした: {output_file}")

    print(f"[INFO] Twitch 動画ダウンロード完了: {output_file}", flush=True)
    if progress_path:
        safe_write_json(progress_path, {
            "progress": 45,
            "message": "Twitch 動画ダウンロード完了",
            "phase": "動画ダウンロード",
        })

    return output_file


def _choose_pixels_per_second(duration_sec: int) -> int:
    """波形生成の解像度（duration による調整、既存 downloader.py と一致）"""
    if duration_sec <= 600:
        return 500
    elif duration_sec <= 3600:
        return 800
    elif duration_sec <= 3 * 3600:
        return 1000
    elif duration_sec <= 6 * 3600:
        return 1500
    return 2000


def _generate_waveform(mp4_path: str, title_folder: str, progress_path: Optional[str]) -> None:
    """
    波形生成（既存 downloader.py と同じロジック）

    Note: DRY 違反だが、ローンチ前リスク最小化のため downloader.py には触れない。
          v1.x 以降で `system_utils` に統合する予定（整合君タスク）。
    """
    if progress_path:
        safe_write_json(progress_path, {
            "progress": 85,
            "message": "波形生成中",
            "phase": "波形生成",
        })

    ffmpeg_path = get_ffmpeg_path()
    ffprobe_path = get_ffprobe_path()
    wav_path = os.path.join(title_folder, "waveform.wav")
    json_path = os.path.join(title_folder, "waveform.json")

    try:
        cmd_wav = [
            ffmpeg_path, "-y", "-i", mp4_path, "-vn",
            "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
            wav_path,
        ]
        subprocess.run(cmd_wav, check=True, creationflags=subprocess.CREATE_NO_WINDOW)

        cmd_probe = [
            ffprobe_path, "-i", mp4_path,
            "-show_entries", "format=duration",
            "-v", "quiet", "-of", "csv=p=0",
        ]
        duration_output = subprocess.check_output(
            cmd_probe, creationflags=subprocess.CREATE_NO_WINDOW
        ).decode("utf-8", errors="replace").strip()
        duration_sec = int(float(duration_output))
        print(f"[INFO] 動画長さ: {duration_sec} 秒", flush=True)

        pps = _choose_pixels_per_second(duration_sec)
        cmd_json = [
            _AUDIOWAVEFORM_PATH, "-i", wav_path, "-o", json_path,
            "--pixels-per-second", str(pps), "--bits", "8",
        ]
        result = subprocess.run(
            cmd_json, check=True, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.stderr:
            print(
                "[WARN] audiowaveform stderr:",
                result.stderr.decode("utf-8", errors="replace"),
            )

        if os.path.exists(wav_path):
            os.remove(wav_path)

        print(f"[INFO] 波形データ生成完了: {json_path}", flush=True)
    except Exception as e:
        print(f"[ERROR] 波形生成失敗: {e}", flush=True)


def download_video_and_chat_twitch(
    url: str, base_output_folder: str, progress_path: str
) -> None:
    """
    Twitch VOD の動画 + チャットを取得してフォルダに保存
    （既存 download_video_and_chat と同等の振る舞い）

    出力構造:
        {base_output_folder}/{title}/
            ├── {title}.mp4               動画（1080p 以下）
            ├── comments_cleaned.csv      チャット（YouTube 互換 CSV）
            └── waveform.json             波形データ
    """
    output_folder = os.path.abspath(base_output_folder)
    os.makedirs(output_folder, exist_ok=True)

    safe_write_json(progress_path, {"progress": 0, "message": "Twitch VOD 解析中"})

    video_id = extract_twitch_video_id(url)
    if not video_id:
        raise ValueError(f"Twitch VOD URL ではありません: {url}")

    print(f"[INFO] Twitch video ID: {video_id}", flush=True)

    title = fetch_twitch_video_title(video_id)
    print(f"[INFO] タイトル: {title}", flush=True)

    title_folder = os.path.join(output_folder, title)
    if os.path.exists(title_folder):
        shutil.rmtree(title_folder)
        print(f"[INFO] 既存フォルダを削除して再ダウンロード: {title_folder}", flush=True)
    os.makedirs(title_folder, exist_ok=True)

    # 動画ダウンロード（0〜45%、自前 HLS ダウンローダー使用）
    download_twitch_video(video_id, title_folder, title, progress_path=progress_path)

    # チャット取得（45〜80%）
    safe_write_json(progress_path, {
        "progress": 45,
        "message": "チャットダウンロード開始",
        "phase": "チャットダウンロード",
    })

    comments = fetch_twitch_chat(video_id, progress_path=progress_path)

    csv_path = os.path.join(title_folder, "comments_cleaned.csv")
    write_chat_csv(comments, csv_path)
    print(f"[INFO] チャット CSV 保存: {csv_path}", flush=True)

    safe_write_json(progress_path, {
        "progress": 80,
        "message": "チャットダウンロード完了",
        "phase": "チャットダウンロード",
    })

    # 波形生成（85〜100%）
    mp4_path = os.path.join(title_folder, f"{title}.mp4")
    _generate_waveform(mp4_path, title_folder, progress_path)

    safe_write_json(progress_path, {
        "progress": 100,
        "message": "完了",
        "phase": "完了",
    })
    print(f"[INFO] {title} のすべての処理が完了しました", flush=True)
