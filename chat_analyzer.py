"""
チャット解析ロジック。純粋関数のみ。

用語:
- lines: [(time_str, comment), ...] の形でチャットを渡す
- keywords: 検出対象のキーワードリスト
- start_threshold / end_threshold: 10秒窓あたりのコメント数の閾値
- clip_offset: ヒット開始から遡って切り出す秒数
"""

import re
import bisect
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


def analyze_chat_single_keyword(
    lines, keyword, start_threshold, end_threshold, clip_offset
):
    """単一キーワードについてクリップ候補を検出する。"""
    logger.info("🎯 キーワード: %s", keyword)
    normalized_kw = normalize_comment(keyword)
    pattern = re.compile(re.escape(normalized_kw), re.IGNORECASE)

    # idx（lines 内での位置）を必ず持たせる。
    # 解析はキーワードごとに独立して走るので、1 つのコメントが複数キーワードに
    # 当たると、その分だけ別々の clip の hitLogs に入る。マージ時に
    # **文字列で重複を判定すると「同じ秒に別の人が同じコメントを打った」まで
    # 潰れてしまう**（盛り上がりの強さが消える）。
    # コメントの実体を指す idx で判定すれば、両方を正しく区別できる。
    hit_times = []
    for idx, (time_str, comment) in enumerate(lines):
        sec = parse_time_to_seconds(time_str)
        if sec is None:
            continue
        if pattern.search(normalize_comment(comment)):
            hit_times.append({"idx": idx, "sec": sec, "comment": comment})

    if not hit_times:
        return []

    # ソート必須:
    #   - max_time / clip_end で time_list[-1] を「最後のヒット時刻」として使っている
    #   - 下の window_count が二分探索前提
    # CSV の行が時刻順に並んでいる保証はない（結合したチャットログ等）ので明示的に並べる。
    hit_times.sort(key=lambda t: t["sec"])
    time_list = [t["sec"] for t in hit_times]
    clips = []
    max_time = time_list[-1]

    def window_count(lo, hi):
        """[lo, hi) に入るヒット数。

        旧実装は毎回 time_list 全体を走査していたため
        O(動画長 × ヒット数) になり、10 時間配信 × 大量ヒットで解析が固まっていた。
        ソート済み配列に対する二分探索なら O(log n) で同じ値が出る。
        """
        return bisect.bisect_left(time_list, hi) - bisect.bisect_left(time_list, lo)

    i = 0
    while i <= max_time:
        count = window_count(i, i + 10)
        if count >= start_threshold:
            clip_start = max(0, i - clip_offset)
            zero_count = 0
            j = i + 10
            found_end = False
            while j <= max_time + 10:
                c = window_count(j, j + 10)
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
            # (idx, sec, 表示文字列) で保持する。文字列化は最後（analyze_chat）で行う。
            hit_entries = [
                (t["idx"], t["sec"], f"{format_seconds_to_time(t['sec'])} → {t['comment']}")
                for t in hit_times
                if clip_start <= t["sec"] <= clip_end
            ]
            clips.append({
                "start": clip_start,
                "end": clip_end,
                "hitEntries": hit_entries,
                # 後方互換: 単独で使う呼び出し元のために文字列版も持たせる
                "hitLogs": [e[2] for e in hit_entries],
            })
            i = clip_end
        else:
            i += 1

    return clips


def merge_clips(clips):
    """重複・隣接するクリップをマージする。

    引数の clips は変更しない（旧実装は呼び出し元のリストを破壊的に sort していた）。
    start/end の反転補正は sort より **前** に行う。旧実装は sort 後に swap しており、
    反転クリップが混ざるとソート順が崩れたままマージ判定に入っていた。
    """
    if not clips:
        return []
    normalized = []
    for c in clips:
        c = dict(c)
        if c["start"] > c["end"]:
            c["start"], c["end"] = c["end"], c["start"]
        c["hitEntries"] = list(c.get("hitEntries", []))
        c["hitLogs"] = list(c.get("hitLogs", []))
        normalized.append(c)
    normalized.sort(key=lambda x: x["start"])
    merged = [normalized[0]]
    for clip in normalized[1:]:
        last_clip = merged[-1]
        if clip["start"] <= last_clip["end"]:
            last_clip["end"] = max(last_clip["end"], clip["end"])
            last_clip["hitEntries"].extend(clip.get("hitEntries", []))
            last_clip["hitLogs"].extend(clip.get("hitLogs", []))
        else:
            merged.append(clip)

    # マージ後に「同じコメント（idx が同じ）」を 1 件へ畳む。
    # キーワードを複数指定すると、両方に当たったコメントがキーワードの数だけ
    # 入ってしまうため（例: 「大草原ｗｗ」は 草 と ｗ の両方に当たる）。
    # ⚠️ 文字列ではなく idx で判定すること。文字列だと「同じ秒に別の人が
    #    同じコメントを打った」ケースまで潰れる。
    # 並びは (時刻, idx) 順＝チャットの流れ順に直す。
    # 単純連結だと「草の分ぜんぶ → ｗの分ぜんぶ」となり、境目で時刻が逆行していた。
    for clip in merged:
        entries = clip.get("hitEntries") or []
        if not entries:
            continue
        seen_idx = set()
        unique = []
        for idx, sec, text in entries:
            if idx in seen_idx:
                continue
            seen_idx.add(idx)
            unique.append((idx, sec, text))
        unique.sort(key=lambda e: (e[1], e[0]))
        clip["hitEntries"] = unique
        clip["hitLogs"] = [e[2] for e in unique]
    return merged


def analyze_chat(
    lines,
    keywords,
    start_threshold,
    end_threshold,
    clip_offset,
    video_duration_sec=None,
):
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
            analyze_chat_single_keyword(
                lines, kw, start_threshold, end_threshold, clip_offset
            )
        )

    merged = merge_clips(all_clips)

    # `is not None` ではなく truthy 判定にしている理由:
    # app.py は videoDuration 未指定時に 0 を渡してくる。`is not None` だと
    # 全クリップの end が 0 に丸められ、解析結果が全滅する。
    if video_duration_sec:
        # end だけをクランプしていたため、start が動画長を超えたクリップで
        # start > end の逆転レンジが生成されていた。start も同時に丸め、
        # それでも潰れてしまう（長さ 0 以下）クリップは捨てる。
        clamped = []
        for clip in merged:
            clip["start"] = min(clip["start"], video_duration_sec)
            clip["end"] = min(clip["end"], video_duration_sec)
            if clip["end"] > clip["start"]:
                clamped.append(clip)
            else:
                logger.info(
                    "動画長 %s 秒を超えるクリップを除外: %s-%s",
                    video_duration_sec, clip["start"], clip["end"],
                )
        merged = clamped

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
