"""
auto_update.py - GitHub自動更新モジュール
app.pyからimportして使う
"""
import os
import json
import shutil
import sys
import threading
import time
import urllib.request
import urllib.error

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
TOKEN_FILE = os.path.join(BIN_DIR, "github_token.txt")
LOCAL_VERSION_FILE = os.path.join(BASE_DIR, "version.json")

# ここを自分のリポジトリに書き換える
GITHUB_OWNER = "kyohei0612"
GITHUB_REPO = "clipgift"
GITHUB_BRANCH = "main"

# 更新から除外するファイル・フォルダ
EXCLUDE_FILES = {
    "bin/ffmpeg.exe",
    "bin/ffprobe.exe",
    "bin/audiowaveform.exe",
    "bin/github_token.txt",
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


def _load_token():
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


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


def _get_all_files(token, path=""):
    """GitHubリポジトリのファイル一覧を再帰取得"""
    url = _github_api_url(path)
    data = json.loads(_fetch_url(url, token).decode("utf-8"))
    files = []
    for item in data:
        if item["type"] == "file":
            files.append(item["path"])
        elif item["type"] == "dir":
            files.extend(_get_all_files(token, item["path"]))
    return files


def _fetch_url(url, token=""):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"token {token}")
    req.add_header("User-Agent", "youtube-clip-tool-updater")
    with urllib.request.urlopen(req, timeout=10) as res:
        return res.read()


def get_remote_version(token):
    """GitHubのversion.jsonを取得"""
    url = _github_raw_url("version.json")
    data = _fetch_url(url, token)
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
    token = _load_token()
    if not token:
        return {"has_update": False, "error": "トークンが設定されていません"}

    try:
        remote = get_remote_version(token)
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


def _download_file(filepath, token):
    """GitHubからファイルをダウンロードしてローカルに上書き"""
    url = _github_raw_url(filepath)
    data = _fetch_url(url, token)

    local_path = os.path.join(BASE_DIR, filepath.replace("/", os.sep))
    os.makedirs(os.path.dirname(local_path) if os.path.dirname(local_path) else BASE_DIR, exist_ok=True)

    # バックアップ
    if os.path.exists(local_path):
        shutil.copy2(local_path, local_path + ".bak")

    with open(local_path, "wb") as f:
        f.write(data)


def run_update_async():
    """バックグラウンドで更新を実行"""
    def _do_update():
        with _update_lock:
            _update_state["status"] = "updating"
            _update_state["message"] = "更新ファイルを取得中..."

        try:
            token = _load_token()

            # GitHubのファイル一覧を取得して除外リスト以外を更新
            with _update_lock:
                _update_state["message"] = "ファイル一覧を取得中..."
            all_files = _get_all_files(token)
            files = [f for f in all_files if f not in EXCLUDE_FILES]

            for i, filepath in enumerate(files):
                with _update_lock:
                    _update_state["message"] = f"ダウンロード中: {filepath} ({i+1}/{len(files)})"
                _download_file(filepath, token)

            # GitHubにないローカルファイルを削除
            with _update_lock:
                _update_state["message"] = "不要ファイルを削除中..."
            github_files = set(all_files) | {"version.json"}
            for root, dirs, local_files in os.walk(BASE_DIR):
                # .gitフォルダは除外
                dirs[:] = [d for d in dirs if d not in {".git", "bin", "__pycache__"}]
                for fname in local_files:
                    local_abs = os.path.join(root, fname)
                    rel = os.path.relpath(local_abs, BASE_DIR).replace(os.sep, "/")
                    # 除外リスト・GitHubにある・.bakは無視
                    if rel in EXCLUDE_FILES or rel in github_files or rel.endswith(".bak"):
                        continue
                    try:
                        os.remove(local_abs)
                    except Exception:
                        pass

            # version.jsonを最後に更新
            _download_file("version.json", token)

            with _update_lock:
                _update_state["status"] = "done"
                _update_state["message"] = "更新完了。再起動します..."

            # 2秒後にサーバー再起動
            time.sleep(2)
            os.execv(sys.executable, [sys.executable] + sys.argv)

        except Exception as e:
            with _update_lock:
                _update_state["status"] = "error"
                _update_state["message"] = f"更新エラー: {str(e)}"

    threading.Thread(target=_do_update, daemon=True).start()


def get_update_state():
    with _update_lock:
        return dict(_update_state)
