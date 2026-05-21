"""
ClipGift launcher (pywebview 版).

- Flask サーバーを subprocess で起動（既起動チェック付き）
- WebView2（Edge Chromium）ベースの pywebview ウィンドウで表示
- ウィンドウクローズで Flask も停止する
- pywebview / WebView2 が使えない環境はデフォルトブラウザに自動フォールバック
- 旧 Chrome --app モードの後継（v1.0.113 〜 v1.0.119）。Chrome 完全不要。
"""

import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
APP_PY = os.path.join(BASE_DIR, "app.py")
PYTHON_PATH_FILE = os.path.join(BASE_DIR, "bin", "python_path.txt")
ICON_PATH = os.path.join(BASE_DIR, "installer_assets", "ClipGiftLog.ico")

SERVER_PORT = 5001
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"

_LOG_FILE = os.path.join(BASE_DIR, "launcher_window.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [launcher] %(message)s",
    handlers=[
        logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("launcher")
log.info("===== launcher_window.py start =====")
log.info(f"sys.executable = {sys.executable}")
log.info(f"BASE_DIR = {BASE_DIR}")


def get_pythonw_path():
    """bin/python_path.txt から pythonw のフルパスを解決する。"""
    if os.path.exists(PYTHON_PATH_FILE):
        try:
            with open(PYTHON_PATH_FILE, "r", encoding="utf-8") as f:
                recorded = f.read().strip()
            pythonw = recorded.replace("python.exe", "pythonw.exe")
            if os.path.exists(pythonw):
                return pythonw
            if os.path.exists(recorded):
                return recorded
        except Exception as e:
            log.warning(f"python_path.txt 読み取り失敗: {e}")
    return "pythonw"


def is_server_running(url, timeout=1.5):
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False


def wait_for_server(url, timeout_sec=30):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if is_server_running(url):
            return True
        time.sleep(0.5)
    return False


def start_flask_subprocess():
    pythonw = get_pythonw_path()
    env = os.environ.copy()
    env["CLIPGEN_PORT"] = str(SERVER_PORT)
    env["LAUNCHED_BY_VBS"] = "1"
    log.info(f"Flask 起動: {pythonw} (port={SERVER_PORT})")
    creationflags = 0x08000000 if os.name == "nt" else 0
    proc = subprocess.Popen(
        [pythonw, APP_PY],
        cwd=BASE_DIR,
        env=env,
        creationflags=creationflags,
    )
    return proc


def open_window_pywebview(flask_proc):
    try:
        import webview
    except ImportError:
        log.warning("pywebview 未インストール → ブラウザフォールバック")
        return False

    try:
        webview.create_window(
            title="ClipGift",
            url=SERVER_URL,
            width=1280,
            height=820,
            min_size=(900, 600),
            resizable=True,
        )
    except Exception as e:
        log.error(f"pywebview create_window 失敗: {e}")
        return False

    def on_closed():
        log.info("ウィンドウクローズ → Flask 停止")
        if flask_proc is not None:
            try:
                flask_proc.terminate()
                flask_proc.wait(timeout=3)
            except Exception:
                try:
                    flask_proc.kill()
                except Exception as kill_err:
                    log.warning(f"Flask 停止失敗: {kill_err}")

    try:
        windows = webview.windows
        if windows:
            windows[0].events.closed += on_closed
    except Exception as e:
        log.warning(f"closed イベント登録失敗: {e}")

    start_kwargs = {}
    if os.name == "nt":
        start_kwargs["gui"] = "edgechromium"
    if os.path.exists(ICON_PATH):
        start_kwargs["icon"] = ICON_PATH

    try:
        webview.start(**start_kwargs)
        return True
    except TypeError:
        start_kwargs.pop("icon", None)
        try:
            webview.start(**start_kwargs)
            return True
        except Exception as e:
            log.error(f"pywebview start 失敗（リトライ後）: {e}")
            return False
    except Exception as e:
        log.error(f"pywebview start 失敗: {e}")
        return False


def open_window_browser_fallback():
    log.info(f"フォールバック: デフォルトブラウザで {SERVER_URL} を開く")
    webbrowser.open(SERVER_URL)


def main():
    flask_proc = None
    if is_server_running(SERVER_URL):
        log.info("Flask 既に起動中、subprocess 起動をスキップ")
    else:
        flask_proc = start_flask_subprocess()
        if not wait_for_server(SERVER_URL, timeout_sec=30):
            log.error("Flask 起動が間に合わなかった")
            sys.exit(1)
        log.info("Flask 起動確認")

    if not open_window_pywebview(flask_proc):
        open_window_browser_fallback()


if __name__ == "__main__":
    main()
