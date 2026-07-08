"""
auto_update.py - GitHub自動更新モジュール
app.pyからimportして使う
"""
import os
import json
import shutil
import subprocess
import threading
import time
import hashlib
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
# 注意: support_center/{__init__,config,pii_masker,error_reporter}.py はエンドユーザーの
# app.py から参照されるため除外しない。`scripts/refresh_icon_cache.bat` もエンドユーザー用。
EXCLUDE_FILES = {
    "bin/ffmpeg.exe",
    "bin/ffprobe.exe",
    "bin/audiowaveform.exe",
    "bin/python_path.txt",
    "bin/last_font.json",
    "server_start_count.txt",
    "version.json",
    # サポートセンター: kyohei ローカル運用専用（エンドユーザーには不要）
    "support_center/mail_watcher.py",
    "support_center/claude_runner.py",
    "support_center/notify_kyohei.py",
    "support_center/reply_user.py",
    "support_center/state_machine.py",
    "support_center/.env.example",
    "scripts/watch_support_mail.py",
    "scripts/watch_support_idle.py",
    "scripts/watch_support_http.py",
    "scripts/reply_user.py",
    "scripts/configure_support_task.ps1",
    "scripts/configure_support_http_watcher.ps1",
    # 管理スクリプト: kyohei ローカル運用専用（エンドユーザーには不要）
    "scripts/issue_license.py",
    "scripts/revoke_license.py",
    "scripts/sync_sns_secrets_to_github.py",
    "scripts/package_for_booth.ps1",
}

# 製品配布物以外（マーケティングツール・CI・組織管理・環境変数）
# エンドユーザーへ降ろさない / ローカル削除もしない
EXCLUDE_PREFIXES = (
    ".company/",
    ".github/",
    ".claude/",
    "sns_automation/",
    ".env",
    ".gitignore",
    # サポートセンター kyohei ローカル運用ファイル
    "support_center/incoming/",
    "support_center/.env",
    # Tauri ソース（2026-05-21 v2.0.0 で導入、エンドユーザーには ClipGift.exe を
    # インストーラー経由で配布するので Rust ソースは不要）
    "src-tauri/",
)


def _is_excluded(rel_path: str) -> bool:
    if rel_path in EXCLUDE_FILES:
        return True
    return any(rel_path.startswith(prefix) for prefix in EXCLUDE_PREFIXES)


def _excluded_top_dirs() -> set:
    """
    EXCLUDE_PREFIXES から「トップレベルのディレクトリ名」集合を導出する。

    os.walk の dirs[:] フィルタで使うため、ディレクトリ全体を丸ごと除外する
    プレフィックス（例: ".company/"）のみを対象とする。
    階層深いプレフィックス（例: "support_center/incoming/"）や
    ファイル指定（例: ".env"）は対象外（_is_excluded 側で拾う）。
    """
    top_dirs = set()
    for prefix in EXCLUDE_PREFIXES:
        # ディレクトリプレフィックスは "/" で終わる。ファイル指定は除外。
        if not prefix.endswith("/"):
            continue
        # 末尾 "/" を除いて "/" を含まない = トップレベル 1 段
        name = prefix[:-1]
        if "/" in name or not name:
            continue
        top_dirs.add(name)
    return top_dirs

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


def _read_github_token():
    """bin/github_token.txt からトークン読込（無ければ None、レート制限緩和用）"""
    token_path = os.path.join(BASE_DIR, "bin", "github_token.txt")
    if os.path.exists(token_path):
        try:
            with open(token_path, "r", encoding="utf-8") as f:
                t = f.read().strip()
            return t or None
        except Exception:
            pass
    return None


def _get_all_files(path=""):
    """GitHub の git tree API で全ファイルを 1 リクエストで列挙（(path, sha) のタプル）。
    sha は差分判定（変わってないファイルの取得スキップ）に使う。"""
    url = (
        f"https://api.github.com/repos/"
        f"{GITHUB_OWNER}/{GITHUB_REPO}/git/trees/{GITHUB_BRANCH}?recursive=1"
    )
    data = json.loads(_fetch_url(url).decode("utf-8"))
    return [
        (item["path"], item.get("sha"))
        for item in data.get("tree", [])
        if item.get("type") == "blob"
    ]


def _fetch_url(url, _retries=3):
    sep = "&" if "?" in url else "?"
    full = f"{url}{sep}t={int(time.time())}"
    req = urllib.request.Request(full)
    req.add_header("User-Agent", "youtube-clip-tool-updater")
    req.add_header("Cache-Control", "no-cache")
    req.add_header("Pragma", "no-cache")
    token = _read_github_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.read()
    except urllib.error.HTTPError as e:
        # 429/403 = レート制限。少し待って再試行（Retry-After があれば尊重、最大30秒）。
        if e.code in (429, 403) and _retries > 0:
            ra = e.headers.get("Retry-After") if e.headers else None
            wait = int(ra) if (ra and str(ra).isdigit()) else 5
            time.sleep(min(wait, 30))
            return _fetch_url(url, _retries - 1)
        raise


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


def _git_blob_sha(path):
    """
    ローカルファイルの git blob SHA を計算する。

    GitHub の git tree API が返す各 blob の sha と同じ算出式
    （sha1("blob " + len + "\\0" + content)）。一致すれば内容が同じ = ダウンロード不要。
    失敗時は None を返し、呼び出し側は安全側に倒してダウンロードする。
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
        h = hashlib.sha1()
        h.update(b"blob " + str(len(data)).encode() + b"\0")
        h.update(data)
        return h.hexdigest()
    except Exception:
        return None


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


def _is_legitimate_empty_file(filepath: str) -> bool:
    """
    空でも正当なファイル名の判定

    Python の `__init__.py` はパッケージマーカーとして空でも有効。
    その他、空でも問題ないファイル種別がある場合はここで許容する。
    """
    basename = os.path.basename(filepath.replace("/", os.sep))
    if basename == "__init__.py":
        return True
    return False


def _download_file(filepath):
    """GitHubからファイルをダウンロードしてローカルに上書き"""
    url = _github_raw_url(filepath)
    data = _fetch_url(url)

    # 空ファイルは原則異常だが、__init__.py 等の正当な空ファイルは許容
    # （CDN キャッシュ起因の事故も含めて safe net として 1 度リトライしてから判定）
    if not data:
        if _is_legitimate_empty_file(filepath):
            data = b""  # 空のままローカル保存（パッケージマーカーとして有効）
        else:
            # CDN キャッシュ古い可能性 → 1 度リトライしてみる
            time.sleep(1.0)
            data = _fetch_url(url)
            if not data:
                if _is_legitimate_empty_file(filepath):
                    data = b""
                else:
                    raise ValueError(
                        f"ダウンロードしたファイルが空です: {filepath}（"
                        f"CDN キャッシュの可能性があります、5 分後に再試行してください）"
                    )

    # .pyファイルの場合、Pythonとして構文チェック（空ファイル除く）
    if filepath.endswith(".py") and data:
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


def _resolve_python_exe():
    """bin/python_path.txt から python.exe のフルパスを解決。"""
    txt_path = os.path.join(BASE_DIR, "bin", "python_path.txt")
    if os.path.exists(txt_path):
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                recorded = f.read().strip()
            # python_path.txt は pythonw.exe を記録してる場合 → python.exe に戻す
            python_exe = recorded.replace("pythonw.exe", "python.exe")
            if os.path.exists(python_exe):
                return python_exe
            if os.path.exists(recorded):
                return recorded
        except Exception:
            pass
    return "python"


def _sync_pip_packages_from_requirements():
    """
    requirements.txt の依存を pip install --user で同期する。

    2026-05-21 新設。auto_update 直後に呼ばれる。
    既存ユーザーが新依存（pywebview など）を取り損ねるのを防ぐ。

    - --user 指定で global site-packages を汚さない
    - --upgrade 付きで既存パッケージの version 制約に追従
    - 失敗してもログ警告のみ、更新自体は成功扱い
    """
    req_path = os.path.join(BASE_DIR, "requirements.txt")
    if not os.path.exists(req_path):
        return

    python_exe = _resolve_python_exe()
    creationflags = 0x08000000 if os.name == "nt" else 0
    try:
        result = subprocess.run(
            [python_exe, "-m", "pip", "install", "--user", "-r", req_path, "--quiet"],
            cwd=BASE_DIR,
            creationflags=creationflags,
            timeout=300,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            logger.info("pip sync 完了: requirements.txt の依存をインストール")
        else:
            stderr_tail = (result.stderr or "")[-300:]
            logger.warning("pip sync 失敗 returncode=%s stderr=%s", result.returncode, stderr_tail)
    except subprocess.TimeoutExpired:
        logger.warning("pip sync タイムアウト（5分超）")
    except Exception as e:
        logger.warning("pip sync エラー: %s", e)


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
            all_items = _get_all_files()
            all_files = [p for p, _ in all_items]
            sha_map = {p: s for p, s in all_items}
            files = [f for f in all_files if not _is_excluded(f)]

            skipped = 0
            for i, filepath in enumerate(files):
                with _update_lock:
                    _update_state["message"] = f"更新中: {filepath} ({i+1}/{len(files)})"
                # レート制限(429)回避: ローカルの git blob SHA が GitHub と一致する
                # ファイル（＝内容が同じ）は取得をスキップする。変わったファイルだけ落とす。
                local_path = os.path.join(BASE_DIR, filepath.replace("/", os.sep))
                remote_sha = sha_map.get(filepath)
                if remote_sha and os.path.exists(local_path) and _git_blob_sha(local_path) == remote_sha:
                    skipped += 1
                    continue
                _download_file(filepath)
            logger.info(
                "auto_update: %d 取得 / %d スキップ（差分のみ取得）",
                len(files) - skipped, skipped,
            )

            # GitHubにないローカルファイルを削除
            with _update_lock:
                _update_state["message"] = "不要ファイルを削除中..."
            github_files = set(all_files) | {"version.json"}
            # EXCLUDE_PREFIXES からトップレベル除外ディレクトリを導出 + os.walk 必須スキップ
            skip_dirs = _excluded_top_dirs() | {".git", "bin", "__pycache__"}
            for root, dirs, local_files in os.walk(BASE_DIR):
                dirs[:] = [d for d in dirs if d not in skip_dirs]
                for fname in local_files:
                    local_abs = os.path.join(root, fname)
                    rel = os.path.relpath(local_abs, BASE_DIR).replace(os.sep, "/")
                    if _is_excluded(rel) or rel in github_files or rel.endswith(".bak"):
                        continue
                    try:
                        os.remove(local_abs)
                    except Exception:
                        pass

            # version.jsonを最後に更新
            _download_file("version.json")

            # 新しい requirements.txt の依存を同期（既存ユーザーが新依存を取り損ねないように）
            # 2026-05-21 新設。pywebview 等の新依存を auto_update 経由で自動セットアップ。
            with _update_lock:
                _update_state["message"] = "依存パッケージを同期中..."
            try:
                _sync_pip_packages_from_requirements()
            except Exception as e:
                logger.warning("pip sync 例外（更新自体は成功）: %s", e)

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
