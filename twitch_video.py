"""
Twitch VOD 動画ダウンローダー（自社製、yt-dlp 非依存）

Twitch VOD（アーカイブ）は HLS（HTTP Live Streaming）形式で配信される。
GraphQL でアクセストークンを取得 → HLS マスタープレイリスト取得 →
バリアント選択 → セグメント並列 DL → ffmpeg で結合。

self-built principle: 「他社ツール / yt-dlp に頼らず、すべて自前で実装」
これがクリップギフトの核となるプロダクト価値（200 万 + 3.5 年 投資の根拠）。

依存: requests + ffmpeg のみ
"""

import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Optional, Tuple

import requests

from system_utils import get_ffmpeg_path
from twitch_chat import TWITCH_CLIENT_ID, TWITCH_GQL_URL


# === Twitch HLS 設定 ===
USHER_BASE_URL = "https://usher.ttvnw.net/vod/{video_id}.m3u8"

GET_ACCESS_TOKEN_QUERY = """
query GetVideoAccessToken($videoID: ID!) {
  videoPlaybackAccessToken(id: $videoID, params: {platform: "web", playerType: "site", playerBackend: "mediaplayer"}) {
    value
    signature
  }
}
""".strip()

SEGMENT_DOWNLOAD_TIMEOUT_SEC = 60
SEGMENT_DOWNLOAD_RETRIES = 3
PARALLEL_DOWNLOAD_WORKERS = 8


def get_access_token(video_id: str) -> Tuple[str, str]:
    """GraphQL で VOD のアクセストークンを取得"""
    body = {"query": GET_ACCESS_TOKEN_QUERY, "variables": {"videoID": video_id}}
    headers = {"Client-ID": TWITCH_CLIENT_ID}
    r = requests.post(TWITCH_GQL_URL, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    data = r.json()
    token_data = (data.get("data") or {}).get("videoPlaybackAccessToken")
    if not token_data:
        raise RuntimeError(
            "VOD アクセストークン取得失敗（VOD が削除済み or 限定公開 / 加入者限定の可能性）"
        )
    return token_data["value"], token_data["signature"]


def fetch_master_playlist(video_id: str, token: str, signature: str) -> str:
    """マスタープレイリスト（M3U8）を取得"""
    params = {
        "allow_source": "true",
        "allow_audio_only": "true",
        "allow_spectre": "true",
        "player": "twitchweb",
        "playlist_include_framerate": "true",
        "nauth": token,
        "nauthsig": signature,
    }
    url = USHER_BASE_URL.format(video_id=video_id)
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.text


def parse_master_playlist(content: str) -> List[dict]:
    """マスタープレイリストをパースして画質バリアント一覧を返す"""
    lines = content.split("\n")
    variants: List[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXT-X-MEDIA"):
            name_match = re.search(r'NAME="([^"]+)"', line)
            group_match = re.search(r'GROUP-ID="([^"]+)"', line)
            name = name_match.group(1) if name_match else ""
            group = group_match.group(1) if group_match else ""
            j = i + 1
            while j < len(lines) and not lines[j].startswith("#EXT-X-STREAM-INF"):
                j += 1
            if j < len(lines):
                stream_inf = lines[j]
                res_match = re.search(r"RESOLUTION=(\d+x\d+)", stream_inf)
                bw_match = re.search(r"BANDWIDTH=(\d+)", stream_inf)
                resolution = res_match.group(1) if res_match else ""
                bandwidth = int(bw_match.group(1)) if bw_match else 0
                if j + 1 < len(lines):
                    variant_url = lines[j + 1].strip()
                    if variant_url and not variant_url.startswith("#"):
                        height = (
                            int(resolution.split("x")[1]) if "x" in resolution else 0
                        )
                        variants.append({
                            "name": name,
                            "group": group,
                            "resolution": resolution,
                            "height": height,
                            "bandwidth": bandwidth,
                            "url": variant_url,
                        })
            i = j + 2
        else:
            i += 1
    return variants


def choose_variant(variants: List[dict], max_height: int = 1080) -> dict:
    """指定画質以下で最も高画質のバリアントを選ぶ"""
    if not variants:
        raise RuntimeError("バリアントが見つかりません")
    video_variants = [
        v for v in variants
        if v.get("group") != "audio_only" and v.get("height", 0) > 0
    ]
    if not video_variants:
        # フォールバック: bandwidth で選ぶ
        return max(variants, key=lambda v: v.get("bandwidth", 0))
    eligible = [v for v in video_variants if v["height"] <= max_height]
    if not eligible:
        # 全て max_height 超えなら一番低画質を選ぶ
        return min(video_variants, key=lambda v: v["height"])
    return max(eligible, key=lambda v: v["height"])


def fetch_variant_playlist(variant_url: str) -> List[Tuple[float, str]]:
    """バリアントプレイリストから (duration, segment_url) のリストを返す"""
    r = requests.get(variant_url, timeout=30)
    r.raise_for_status()
    content = r.text
    base_url = variant_url.rsplit("/", 1)[0] + "/"

    segments: List[Tuple[float, str]] = []
    lines = content.split("\n")
    current_duration = 0.0
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            m = re.search(r"#EXTINF:([\d.]+)", line)
            if m:
                current_duration = float(m.group(1))
        elif line and not line.startswith("#"):
            seg_url = line if line.startswith("http") else base_url + line
            segments.append((current_duration, seg_url))
            current_duration = 0.0
    return segments


def _download_one_segment(idx: int, url: str, output_folder: str) -> Tuple[int, str]:
    """1 セグメントをダウンロード（リトライ込み）"""
    ts_path = os.path.join(output_folder, f"seg_{idx:06d}.ts")
    last_err: Optional[Exception] = None
    for attempt in range(SEGMENT_DOWNLOAD_RETRIES):
        try:
            r = requests.get(url, timeout=SEGMENT_DOWNLOAD_TIMEOUT_SEC, stream=True)
            r.raise_for_status()
            with open(ts_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return idx, ts_path
        except requests.exceptions.RequestException as e:
            last_err = e
            if attempt < SEGMENT_DOWNLOAD_RETRIES - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"セグメント DL 失敗（{SEGMENT_DOWNLOAD_RETRIES} 回）: {last_err}")


def download_segments(
    segments: List[Tuple[float, str]],
    output_folder: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> List[str]:
    """全セグメントを並列ダウンロード"""
    os.makedirs(output_folder, exist_ok=True)
    ts_files: List[Optional[str]] = [None] * len(segments)
    completed = 0

    with ThreadPoolExecutor(max_workers=PARALLEL_DOWNLOAD_WORKERS) as executor:
        futures = {
            executor.submit(_download_one_segment, i, url, output_folder): i
            for i, (_, url) in enumerate(segments)
        }
        for future in as_completed(futures):
            idx, ts_path = future.result()
            ts_files[idx] = ts_path
            completed += 1
            if progress_callback:
                progress_callback(completed, len(segments))

    # 全部 None じゃないことを確認
    if any(t is None for t in ts_files):
        raise RuntimeError("一部のセグメント DL に失敗")
    return [str(t) for t in ts_files]


def concat_segments_to_mp4(ts_files: List[str], output_mp4: str) -> None:
    """ffmpeg で .ts セグメントを mp4 に結合"""
    list_file = output_mp4 + ".filelist.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for ts in ts_files:
            f.write(f"file '{os.path.abspath(ts)}'\n")

    ffmpeg = get_ffmpeg_path()
    cmd = [
        ffmpeg, "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        output_mp4,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    # 一時ファイル削除
    try:
        os.remove(list_file)
    except OSError:
        pass

    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg concat 失敗: {result.stderr.decode('utf-8', errors='replace')}"
        )


def download_twitch_video_native(
    video_id: str,
    output_path: str,
    max_height: int = 1080,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> str:
    """
    Twitch VOD を自前ロジックで動画ダウンロード（yt-dlp 非依存）

    Args:
        video_id: Twitch video ID（数字文字列）
        output_path: 出力 mp4 ファイルパス
        max_height: 最大画質（デフォルト 1080）
        progress_callback: (completed, total) → None の進捗コールバック

    Returns:
        出力パス
    """
    print(f"[INFO] Twitch VOD ダウンロード開始（自社製）: video_id={video_id}", flush=True)

    print("[INFO] アクセストークン取得中...", flush=True)
    token, signature = get_access_token(video_id)

    print("[INFO] マスタープレイリスト取得中...", flush=True)
    master = fetch_master_playlist(video_id, token, signature)

    variants = parse_master_playlist(master)
    available = ", ".join(f"{v['name']}({v['resolution']})" for v in variants)
    print(f"[INFO] 利用可能画質: {available}", flush=True)

    chosen = choose_variant(variants, max_height=max_height)
    print(f"[INFO] 選択画質: {chosen['name']} ({chosen['resolution']})", flush=True)

    segments = fetch_variant_playlist(chosen["url"])
    print(f"[INFO] セグメント数: {len(segments)}", flush=True)
    if not segments:
        raise RuntimeError("セグメントが見つかりません")

    temp_folder = output_path + ".segments"
    print(
        f"[INFO] セグメント並列 DL 開始（{PARALLEL_DOWNLOAD_WORKERS} 並列）...",
        flush=True,
    )
    ts_files = download_segments(
        segments, temp_folder, progress_callback=progress_callback
    )

    print("[INFO] mp4 へ結合中...", flush=True)
    concat_segments_to_mp4(ts_files, output_path)

    # 一時セグメントフォルダ削除
    try:
        shutil.rmtree(temp_folder, ignore_errors=True)
    except OSError:
        pass

    print(f"[INFO] Twitch VOD ダウンロード完了: {output_path}", flush=True)
    return output_path
