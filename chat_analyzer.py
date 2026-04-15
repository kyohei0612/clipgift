"""
チャット解析ロジック。純粋関数のみ。

用語:
- lines: [(time_str, comment), ...] の形でチャットを渡す
- keywords: 検出対象のキーワードリスト
- start_threshold / end_threshold: 10秒窓あたりのコメント数の閾値
- clip_offset: ヒット開始から遡って切り出す秒数
"""
import re
import logging
import unicodedata
from datetime import timedelta

logger = logging.getLogger(__name__)


def parse_time_to_seconds(t):
    """'mm:ss' または 'hh:mm:ss' を秒に変換。失敗したら None。"""
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
        logger.warning("parse_time_to_seconds エラー: %s (%s)", e, t)
        return None


def format_seconds_to_time(s):
    """秒数 → 'h:mm:ss' または 'm:ss' 文字列。"""
    if isinstance(s, str):
        s = float(s)
    td = timedelta(seconds=int(s))
    total_seconds = int(td.total_seconds())
    m, s = divmod(total_seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h > 0 else f"{m}:{s:02}"


def normalize_comment(comment):
    """全角w→半角w、NFKC 正規化、小文字化。キーワード判定用。"""
    comment = comment.replace("ｗ", "w")
    comment = unicodedata.normalize("NFKC", comment)
    return comment.lower()


def analyze_chat_single_keyword(lines, keyword, start_threshold, end_threshold, clip_offset):
    """単一キーワードについてクリップ候補を検出する。"""
    logger.info("🎯 キーワード: %s", keyword)
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
    """重複・隣接するクリップをマージする。"""
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
    """
    複数キーワードで解析し、マージ済みのクリップ一覧を返す。
    video_duration_sec を渡すと、動画長を超える end をクランプする。
    戻り値は UI 向けのキー構造:
        [{"start", "end", "start_str", "end_str", "hitLogs"}]
    """
    logger.info("📦 キーワード: %s", keywords)
    logger.info("📈 コメント総数: %d", len(lines))
    logger.info("🎥 動画長さ(秒): %s", video_duration_sec)

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
