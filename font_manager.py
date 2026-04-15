"""
フォント管理ロジック。
Windows のシステム/ユーザーフォントフォルダを探索し、
日本語名が取得できるものだけを返す。
"""
import os
import json
import logging

from paths import LAST_FONT_FILE

logger = logging.getLogger(__name__)


def get_font_dirs():
    """システム・ユーザーフォントフォルダを返す"""
    dirs = []
    windir = os.environ.get("WINDIR", "C:/Windows")
    dirs.append(os.path.join(windir, "Fonts"))
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        dirs.append(os.path.join(localappdata, "Microsoft", "Windows", "Fonts"))
    return dirs


def get_font_japanese_name(path):
    """フォントファイルから日本語表示名を取得する。日本語名がなければ None。"""
    try:
        from fontTools.ttLib import TTFont
        font = TTFont(path, fontNumber=0)
        name_table = font["name"]
        # nameID=4: Full name, nameID=1: Family name
        # langID=0x411(日本語) または platformID=1+langID=11(Mac日本語) を優先
        for target_id in (4, 1):
            # Windows日本語(platformID=3, langID=0x411)
            for record in name_table.names:
                if record.nameID == target_id and record.platformID == 3 and record.langID == 0x411:
                    try:
                        name = record.toUnicode()
                        if name:
                            return name
                    except Exception:
                        pass
            # Mac日本語(platformID=1, langID=11)
            for record in name_table.names:
                if record.nameID == target_id and record.platformID == 1 and record.langID == 11:
                    try:
                        name = record.toUnicode()
                        if name:
                            return name
                    except Exception:
                        pass
    except Exception:
        pass
    return None


def list_fonts():
    """日本語名を持つフォントのみ返す"""
    fonts = []
    for d in get_font_dirs():
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if fname.lower().endswith((".ttf", ".otf", ".ttc")):
                path = os.path.join(d, fname)
                display_name = get_font_japanese_name(path)
                if display_name:  # 日本語名があるもののみ
                    fonts.append({"name": fname, "display_name": display_name, "path": path})
    # 重複除去（ファイル名優先）
    seen = set()
    unique = []
    for f in fonts:
        if f["name"] not in seen:
            seen.add(f["name"])
            unique.append(f)
    # 表示名でソート
    unique.sort(key=lambda x: x["display_name"].lower())
    return unique


def load_last_font():
    try:
        with open(LAST_FONT_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("font_name", "")
    except Exception:
        return ""


def save_last_font(font_name):
    try:
        with open(LAST_FONT_FILE, "w", encoding="utf-8") as f:
            json.dump({"font_name": font_name}, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("save_last_font エラー: %s", e)
