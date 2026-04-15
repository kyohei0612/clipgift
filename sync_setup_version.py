"""
setup.iss の `#define MyAppVersion "..."` を version.json の値と同期する小ユーティリティ。
build_and_push.bat から呼ばれる。単独実行も可能。
"""
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VERSION_FILE = BASE_DIR / "version.json"
SETUP_ISS = BASE_DIR / "setup.iss"


def main():
    if not VERSION_FILE.exists():
        print(f"ERROR: version.json が見つかりません: {VERSION_FILE}", file=sys.stderr)
        return 1
    if not SETUP_ISS.exists():
        print(f"ERROR: setup.iss が見つかりません: {SETUP_ISS}", file=sys.stderr)
        return 1

    version = json.loads(VERSION_FILE.read_text(encoding="utf-8")).get("version")
    if not version:
        print("ERROR: version.json に version フィールドがありません", file=sys.stderr)
        return 1

    before = SETUP_ISS.read_text(encoding="utf-8")
    after = re.sub(
        r'#define MyAppVersion "[^"]+"',
        f'#define MyAppVersion "{version}"',
        before,
    )
    if before == after:
        print(f"setup.iss MyAppVersion はすでに {version} です（変更なし）")
        return 0

    # 改行コードを変えないよう newline='' で書き込む
    SETUP_ISS.write_text(after, encoding="utf-8", newline="")
    print(f"setup.iss MyAppVersion -> {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
