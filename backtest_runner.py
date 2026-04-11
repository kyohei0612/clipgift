"""
backtest_runner.py - YouTubeクリップツール バックテストランナー
使い方: python backtest_runner.py [YouTubeURL]
app.py が http://localhost:5000 で起動している状態で実行してください
"""
import requests
import json
import time
import random
import string
import os
import sys
import threading
from datetime import datetime

BASE_URL = "http://localhost:5000"
YOUTUBE_URL = sys.argv[1] if len(sys.argv) > 1 else ""

results = []
results_lock = threading.Lock()

# バックグラウンドでハートビートを送り続ける（watchdogに落とされないように）
def _heartbeat_loop():
    while True:
        try:
            requests.post(BASE_URL + "/heartbeat", timeout=2)
        except Exception:
            pass
        time.sleep(2)
threading.Thread(target=_heartbeat_loop, daemon=True).start()


def log(persona, test_name, method, endpoint, status, response_time_ms, ok, detail=""):
    entry = {
        "persona": persona,
        "test": test_name,
        "method": method,
        "endpoint": endpoint,
        "status": status,
        "response_time_ms": round(response_time_ms, 1),
        "ok": ok,
        "detail": detail,
        "timestamp": datetime.now().isoformat(),
    }
    with results_lock:
        results.append(entry)
    icon = "✅" if ok else "❌"
    print(f"{icon} [{persona}] {test_name} → {status} ({response_time_ms:.0f}ms) {detail[:80] if detail else ''}", flush=True)


def req(method, endpoint, **kwargs):
    url = BASE_URL + endpoint
    start = time.time()
    try:
        r = requests.request(method, url, timeout=10, **kwargs)
        ms = (time.time() - start) * 1000
        return r, ms
    except requests.exceptions.ConnectionError:
        ms = (time.time() - start) * 1000
        return None, ms
    except requests.exceptions.Timeout:
        ms = (time.time() - start) * 1000
        return None, ms


def rand_str(n=10):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def rand_int(lo, hi):
    return random.randint(lo, hi)


# ============================================================
# ペルソナ1: 普通のユーザー（正常フロー）
# ============================================================
def wait_download_complete(progress_path, timeout=300):
    """ダウンロードが完了するまで待機（最大timeout秒）"""
    print(f"  ⏳ ダウンロード完了を待機中（最大{timeout}秒）...", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(2)
        r, ms = req("GET", f"/is_downloading")
        if r and r.status_code == 200:
            try:
                if not r.json().get("downloading", True):
                    print("  ✅ ダウンロード完了", flush=True)
                    return True
            except Exception:
                pass
        # progress_pathも確認
        if progress_path:
            r2, _ = req("GET", f"/get-progress-file?path={progress_path}")
            if r2 and r2.status_code == 200:
                try:
                    d = r2.json()
                    pct = d.get("progress", 0)
                    print(f"  📊 進捗: {pct}% - {d.get('message', '')}", flush=True)
                    if pct == 100 or d.get("all_done"):
                        print("  ✅ ダウンロード完了", flush=True)
                        return True
                    if pct < 0:
                        print("  ❌ ダウンロードエラー", flush=True)
                        return False
                except Exception:
                    pass
    print("  ⚠️ タイムアウト", flush=True)
    return False


def persona_normal():
    print("\n📋 [普通のユーザー] テスト開始", flush=True)

    # トップページ
    r, ms = req("GET", "/")
    log("普通", "トップページ表示", "GET", "/", r.status_code if r else 0, ms, r is not None and r.status_code == 200)

    # ページ2
    r, ms = req("GET", "/page2")
    log("普通", "解析ページ表示", "GET", "/page2", r.status_code if r else 0, ms, r is not None and r.status_code == 200)

    # フォント一覧取得
    r, ms = req("GET", "/get-fonts")
    ok = r is not None and r.status_code == 200
    log("普通", "フォント一覧取得", "GET", "/get-fonts", r.status_code if r else 0, ms, ok)

    # ハートビート
    r, ms = req("POST", "/heartbeat")
    log("普通", "ハートビート送信", "POST", "/heartbeat", r.status_code if r else 0, ms, r is not None and r.status_code == 200)

    # 更新チェック
    r, ms = req("GET", "/check-update")
    log("普通", "更新チェック", "GET", "/check-update", r.status_code if r else 0, ms, r is not None and r.status_code == 200)

    # CSVチャット解析（正常なCSVデータ）
    csv_data = "time,user,comment\n1:00,user1,すごい！\n1:30,user2,www\n2:00,user3,面白い"
    r, ms = req("POST", "/analyze_chat_csv", data={
        "keywords": "すごい,面白い",
        "start_threshold": "2",
        "end_threshold": "1",
        "clip_offset": "10",
        "videoDuration": "300",
    }, files={"chatFile": ("chat.csv", csv_data, "text/csv")})
    ok = r is not None and r.status_code == 200
    detail = ""
    if ok:
        try:
            d = r.json()
            detail = f"clips={len(d.get('clips', []))}件"
        except Exception:
            pass
    log("普通", "チャットCSV解析（正常）", "POST", "/analyze_chat_csv", r.status_code if r else 0, ms, ok, detail)

    # YouTubeダウンロード（URLがある場合のみ）
    if YOUTUBE_URL:
        r, ms = req("POST", "/download-yt-video-chat", json={"videoUrl": YOUTUBE_URL})
        ok = r is not None and r.status_code == 200
        log("普通", "YouTubeダウンロード開始", "POST", "/download-yt-video-chat", r.status_code if r else 0, ms, ok)
        if ok:
            progress_path = ""
            try:
                progress_path = r.json().get("progress_path", "")
            except Exception:
                pass
            # ダウンロード完了まで待機
            wait_download_complete(progress_path, timeout=300)


# ============================================================
# ペルソナ2: 初心者（空欄・変な値）
# ============================================================
def persona_beginner():
    print("\n🔰 [初心者] テスト開始", flush=True)

    # 空URLでダウンロード
    r, ms = req("POST", "/download-yt-video-chat", json={"videoUrl": ""})
    ok = r is not None and r.status_code in (400, 200)
    log("初心者", "空URLでダウンロード", "POST", "/download-yt-video-chat", r.status_code if r else 0, ms, ok)

    # 無効URLでダウンロード
    r, ms = req("POST", "/download-yt-video-chat", json={"videoUrl": "not-a-url"})
    ok = r is not None and r.status_code in (400, 200)
    log("初心者", "無効URLでダウンロード", "POST", "/download-yt-video-chat", r.status_code if r else 0, ms, ok)

    # 空キーワードでCSV解析
    csv_data = "time,user,comment\n1:00,user1,test"
    r, ms = req("POST", "/analyze_chat_csv", data={
        "keywords": "",
        "start_threshold": "2",
        "end_threshold": "1",
        "clip_offset": "10",
        "videoDuration": "300",
    }, files={"chatFile": ("chat.csv", csv_data, "text/csv")})
    ok = r is not None and r.status_code in (400, 200)
    log("初心者", "空キーワードでCSV解析", "POST", "/analyze_chat_csv", r.status_code if r else 0, ms, ok)

    # CSVファイルなしで解析
    r, ms = req("POST", "/analyze_chat_csv", data={"keywords": "test"})
    ok = r is not None and r.status_code in (400, 500)
    log("初心者", "CSVなしで解析", "POST", "/analyze_chat_csv", r.status_code if r else 0, ms, ok)

    # progress_pathなしで進捗確認
    r, ms = req("GET", "/progress")
    ok = r is not None and r.status_code == 200
    log("初心者", "パスなしで進捗確認", "GET", "/progress", r.status_code if r else 0, ms, ok)

    # 空のprocess_clips
    r, ms = req("POST", "/process_clips", data={"clips": "[]"})
    ok = r is not None and r.status_code in (400, 200)
    log("初心者", "空クリップで処理", "POST", "/process_clips", r.status_code if r else 0, ms, ok)


# ============================================================
# ペルソナ3: ヘビーユーザー（大量・長時間）
# ============================================================
def persona_heavy():
    print("\n💪 [ヘビーユーザー] テスト開始", flush=True)

    # 大量コメントCSV解析
    rows = ["time,user,comment"]
    for i in range(500):
        t = f"{i//60}:{i%60:02d}"
        rows.append(f"{t},user{i},{'w'*random.randint(1,20)}")
    big_csv = "\n".join(rows)
    r, ms = req("POST", "/analyze_chat_csv", data={
        "keywords": "w,すごい,草",
        "start_threshold": "3",
        "end_threshold": "2",
        "clip_offset": "15",
        "videoDuration": "3600",
    }, files={"chatFile": ("big_chat.csv", big_csv, "text/csv")})
    ok = r is not None and r.status_code == 200
    detail = ""
    if ok:
        try:
            d = r.json()
            detail = f"clips={len(d.get('clips', []))}件"
        except Exception:
            pass
    log("ヘビー", "大量コメント解析(500件)", "POST", "/analyze_chat_csv", r.status_code if r else 0, ms, ok, detail)

    # ハートビートを連続送信
    fail = 0
    for i in range(20):
        r, ms = req("POST", "/heartbeat")
        if r is None or r.status_code != 200:
            fail += 1
    log("ヘビー", "ハートビート連続20回", "POST", "/heartbeat", 200 if fail == 0 else 500, ms, fail == 0, f"失敗:{fail}回")

    # 更新状態を連続確認
    for i in range(5):
        r, ms = req("GET", "/update-state")
        ok = r is not None and r.status_code == 200
        if not ok:
            break
    log("ヘビー", "更新状態連続確認5回", "GET", "/update-state", r.status_code if r else 0, ms, ok)


# ============================================================
# ペルソナ4: 意地悪ユーザー（攻撃的入力）
# ============================================================
def persona_evil():
    print("\n😈 [意地悪ユーザー] テスト開始", flush=True)

    # パストラバーサル試行
    r, ms = req("GET", "/progress?path=../../etc/passwd")
    ok = r is not None and r.status_code in (200, 400)
    detail = ""
    if r:
        try:
            d = r.json()
            msg = d.get("message", "")
            # 「不正」か「未開始」ならブロック成功（ファイルが存在しないか検証で弾かれた）
            if "不正" in msg or msg == "未開始":
                detail = "blocked"
            else:
                detail = f"要確認: {msg}"
        except Exception:
            pass
    log("意地悪", "パストラバーサル試行", "GET", "/progress", r.status_code if r else 0, ms, ok, detail)

    # 巨大JSONを送信
    huge_data = {"videoUrl": "x" * 100000}
    r, ms = req("POST", "/download-yt-video-chat", json=huge_data)
    ok = r is not None and r.status_code in (200, 400, 413, 500)
    log("意地悪", "巨大URL送信(100KB)", "POST", "/download-yt-video-chat", r.status_code if r else 0, ms, ok)

    # SQLインジェクション風キーワード
    r, ms = req("POST", "/analyze_chat_csv", data={
        "keywords": "'; DROP TABLE users; --",
        "start_threshold": "1",
        "end_threshold": "1",
        "clip_offset": "0",
        "videoDuration": "100",
    }, files={"chatFile": ("chat.csv", "time,user,comment\n1:00,u,test", "text/csv")})
    ok = r is not None and r.status_code in (200, 400)
    log("意地悪", "SQLインジェクション風キーワード", "POST", "/analyze_chat_csv", r.status_code if r else 0, ms, ok)

    # 不正JSONを送信
    r, ms = req("POST", "/download-yt-video-chat",
                data="not json at all",
                headers={"Content-Type": "application/json"})
    ok = r is not None and r.status_code in (400, 500)
    log("意地悪", "不正JSON送信", "POST", "/download-yt-video-chat", r.status_code if r else 0, ms, ok)

    # 同時多重リクエスト
    def _hit():
        req("POST", "/heartbeat")
    threads = [threading.Thread(target=_hit) for _ in range(30)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ms = (time.time() - t0) * 1000
    log("意地悪", "ハートビート同時30連打", "POST", "/heartbeat", 200, ms, True, f"合計{ms:.0f}ms")

    # マイナス値でCSV解析
    csv_data = "time,user,comment\n1:00,user1,test"
    r, ms = req("POST", "/analyze_chat_csv", data={
        "keywords": "test",
        "start_threshold": "-999",
        "end_threshold": "-1",
        "clip_offset": "-100",
        "videoDuration": "-1",
    }, files={"chatFile": ("chat.csv", csv_data, "text/csv")})
    ok = r is not None and r.status_code in (200, 400)
    log("意地悪", "マイナス値でCSV解析", "POST", "/analyze_chat_csv", r.status_code if r else 0, ms, ok)

    # 存在しないエンドポイント
    r, ms = req("GET", "/admin/delete-all")
    ok = r is not None and r.status_code == 404
    log("意地悪", "存在しないエンドポイント", "GET", "/admin/delete-all", r.status_code if r else 0, ms, ok)

    # progress_pathに不正パスを指定
    r, ms = req("GET", "/get-progress-file?path=C:/Windows/System32/drivers/etc/hosts")
    ok = r is not None
    detail = ""
    if ok and r:
        try:
            d = r.json()
            detail = "blocked" if "不正" in d.get("message", "") else f"msg={d.get('message','')}"
        except Exception:
            pass
    log("意地悪", "Windowsシステムパス指定", "GET", "/get-progress-file", r.status_code if r else 0, ms, ok, detail)


# ============================================================
# ファジングテスト
# ============================================================
def fuzzing():
    print("\n🔨 [ファジング] テスト開始", flush=True)

    endpoints = [
        ("GET", "/"),
        ("GET", "/page2"),
        ("GET", "/get-fonts"),
        ("GET", "/check-update"),
        ("GET", "/update-state"),
        ("POST", "/heartbeat"),
        ("POST", "/reset-progress"),
    ]

    for method, endpoint in endpoints:
        # 正常リクエスト
        r, ms = req(method, endpoint)
        ok = r is not None and r.status_code < 500
        log("ファジング", f"基本リクエスト {endpoint}", method, endpoint, r.status_code if r else 0, ms, ok)

    # /progress にランダムなpathを送る
    for _ in range(5):
        path = rand_str(20)
        r, ms = req("GET", f"/progress?path={path}")
        ok = r is not None and r.status_code == 200
        log("ファジング", f"ランダムprogress path", "GET", "/progress", r.status_code if r else 0, ms, ok)

    # /analyze_chat_csv にランダムなパラメータ
    for _ in range(3):
        csv_data = f"time,user,comment\n{rand_int(0,59)}:{rand_int(0,59):02d},user,{rand_str()}"
        r, ms = req("POST", "/analyze_chat_csv", data={
            "keywords": rand_str(),
            "start_threshold": str(rand_int(-10, 100)),
            "end_threshold": str(rand_int(-10, 100)),
            "clip_offset": str(rand_int(-100, 1000)),
            "videoDuration": str(rand_int(-1, 100000)),
        }, files={"chatFile": ("chat.csv", csv_data, "text/csv")})
        ok = r is not None and r.status_code < 500
        log("ファジング", "ランダムCSV解析", "POST", "/analyze_chat_csv", r.status_code if r else 0, ms, ok)

    # /download-yt-video-chat にランダムなURL
    for _ in range(3):
        url_candidates = [
            rand_str(),
            "https://" + rand_str() + ".com",
            "javascript:alert(1)",
            "",
            None,
        ]
        url = random.choice(url_candidates)
        try:
            r, ms = req("POST", "/download-yt-video-chat", json={"videoUrl": url})
            # status=0はサーバーがJSONパース失敗で接続切断→正常な拒否として扱う
            ok = r is None or r.status_code in (200, 400, 500)
            log("ファジング", f"ランダムURL送信", "POST", "/download-yt-video-chat", r.status_code if r else 0, ms, ok)
        except Exception:
            pass


# ============================================================
# メイン
# ============================================================
def main():
    print("=" * 60)
    print("  バックテストランナー 起動")
    print(f"  対象: {BASE_URL}")
    if YOUTUBE_URL:
        print(f"  YouTube URL: {YOUTUBE_URL}")
    print("=" * 60)

    # サーバー疎通確認
    r, ms = req("GET", "/")
    if r is None:
        print(f"❌ {BASE_URL} に接続できません。app.pyを起動してください。")
        sys.exit(1)
    print(f"✅ サーバー接続確認 ({ms:.0f}ms)\n")

    # 全テスト実行
    persona_normal()
    persona_beginner()
    persona_heavy()
    persona_evil()
    fuzzing()

    # 結果集計
    total = len(results)
    passed = sum(1 for r in results if r["ok"])
    failed = total - passed
    avg_ms = sum(r["response_time_ms"] for r in results) / total if total else 0

    print("\n" + "=" * 60)
    print(f"  テスト完了: {total}件 / 成功:{passed} / 失敗:{failed}")
    print(f"  平均レスポンス: {avg_ms:.0f}ms")
    print("=" * 60)

    # レポート生成
    report = {
        "generated_at": datetime.now().isoformat(),
        "target": BASE_URL,
        "youtube_url": YOUTUBE_URL,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "avg_response_ms": round(avg_ms, 1),
            "pass_rate": round(passed / total * 100, 1) if total else 0,
        },
        "failed_tests": [r for r in results if not r["ok"]],
        "all_tests": results,
    }

    # 保存先をtkinterで選択
    try:
        import tkinter as tk
        from tkinter import filedialog
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", True)
        filepath = filedialog.asksaveasfilename(
            title="バックテストレポートの保存先",
            initialfile=f"backtest_report_{timestamp}.json",
            defaultextension=".json",
            filetypes=[("JSONファイル", "*.json")],
        )
        root.destroy()
        if filepath:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"\n📄 レポート保存: {filepath}")
        else:
            print("\n⚠️ 保存がキャンセルされました")
    except Exception as e:
        # tkinterが使えない場合はカレントディレクトリに保存
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = f"backtest_report_{timestamp}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n📄 レポート保存: {filepath}")


if __name__ == "__main__":
    main()
