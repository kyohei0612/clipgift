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
from chat_filter import should_skip_comment, strip_emojis

# --- 子プロセスのコンソール窓を全面的に抑止（Windows） ---
# pytubefix 10.x は nodejs-wheel-binaries を同梱し、署名解読（sig/nsig）と
# botGuard で **node.exe を subprocess で起動する**。9.5.x では node が無く
# 「Node.js not found」でスキップされていたので表面化しなかった。
#   - sig_nsig/node_runner.py … Cipher 1 個につき node 2 プロセス
#   - botGuard/bot_guard.py   … po_token 生成でさらに 1 プロセス
# どちらも creationflags を渡しておらず、ClipGift 本体は Tauri から
# CREATE_NO_WINDOW（コンソール無し）で起動されるため、node が起動するたびに
# **新しい黒いコンソール窓が開く**。クライアントを順に試すので大量に開く。
#
# pytubefix 側を直せないので、このプロセス内の Popen 既定値として
# CREATE_NO_WINDOW を強制する。yt-dlp が spawn する ffmpeg にも同じ効果がある。
# 既に明示指定している箇所とは OR で合成されるので競合しない。
if os.name == "nt":
    _CREATE_NO_WINDOW = 0x08000000
    _orig_popen_init = subprocess.Popen.__init__

    def _popen_no_window(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | _CREATE_NO_WINDOW
        return _orig_popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _popen_no_window

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

# ffmpeg / audiowaveform の上限秒数。
# 指定しないとハング時に DL スレッドが永久に戻らず、UI が固まったままになる。
FFMPEG_MERGE_TIMEOUT_SEC = 3600   # 映像+音声の結合（-c copy なので通常は数十秒）
WAVEFORM_TIMEOUT_SEC = 1800       # wav 抽出 / 波形 JSON 生成

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
    # 正規表現が非貪欲なので、JSON の途中で切れて json.loads が失敗することがある。
    # 素通しすると生の JSONDecodeError がユーザーに出てしまうため、None に倒して
    # 呼び出し側の親切メッセージ（ChatNotAvailableError）経路に合流させる。
    yid = None
    if yid_m:
        try:
            yid = json.loads(yid_m.group(1))
        except ValueError as e:
            print(f"⚠️ ytInitialData の解析に失敗しました: {e}", flush=True)
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
        if "replayChatItemAction" not in a:
            continue
        # 1 つの replayChatItemAction に複数の action がぶら下がることがある。
        # 旧実装は [0] しか見ておらず、2 個目以降のコメントを取りこぼしていた。
        for item in a["replayChatItemAction"].get("actions") or [{}]:
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
                    # 絵文字 / ピクトグラムを取り除く（BIZ UDゴシック等で描画失敗するため）
                    msg = strip_emojis(msg).strip()
                    # 共通フィルタ: 英数字のみ / Bot / システムメッセージを除外
                    if should_skip_comment(msg, user=author):
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


class ChatNotAvailableError(RuntimeError):
    """チャットリプレイが取得できない動画に対するエラー（ライブ配信中 / リプレイ無効化 / メンバー限定など）。"""

    def __init__(self, reason: str, user_message: str):
        super().__init__(reason)
        self.user_message = user_message


def _detect_unavailable_reason(html: str) -> tuple[bool, str]:
    """HTML を見てチャット取得不可の理由を判別。
    Returns: (is_unavailable, user_facing_message)
    """
    # ライブ配信中（=リプレイ未生成）
    if '"isLive":true' in html or '"isLiveBroadcast":"True"' in html:
        return True, (
            "この URL はライブ配信中の動画のようです。\n"
            "配信終了 → アーカイブ生成 → チャットリプレイ反映まで待ってから再度お試しください\n"
            "（通常、配信終了から数十分〜数時間後にチャットリプレイが取得可能になります）。"
        )
    # チャットリプレイ無効化済み
    if '"isChatReplayEnabled":false' in html:
        return True, (
            "この動画はチャットリプレイが無効化されています。\n"
            "投稿者がチャットリプレイをオフにしている場合、コメント取得はできません。"
        )
    # メンバー限定など、通常公開でない
    if '"isPrivate":true' in html or '"isUnlisted":true' in html:
        return True, (
            "この動画は限定公開・非公開・メンバー限定のようです。\n"
            "通常公開の動画 URL でお試しください。"
        )
    # その他
    return False, ""


def check_chat_available(url):
    """動画 DL の前にチャット取得可否を確認する早期チェック。
    取得不可なら ChatNotAvailableError を即発生 → 親プロセスが progress.json に
    ユーザー向けメッセージを書いて exit(2) する。
    既存の HTML 取得 + 判定ロジックを再利用するだけなので軽量。"""
    print("▶ チャット可否を事前チェック:", url, flush=True)
    html = _fetch_html(url)

    # ライブ配信中 / リプレイ無効化 / 限定公開 などを判定
    is_unavailable, msg = _detect_unavailable_reason(html)
    if is_unavailable:
        raise ChatNotAvailableError("CHAT_NOT_AVAILABLE", msg)

    api_key, version, yid = _extract_params(html)
    if not yid:
        raise ChatNotAvailableError(
            "NO_YT_INITIAL_DATA",
            "動画情報の取得に失敗しました。\n"
            "URL が正しいか、動画が削除されていないかご確認ください。",
        )

    continuation = _find_continuation(yid)
    if not continuation:
        raise ChatNotAvailableError(
            "NO_CONTINUATION",
            "この動画ではチャット（コメント）が取得できませんでした。\n\n"
            "■ アーカイブのライブチャットがオンになっているかご確認ください\n"
            "  YouTube Studio → 該当動画 → 詳細 → 「コメントとチャット」\n"
            "  → 「ライブチャットのリプレイを許可」を ON にする必要があります。\n"
            "\n"
            "その他の考えられる原因:\n"
            "  • チャットリプレイが投稿者により無効化されている\n"
            "  • 通常のアップロード動画でライブチャットが存在しない\n"
            "  • ライブ配信中で、まだチャットリプレイが生成されていない\n"
            "\n"
            "クリップギフトはライブチャットリプレイ前提のツールです。\n"
            "ライブチャット付きアーカイブ動画 URL でお試しください。",
        )
    print("✅ チャット利用可能（continuation 取得 OK）", flush=True)


def download_chat(url, progress_path=None, out_path=None):
    """YouTubeチャットログをcsvとして保存する。

    取得不可の動画（ライブ配信中・リプレイ無効・限定公開等）の場合は
    `ChatNotAvailableError` を発生させる。
    """
    print(f"▶ Fetching chat: {url}")

    html = _fetch_html(url)

    # duration取得（ytInitialPlayerResponseから）
    duration = 0
    dur_m = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', html)
    if dur_m:
        duration = int(dur_m.group(1))
    print(f"📏 動画の長さ: {duration} 秒")

    # 取得不可の動画タイプを早期判別
    is_unavailable, msg = _detect_unavailable_reason(html)
    if is_unavailable:
        raise ChatNotAvailableError("CHAT_NOT_AVAILABLE", msg)

    api_key, version, yid = _extract_params(html)
    if not yid:
        raise ChatNotAvailableError(
            "NO_YT_INITIAL_DATA",
            "動画情報の取得に失敗しました。\n"
            "URL が正しいか、動画が削除されていないかご確認ください。",
        )
    continuation = _find_continuation(yid)
    if not continuation:
        raise ChatNotAvailableError(
            "NO_CONTINUATION",
            "この動画ではチャット（コメント）を取得できませんでした。\n\n"
            "考えられる原因:\n"
            "  • ライブ配信中で、まだチャットリプレイが生成されていない\n"
            "  • チャットリプレイが投稿者により無効化されている\n"
            "  • コメント数が極端に少ない / そもそもコメントなしの動画\n\n"
            "ライブ配信のアーカイブの場合は、配信終了から数時間待って再度お試しください。",
        )

    out = out_path if out_path else "chatlog.csv"
    total = 0
    max_seen_offset = 0
    seen_continuations = set()
    hit_batch_limit = True  # ループを break せず抜けたら上限到達

    # ⚠️ CSV は必ず csv モジュールで書くこと。
    # 以前は f.write(f"{t},{author},{msg}\n") と手書きしていたため、
    # コメントに半角カンマが含まれると列が増えてしまい、読み手
    # （mp4inchatnagasi.py の DictReader / app.py の row[2]）が
    # **カンマ以降を丸ごと失っていた**（"え,まって,今の何" → "え"）。
    # クリップ検出のキーワード判定にも、動画に流すコメント本文にも影響する。
    with open(out, "w", encoding="utf-8", newline="") as f:
        csv_module.writer(f).writerow(["time", "user", "comment"])

    start_time = time.time()
    _BATCH_LIMIT = 3000
    for i in range(_BATCH_LIMIT):
        if continuation in seen_continuations:
            print("🔁 同じ continuation が繰り返されたため終了します。")
            hit_batch_limit = False
            break
        seen_continuations.add(continuation)

        data = _fetch_chat(api_key, version, continuation)
        actions = data.get("actions") or data.get("continuationContents", {}).get(
            "liveChatContinuation", {}
        ).get("actions")
        msgs, latest_offset = _parse_messages(actions)

        if latest_offset > max_seen_offset:
            max_seen_offset = latest_offset

        # ⚠️ 取得したメッセージは「終了判定より先に」必ず書き出すこと。
        # 旧実装は動画長到達の判定を書き込みより前に置いており、
        # **最後のバッチのコメントが丸ごと捨てられていた**。
        # 終盤ほど盛り上がる（＝クリップ対象になる）ので影響が大きい。
        if msgs:
            with open(out, "a", encoding="utf-8", newline="") as f:
                writer = csv_module.writer(f)
                for t, author, msg, offset in msgs:
                    total += 1
                    writer.writerow([t, author, msg])
                f.flush()
                os.fsync(f.fileno())

        if duration > 0 and max_seen_offset / 1000 >= duration:
            print(f"🏁 動画時間（{duration}s）に到達したため終了します。")
            hit_batch_limit = False
            break

        next_c = _extract_next_cont(data)
        if not next_c:
            print("🟢 continuation が無くなったため終了します。")
            hit_batch_limit = False
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

    if hit_batch_limit:
        # 上限に達しても黙って「✅ 完了」と出ていたため、取りこぼしに気付けなかった。
        print(
            f"⚠️ 取得バッチ数が上限（{_BATCH_LIMIT}）に達したため打ち切りました。"
            f"動画終盤のコメントが欠けている可能性があります。",
            flush=True,
        )

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
from pytubefix import request as pytubefix_request

# --- YouTube bot 検出対策: クライアント順次フォールバック ---
# pytubefix 既定クライアント（WEB / ANDROID）は YouTube に bot 判定され
# 「This request was detected as a bot. Use use_po_token=True ...」で失敗することがある。
# use_po_token=True はコンソールで token 貼り付けを対話要求するため GUI アプリでは使えない。
# 代わりに po_token 不要で通りやすいクライアントを順に試し、最初に成功したものを使う。
# ⚠️ ANDROID_VR を先頭から動かさないこと（2026-08-15 実測）。
# pytubefix 10.11 時点で「実際にバイトを返す」のは ANDROID_VR だけ。
#   ANDROID_VR … 実データ取得 OK（ftypdash が返る）。ただし bot 判定されると弾かれる
#   TV/WEB/WEB_SAFARI … yt.streams は成功するが URL が SABR/UMP。素の GET では
#                       `sabr.malformed_config` が 31 byte 返るだけでダウンロード不可
#   MWEB … 実データ取得 OK。ただし 403 を返すこともあり不安定なので ANDROID_VR の次
#   IOS … 400（YouTube が iOS クライアントを実質廃止）
#   WEB_EMBED … VideoUnavailable
# つまり「疎通したクライアント」を採用すると、SABR 組を掴んだ瞬間に
# ダウンロード段階で落ちる（しかもフォールバック済みなので後がない）。
# そのため _create_youtube_with_fallback は streams の有無ではなく
# 「先頭 1 チャンクが実データか」で採否を決める。
DL_CLIENTS = ["ANDROID_VR", "TV", "MWEB", "WEB", "WEB_SAFARI", "IOS", "WEB_EMBED"]


def _stream_is_downloadable(stream):
    """そのストリーム URL が本当に実データを返すかを 1KB だけ取って確かめる。

    yt.streams が成功しても落とせないことがある。YouTube は一部クライアント
    （TV / WEB / WEB_SAFARI）に SABR/UMP 形式の URL を返すようになっており、
    素の GET では Content-Type: application/vnd.yt-ump で
    `sabr.malformed_config` が 31 byte 返るだけで、動画は 1 byte も取れない。

    これを見ないと「疎通 OK」で採用 → download() 段階で TypeError、
    しかもフォールバックを抜けた後なので後がない、という最悪の形で落ちる。
    """
    # ⚠️ 必ず pytubefix の request 経路で試すこと。
    # requests で自前 UA を付けて叩くと**クライアント別ヘッダが再現されず**、
    # 実際には 403 で落ちる MWEB が「取得できる」と誤判定される。
    # default_range_size は既定 9MB。判定には要らないので一時的に縮めて捨て転送を減らす。
    original_range = pytubefix_request.default_range_size
    pytubefix_request.default_range_size = 65536
    try:
        chunk = next(pytubefix_request.stream(stream.url), b"")
        if len(chunk) < 512:
            return False, f"応答が短すぎる（{len(chunk)} byte）"
        if chunk.lstrip()[:1] == b"," or b"sabr." in chunk[:64]:
            return False, "SABR/UMP 応答"
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        pytubefix_request.default_range_size = original_range


def _create_youtube_with_fallback(url, on_progress_callback=None):
    """bot 検出を回避するため、複数クライアントを順に試して YouTube オブジェクトを生成する。

    採否は「streams が引けたか」ではなく「実データが返るか」で決める。
    詳細は _stream_is_downloadable と DL_CLIENTS のコメントを参照。
    """
    last_err = None
    for i, client in enumerate(DL_CLIENTS):
        try:
            yt = YouTube(url, client=client, on_progress_callback=on_progress_callback)
            # bot 検出は streams / player 取得時に発火するので、まずここで疎通確認
            probe_stream = yt.streams.filter(type="video").first()
            if probe_stream is None:
                raise RuntimeError("動画ストリームが 1 本も無い")
            # 疎通しただけでは足りない。実際に落とせるかまで見る
            ok, reason = _stream_is_downloadable(probe_stream)
            if not ok:
                raise RuntimeError(f"ストリームを取得できない形式です（{reason}）")
            print(f"[INFO] クライアント {client} で接続成功", flush=True)
            return yt
        except Exception as e:
            last_err = e
            print(f"[WARN] クライアント {client} 失敗（{type(e).__name__}: {e}）、次を試行 {i+1}/{len(DL_CLIENTS)}", flush=True)
    raise RuntimeError(f"全クライアントで動画取得に失敗しました（最後のエラー: {last_err}）")

# ffmpeg / ffprobe のパス（system_utils で一本化）
ffmpeg_path = get_ffmpeg_path()
_BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ffprobe_path = get_ffprobe_path()
audiowaveform_path = os.path.join(_BASE_DIR, "bin", "audiowaveform.exe")

# 標準出力・標準エラーを UTF-8 に。
# stderr が抜けていたのが 2026-08-15 のエラー報告が読めなかった原因。
# app.py は Popen(..., stderr=STDOUT, encoding="utf-8", errors="backslashreplace")
# で読むので、stderr が cp932 のままだと**トレースバックだけ**が
# `\x91S\x83N\x83\x89...` に化けて、肝心の例外メッセージが判読不能になる。
#
# None ガード付き: pythonw 経由（Tauri ランチャー）で起動されると
# sys.stdout / sys.stderr が None になり得る。twitch_chat.py はこのモジュールを
# lazy import するため、無防備だと import 時点で AttributeError で落ちる。
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None:
        _stream.reconfigure(encoding="utf-8", errors="replace")


def safe_write_json(path, data):
    """progress.json をアトミックに書く。

    失敗しても一時ファイルを残さない（旧実装は delete=False の NamedTemporaryFile を
    作った後で例外が出ると tmpXXXX が書き込み先ディレクトリに残り続けていた。
    出力先は Downloads/<タイトル>/ 配下なのでユーザーの目に触れるゴミになる）。
    """
    dir_name = os.path.dirname(path) or "."
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=dir_name, delete=False
        ) as tmp:
            tmp_name = tmp.name
            json.dump(data, tmp, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
        shutil.move(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass




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


def _prepare_title_folder(output_folder, title):
    """出力フォルダを決めて作る。pytubefix / yt-dlp 両経路から使う。

    ⚠️ 旧実装は既存フォルダを問答無用で shutil.rmtree していた。
    title は「タイトル先頭 30 文字」なので、シリーズ物のように前半が同じ配信だと
    別動画でも同じフォルダ名になり、**前回 DL した動画・チャット・波形が消えていた**
    （まだクリップにしていない素材の消失＝復旧不能）。
    完成品（{title}.mp4）が残っている場合は消さずに連番フォルダへ退避する。
    中断された残骸（video_temp.mp4 だけ等）なら従来どおり作り直す。
    """
    title_folder = os.path.join(output_folder, title)
    if os.path.exists(title_folder):
        if os.path.exists(os.path.join(title_folder, f"{title}.mp4")):
            base_folder = title_folder
            counter = 1
            while os.path.exists(title_folder):
                title_folder = f"{base_folder}({counter})"
                counter += 1
            print(
                f"[INFO] 既存のダウンロード済みフォルダを保護し、別フォルダに保存します: {title_folder}",
                flush=True,
            )
        else:
            shutil.rmtree(title_folder)
            print(f"[INFO] 未完了の残骸を削除して再ダウンロード: {title_folder}", flush=True)
    os.makedirs(title_folder, exist_ok=True)
    return title_folder


def download_with_ytdlp(url, output_folder, max_resolution=720, progress_path=None):
    """yt-dlp による代替ダウンロード。pytubefix が全滅したときの保険。

    pytubefix は YouTube 側の仕様変更（bot 判定 / SABR 化 / player JS 変更）で
    定期的に全クライアント落ちする。2026-08-15 のエラー報告がまさにそれで、
    ユーザーは 7 月にも同じ件を報告している＝再発している。
    yt-dlp は同じ変更への追従が早く、SABR も po_token も自前で処理するため、
    pytubefix が死んでいる期間の穴埋めになる。

    戻り値は download_with_pytubefix と同じ (title_folder, safe_title)。
    """
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError(
            "yt-dlp が入っていないため代替ダウンロードを実行できません。"
            "`pip install yt-dlp` で導入してください。"
        )

    print("[INFO] yt-dlp で代替ダウンロードを開始...", flush=True)
    if progress_path:
        safe_write_json(progress_path, {
            "progress": 0,
            "message": "代替エンジン（yt-dlp）でダウンロード中",
            "phase": "動画ダウンロード",
        })

    # メタデータだけ先に取り、pytubefix 経路と同じ規則でフォルダ名を決める
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)

    title = sanitize_filename(info.get("title") or "video")[:30]
    print(f"[INFO] タイトル: {info.get('title')}", flush=True)
    print(f"[INFO] 長さ: {info.get('duration')}秒", flush=True)

    title_folder = _prepare_title_folder(output_folder, title)
    output_file = os.path.join(title_folder, f"{title}.mp4")

    def _hook(d):
        # 動画 DL は全体の 0〜45%（pytubefix 経路の 動画0-30 + 音声30-45 と揃える）
        if d.get("status") != "downloading" or not progress_path:
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        done = d.get("downloaded_bytes") or 0
        if total <= 0:
            return
        local_pct = min(done / total, 1.0)
        print(f"[PROGRESS] 動画ダウンロード {int(local_pct * 100)}%", flush=True)
        safe_write_json(progress_path, {
            "progress": int(local_pct * 45),
            "message": f"動画ダウンロード {int(local_pct * 100)}%",
            "phase": "動画ダウンロード",
        })

    opts = {
        # ⚠️ H.264(avc1) + AAC(mp4a) を最優先する。
        # 無指定だと yt-dlp は AV1 + Opus を選ぶことがあり、pytubefix 経路が返す
        # avc1 + mp4a と中身が変わってしまう。以降のクリップ生成・波形生成は
        # 後者を前提にした ffmpeg パイプラインなので、経路によって成否が変わるのは避ける。
        # 取れない場合だけ codec 指定を外して段階的に緩める。
        "format": (
            f"bestvideo[height<={max_resolution}][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
            f"bestvideo[height<={max_resolution}]+bestaudio/"
            f"best[height<={max_resolution}]/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(title_folder, f"{title}.%(ext)s"),
        "ffmpeg_location": ffmpeg_path,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [_hook],
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    if not os.path.exists(output_file):
        # merge_output_format が効かず別拡張子で落ちた場合を拾う
        for name in os.listdir(title_folder):
            if name.startswith(title) and name.lower().endswith((".mp4", ".mkv", ".webm")):
                found = os.path.join(title_folder, name)
                if found != output_file:
                    shutil.move(found, output_file)
                break

    if not os.path.exists(output_file):
        raise RuntimeError("yt-dlp のダウンロード結果が見つかりませんでした")

    print(f"[INFO] yt-dlp ダウンロード完了: {output_file}", flush=True)
    return title_folder, title


def download_with_pytubefix(url, output_folder, max_resolution=720, progress_path=None):
    """
    pytubefixを使用して動画をダウンロード
    """
    print(f"[INFO] pytubefix でダウンロード開始...", flush=True)

    # 動画DL用コールバック（0〜30%）
    video_cb = make_progress_callback(progress_path, "動画ダウンロード", 0, 30) if progress_path else None
    # bot 検出対策: 複数クライアントを順に試して接続（単一 client 固定だと弾かれることがある）
    yt = _create_youtube_with_fallback(url, on_progress_callback=video_cb)

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
            actual_resolution = best_stream.resolution or "不明"
            fallback_message = (
                f"⚠️ {max_resolution}p 以下の画質が見つかりませんでした。"
                f"最高画質 {actual_resolution} で DL を続行します"
            )
            print(f"[INFO] {fallback_message}", flush=True)
            if progress_path:
                safe_write_json(progress_path, {
                    "progress": 0,
                    "message": fallback_message,
                    "phase": "画質フォールバック",
                })
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

    title_folder = _prepare_title_folder(output_folder, title)

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
    # timeout 必須: ffmpeg がハングすると DL スレッドが永久に戻らず、
    # UI は「ダウンロード中」のまま固まってキャンセルするしか無くなる。
    # -c copy なので長尺でも通常は数十秒。余裕を見て 1 時間。
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=FFMPEG_MERGE_TIMEOUT_SEC,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise Exception(
            f"ffmpeg の結合が {FFMPEG_MERGE_TIMEOUT_SEC} 秒でタイムアウトしました"
        )

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


def download_video_and_chat(url, base_output_folder, progress_path, max_resolution=1080):
    output_folder = os.path.abspath(base_output_folder)
    os.makedirs(output_folder, exist_ok=True)

    # 動画 DL 前にチャットが取れるかチェック（重い DL を始める前に判定）。
    # 取れない（アーカイブのライブチャット OFF 等）の場合は ChatNotAvailableError →
    # main() の except で exit(2) + 親切メッセージ表示。
    safe_write_json(progress_path, {"progress": 0, "message": "チャット可否を確認中...", "phase": "事前チェック"})
    check_chat_available(url)

    safe_write_json(progress_path, {"progress": 0, "message": "動画ダウンロード開始", "phase": "動画ダウンロード"})

    # pytubefixでダウンロード（動画0〜30%、音声30〜45%）
    # 全クライアントが落ちたら yt-dlp に切り替える。
    # pytubefix は YouTube 側の変更で定期的に全滅するので、ここで諦めると
    # 「アプリが丸ごと使えない」状態になる（2026-07 / 2026-08 に同一ユーザーが報告）。
    try:
        title_folder, safe_title = download_with_pytubefix(
            url, output_folder, max_resolution=max_resolution, progress_path=progress_path
        )
    except Exception as e:
        print(f"[WARN] pytubefix でのダウンロードに失敗: {type(e).__name__}: {e}", flush=True)
        print("[INFO] yt-dlp に切り替えて再試行します", flush=True)
        safe_write_json(progress_path, {
            "progress": 0,
            "message": "代替エンジンに切り替えて再試行中...",
            "phase": "動画ダウンロード",
        })
        try:
            title_folder, safe_title = download_with_ytdlp(
                url, output_folder, max_resolution=max_resolution, progress_path=progress_path
            )
        except Exception as e2:
            # 両方失敗。どちらの理由も残さないと切り分けができない
            raise RuntimeError(
                f"動画のダウンロードに失敗しました。"
                f"pytubefix: {type(e).__name__}: {e} / "
                f"yt-dlp: {type(e2).__name__}: {e2}"
            ) from e2

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
    except ChatNotAvailableError:
        # チャット取得不可は main() でユーザー向けメッセージ表示するために再 raise
        raise
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
        subprocess.run(
            cmd_wav, check=True, timeout=WAVEFORM_TIMEOUT_SEC,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

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
            cmd_probe, timeout=WAVEFORM_TIMEOUT_SEC,
            creationflags=subprocess.CREATE_NO_WINDOW,
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
        result = subprocess.run(
            cmd_json, check=True, capture_output=True, timeout=WAVEFORM_TIMEOUT_SEC,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
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
    # 4 番目の max_resolution は省略可（後方互換）。指定がなければ 1080。
    if len(sys.argv) not in (4, 5):
        print(
            "Usage: python downloader.py <YouTube_or_Twitch_URL> <output_folder> <progress_path> [max_resolution]"
        )
        sys.exit(1)

    video_url = sys.argv[1]
    base_output_folder = sys.argv[2]
    progress_path = sys.argv[3]
    try:
        max_resolution = int(sys.argv[4]) if len(sys.argv) >= 5 else 1080
    except (TypeError, ValueError):
        max_resolution = 1080

    os.makedirs(base_output_folder, exist_ok=True)

    safe_write_json(progress_path, {"progress": 0, "message": "開始"})

    try:
        # URL 判別: Twitch なら専用ダウンローダーへ
        from twitch_chat import is_twitch_url
        if is_twitch_url(video_url):
            from downloader_twitch import download_video_and_chat_twitch
            print(f"[INFO] Twitch URL として処理: {video_url}", flush=True)
            print(f"[INFO] 画質指定: max_resolution={max_resolution}p", flush=True)
            # max_resolution を渡していなかったため、UI の画質選択が Twitch では
            # 完全に無視され、常に 1080p で DL されていた
            download_video_and_chat_twitch(
                video_url, base_output_folder, progress_path,
                max_resolution=max_resolution,
            )
        else:
            # YouTube として扱う前に簡易判定
            if "youtube.com" not in video_url and "youtu.be" not in video_url:
                print(f"[WARN] YouTube / Twitch のどちらにも該当しない URL: {video_url}", flush=True)
                print(f"[WARN] YouTube ロジックで処理を試みますが、失敗する可能性があります", flush=True)
            print(f"[INFO] YouTube URL として処理: {video_url}", flush=True)
            print(f"[INFO] 画質指定: max_resolution={max_resolution}p", flush=True)
            download_video_and_chat(
                video_url, base_output_folder, progress_path,
                max_resolution=max_resolution,
            )
    except ChatNotAvailableError as e:
        # チャット取得不可（ライブ配信中・リプレイ無効・限定公開など）→ exit code 2 で終了
        # フロントは progress.json の message をそのままユーザーに表示する
        safe_write_json(progress_path, {"progress": -1, "message": e.user_message})
        print(f"[CHAT_NOT_AVAILABLE] {e}", flush=True)
        print(e.user_message, flush=True)
        sys.exit(2)
    except (RuntimeError, ValueError) as e:
        # Twitch 系のエラーを親切メッセージに変換
        msg = str(e)
        user_message = None
        if "VOD アクセストークン取得失敗" in msg or "削除済み" in msg:
            user_message = (
                "Twitch VOD の取得に失敗しました。\n\n"
                "考えられる原因:\n"
                "  • VOD が削除されている\n"
                "  • 限定公開 / 加入者（サブスクライバー）限定の VOD\n"
                "  • チャンネル所有者により非公開設定にされた\n\n"
                "別の通常公開の VOD URL でお試しください。"
            )
        elif "Twitch VOD URL ではありません" in msg:
            user_message = (
                "URL の形式が正しくありません。\n\n"
                "Twitch VOD は次のような URL 形式です:\n"
                "  https://www.twitch.tv/videos/123456789\n\n"
                "ライブ配信中の URL（twitch.tv/{channel}）は対象外です。"
            )
        elif "セグメント" in msg or "playlist" in msg.lower() or "M3U8" in msg:
            user_message = (
                "Twitch VOD の動画ダウンロードに失敗しました。\n\n"
                "考えられる原因:\n"
                "  • Twitch 側の一時的な障害\n"
                "  • ネットワーク接続の不安定\n"
                "  • VOD の動画データが部分的に欠損\n\n"
                "数分後に再度お試しください。"
            )
        if user_message:
            safe_write_json(progress_path, {"progress": -1, "message": user_message})
            print(f"[TWITCH_ERROR] {msg}", flush=True)
            print(user_message, flush=True)
            sys.exit(2)
        # 該当しないエラーは下の Exception へフォールバック
        raise
    except Exception as e:
        safe_write_json(progress_path, {"progress": -1, "message": f"エラー: {e}"})
        print("エラー:", e, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
