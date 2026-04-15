"""
アプリ共通のファイルパス定数。他モジュールはここから import する。
"""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")

# bin/ が無い場合は作っておく（初回起動時）
os.makedirs(BIN_DIR, exist_ok=True)

START_COUNT_FILE = os.path.join(BASE_DIR, "server_start_count.txt")
LAST_FONT_FILE = os.path.join(BIN_DIR, "last_font.json")
PYTHON_PATH_FILE = os.path.join(BIN_DIR, "python_path.txt")
