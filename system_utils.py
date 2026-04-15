"""
システム系ユーティリティ:
- Python 実行パス解決（サブプロセス起動用）
- 一時ファイルクリーンアップ
- 起動回数カウント
"""
import os
import sys
import glob
import shutil
import tempfile
import logging
import traceback

from paths import BIN_DIR, START_COUNT_FILE, PYTHON_PATH_FILE

logger = logging.getLogger(__name__)


def get_python_exe():
    """コンソールなしの pythonw.exe を優先して返す。"""

    def _resolve(path):
        """python.exe → pythonw.exe に変換して存在すれば返す"""
        pythonw = path.replace("python.exe", "pythonw.exe")
        if os.path.exists(pythonw):
            return pythonw
        if os.path.exists(path):
            return path
        return None

    # ① インストール時に記録したパスを最優先
    if os.path.exists(PYTHON_PATH_FILE):
        try:
            with open(PYTHON_PATH_FILE, "r", encoding="utf-8") as pf:
                recorded = pf.read().strip()
            if recorded:
                result = _resolve(recorded)
                if result:
                    return result
        except Exception:
            pass

    # ② sys.executable が python*.exe なら使う（通常の .py 実行時）
    exe = sys.executable
    if "python" in os.path.basename(exe).lower() and exe.endswith(".exe"):
        result = _resolve(exe)
        if result:
            return result

    # ③ exe 化されている場合: PYTHONHOME 環境変数から探す
    pythonhome = os.environ.get("PYTHONHOME", "")
    if pythonhome:
        candidate = os.path.join(pythonhome, "python.exe")
        result = _resolve(candidate)
        if result:
            return result

    # ④ exe 化されている場合: sys.executable と同じフォルダに python.exe があるか確認
    exe_dir = os.path.dirname(exe)
    candidate = os.path.join(exe_dir, "python.exe")
    result = _resolve(candidate)
    if result:
        return result

    # ⑤ PATH から python を探す（最終フォールバック）
    python_in_path = shutil.which("pythonw") or shutil.which("python")
    if python_in_path:
        return python_in_path

    # ⑥ 何も見つからなければ sys.executable をそのまま返す
    return exe


def cleanup_temp_files_and_dirs():
    """
    不要な一時ファイル・ディレクトリをすべて削除（clipgen_*系や .mp3 / .wav など）。
    """
    temp_root = tempfile.gettempdir()

    patterns = [
        os.path.join(temp_root, "clipgen_*"),
        os.path.join(temp_root, "*.mp3"),
        os.path.join(temp_root, "*.wav"),
        os.path.join(temp_root, "*.tmp"),
        os.path.join(temp_root, "*.json"),
    ]

    deleted_count = 0

    for pattern in patterns:
        for path in glob.glob(pattern):
            try:
                if os.path.isdir(path):
                    shutil.rmtree(path)
                    logger.info("🗑️ 一時ディレクトリ削除: %s", path)
                else:
                    os.remove(path)
                    logger.info("🗑️ 一時ファイル削除: %s", path)
                deleted_count += 1
            except PermissionError as e:
                if hasattr(e, "winerror") and e.winerror == 32:
                    # 他プロセスが使用中 → ログを出さずスキップ
                    continue
                else:
                    logger.warning("PermissionError: %s", path)
                    traceback.print_exc()
            except Exception as e:
                logger.warning("削除エラー: %s: %s", path, e)
                traceback.print_exc()

    logger.info("✅ 合計削除数: %d", deleted_count)


def check_and_increment_start_count():
    """起動回数をカウントし、2回に達したら一時ファイルを削除する。"""
    count = 0

    if os.path.exists(START_COUNT_FILE):
        try:
            with open(START_COUNT_FILE, "r", encoding="utf-8") as f:
                count = int(f.read().strip())
        except Exception:
            logger.warning("起動回数読み取りエラー。初期化します")
            count = 0

    count += 1
    logger.info("🔢 起動回数: %d/2", count)

    if count >= 2:
        logger.info("🔄 起動回数2回に達したため一時ファイルを全削除します")
        cleanup_temp_files_and_dirs()
        count = 0  # カウントをリセット

    try:
        with open(START_COUNT_FILE, "w", encoding="utf-8") as f:
            f.write(str(count))
    except Exception as e:
        logger.warning("起動回数の保存に失敗: %s", e)
