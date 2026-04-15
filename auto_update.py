"""
auto_update.py - GitHub自動更新モジュール
app.pyからimportして使う
"""
import os
import json
import shutil
import threading
import time
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
LOCAL_VERSION_FILE = os.path.join(BASE_DIR, "version.json")
# 更新進行中マーカー。存在する状態で起動した場合、前回の更新が中断したと判断してロールバックする。
UPDATE_MARKER_FILE = os.path.join(BASE_DIR, ".update_in_progress")

# ここを自分のリポジトリに書き換える
GITHUB_OWNER = "kyohei0612"
GITHUB_REPO = "clipgift"
GITHUB_BRANCH = "main"

# 更新から除外するファイル・フォルダ
EXCLUDE_FILES = {
    "bin/ffmpeg.exe",
    "bin/ffprobe.exe",
    "bin/audiowaveform.exe",
    "bin/python_path.txt",
    "bin/last_font.json",
    "server_start_count.txt",
    "version.json",
}

# 更新状態をメモリで管理
_update_state = {
    "status": "idle",   # idle / checking / updating / done / error
    "message": "",
}
_update_lock = threading.Lock()


def _github_raw_url(filepath):
    return (
        f"https://raw.githubusercontent.com/"
        f"{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{filepath}"
    )


def _github_api_url(filepath):
    return (
        f"https://api.github.com/repos/"
        f"{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filepath}?ref={GITHUB_BRANCH}"
    )


def _get_all_files(path=""):
    """GitHubリポジトリのファイル一覧を再帰取得"""
    url = _github_api_url(path)
    data = json.loads(_fetch_url(url).decode("utf-8"))
    files = []
    for item in data:
        if item["type"] == "file":
            files.append(item["path"])
        elif item["type"] == "dir":
            files.extend(_get_all_files(item["path"]))
    return files


def _fetch_url(url):
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}t={int(time.time())}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "youtube-clip-tool-updater")
    req.add_header("Cache-Control", "no-cache")
    req.add_header("Pragma", "no-cache")
    with urllib.request.urlopen(req, timeout=10) as res:
        return res.read()


def get_remote_version():
    """GitHubのversion.jsonを取得（キャッシュ無効化）"""
    url = _github_raw_url("version.json") + f"?t={int(time.time())}"
    data = _fetch_url(url)
    return json.loads(data.decode("utf-8"))


def get_local_version():
    """ローカルのversion.jsonを読む。なければ{"version":"0.0.0"}"""
    try:
        with open(LOCAL_VERSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": "0.0.0"}


def _version_tuple(v):
    return tuple(int(x) for x in v.split("."))


def check_update():
    """
    更新チェック。
    戻り値: {"has_update": bool, "remote_version": str, "files": [...]}
    """
    try:
        remote = get_remote_version()
        local = get_local_version()
        remote_ver = remote.get("version", "0.0.0")
        local_ver = local.get("version", "0.0.0")

        has_update = _version_tuple(remote_ver) > _version_tuple(local_ver)
        return {
            "has_update": has_update,
            "remote_version": remote_ver,
            "local_version": local_ver,
            "files": remote.get("files", []) if has_update else [],
        }
    except Exception as e:
        return {"has_update": False, "error": str(e)}


def _download_file(filepath):
    """GitHubからファイルをダウンロードしてローカルに上書き"""
    url = _github_raw_url(filepath)
    data = _fetch_url(url)

    # 空ファイルは異常とみなしてスキップ
    if not data:
        raise ValueError(f"ダウンロードしたファイルが空です: {filepath}")

    # .pyファイルの場合、Pythonとして構文チェック
    if filepath.endswith(".py"):
        try:
            compile(data.decode("utf-8", errors="replace"), filepath, "exec")
        except SyntaxError as e:
            raise ValueError(f"ダウンロードしたファイルの構文エラー: {filepath}: {e}")

    local_path = os.path.join(BASE_DIR, filepath.replace("/", os.sep))
    os.makedirs(os.path.dirname(local_path) if os.path.dirname(local_path) else BASE_DIR, exist_ok=True)

    # バックアップ
    if os.path.exists(local_path):
        shutil.copy2(local_path, local_path + ".bak")

    with open(local_path, "wb") as f:
        f.write(data)


def _write_update_marker():
    """更新開始時にマーカーを置く。中断検出に使う。"""
    try:
        with open(UPDATE_MARKER_FILE, "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
    except Exception as e:
        logger.warning("update marker 書き込み失敗: %s", e)


def _clear_update_marker():
    """更新成功時にマーカーを削除する。"""
    try:
        if os.path.exists(UPDATE_MARKER_FILE):
            os.remove(UPDATE_MARKER_FILE)
    except Exception as e:
        logger.warning("update marker 削除失敗: %s", e)


def _list_backups():
    """BASE_DIR 配下の .bak ファイルを列挙（bin/ と __pycache__/ は除外）。"""
    backups = []
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in {".git", "bin", "__pycache__"}]
        for fname in files:
            if fname.endswith(".bak"):
                backups.append(os.path.join(root, fname))
    return backups


def rollback_from_backups():
    """
    .bak ファイルを元のファイルに戻す（更新失敗時の手動復旧 or 起動時自動復旧用）。
    戻り値: 復旧したファイル数
    """
    restored = 0
    for bak in _list_backups():
        original = bak[:-4]  # ".bak" を除去
        try:
            shutil.copy2(bak, original)
            os.remove(bak)
            restored += 1
            logger.info("rollback: %s ← %s", original, bak)
        except Exception as e:
            logger.warning("rollback 失敗 (%s): %s", bak, e)
    return restored


def check_and_recover_from_failed_update():
    """
    起動時に呼び出す。マーカーが残っていたら前回更新が中断したと判断し、
    .bak からロールバックする。完了後マーカーを削除する。
    """
    if not os.path.exists(UPDATE_MARKER_FILE):
        return False
    logger.warning("⚠️ 前回の自動更新が中断された可能性を検知。ロールバックを試行します。")
    n = rollback_from_backups()
    logger.warning("ロールバック完了: %d ファイル復旧", n)
    _clear_update_marker()
    return True


def _cleanup_backups():
    """成功した更新の .bak を掃除する。"""
    for bak in _list_backups():
        try:
            os.remove(bak)
        except Exception:
            pass


def run_update_async():
    """バックグラウンドで更新を実行"""
    def _do_update():
        with _update_lock:
            _update_state["status"] = "updating"
            _update_state["message"] = "更新ファイルを取得中..."
        _write_update_marker()

        try:
            # GitHubのファイル一覧を取得して除外リスト以外を更新
            with _update_lock:
                _update_state["message"] = "ファイル一覧を取得中..."
            all_files = _get_all_files()
            files = [f for f in all_files if f not in EXCLUDE_FILES]

            for i, filepath in enumerate(files):
                with _update_lock:
                    _update_state["message"] = f"ダウンロード中: {filepath} ({i+1}/{len(files)})"
                _download_file(filepath)

            # GitHubにないローカルファイルを削除
            with _update_lock:
                _update_state["message"] = "不要ファイルを削除中..."
            github_files = set(all_files) | {"version.json"}
            for root, dirs, local_files in os.walk(BASE_DIR):
                dirs[:] = [d for d in dirs if d not in {".git", "bin", "__pycache__"}]
                for fname in local_files:
                    local_abs = os.path.join(root, fname)
                    rel = os.path.relpath(local_abs, BASE_DIR).replace(os.sep, "/")
                    if rel in EXCLUDE_FILES or rel in github_files or rel.endswith(".bak"):
                        continue
                    try:
                        os.remove(local_abs)
                    except Exception:
                        pass

            # version.jsonを最後に更新
            _download_file("version.json")

            # 成功: マーカーと .bak を片付ける
            _clear_update_marker()
            _cleanup_backups()

            with _update_lock:
                _update_state["status"] = "done"
                _update_state["message"] = "次回起動時に反映されます"

        except Exception as e:
            # 失敗: マーカーは残したまま（次回起動時にロールバックされる）
            logger.error("更新失敗: %s", e)
            with _update_lock:
                _update_state["status"] = "error"
                _update_state["message"] = f"更新エラー: {str(e)}（次回起動時にロールバックされます）"

    threading.Thread(target=_do_update, daemon=True).start()


def get_update_state():
    with _update_lock:
        return dict(_update_state)
