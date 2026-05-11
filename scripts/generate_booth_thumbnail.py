"""
BOOTH 商品メインサムネ画像生成スクリプト

AI 生成の背景画像にロゴ + ブランド文字を重ねて
BOOTH 商品ページ用 1280×720 のメインサムネを出力する。

世界観重視（プロ仕様のヒーロー画像）路線。
価格・先着訴求はサブ画像 4 に分離してこちらは入れない。
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os

# 入力
BG_PATH = r"C:\Users\kyohei\Downloads\ChatGPT Image 2026年5月11日 22_45_29.png"
LOGO_PATH = r"C:\Users\kyohei\ClipGift\static\ClipGiftLog.png"

# 出力
OUT_PATH = r"C:\Users\kyohei\Downloads\booth_thumbnail_main_1280x720.png"

# BOOTH 商品メインサムネ推奨サイズ（16:9）
W, H = 1280, 720

# 1) 背景: 16:9 にリサイズ
bg = Image.open(BG_PATH).convert("RGB")
bg = bg.resize((W, H), Image.LANCZOS)

# 2) ロゴ
logo = Image.open(LOGO_PATH).convert("RGBA")
LOGO_SIZE = 320
logo_resized = logo.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)

# 中央付近に配置（少し左寄り、テキストとのバランス）
logo_x = 360
logo_y = (H - LOGO_SIZE) // 2 - 20
bg.paste(logo_resized, (logo_x, logo_y), logo_resized)

# 3) テキスト描画
draw = ImageDraw.Draw(bg)

# Windows 標準フォント
WINDOWS_FONTS = os.environ.get("WINDIR", r"C:\Windows") + r"\Fonts"


def load_font(name, size):
    try:
        return ImageFont.truetype(os.path.join(WINDOWS_FONTS, name), size)
    except Exception:
        return ImageFont.load_default()


font_title = load_font("arialbd.ttf", 130)     # ClipGift
font_subtitle = load_font("arial.ttf", 32)     # Stream Clip Tool
font_tag_jp = load_font("meiryob.ttc", 30)     # 日本語タグライン

# "ClipGift" タイトル（ロゴの右）
title_x = logo_x + LOGO_SIZE + 50
title_y = 240
draw.text((title_x, title_y), "ClipGift", font=font_title, fill=(255, 255, 255))

# 区切り線（白薄、細）
line_y = title_y + 155
draw.line(
    [(title_x, line_y), (title_x + 360, line_y)],
    fill=(150, 210, 255, 200),
    width=2,
)

# サブタイトル英語（区切り線の下）
subtitle_y = line_y + 14
draw.text(
    (title_x, subtitle_y),
    "Stream Clip Tool for Windows",
    font=font_subtitle,
    fill=(180, 220, 255),
)

# 日本語タグライン（さらに下）
tag_y = subtitle_y + 50
draw.text(
    (title_x, tag_y),
    "配信切り抜きの作業時間を 1/3 に",
    font=font_tag_jp,
    fill=(200, 230, 255),
)

# 右下に控えめなエディション表記
font_edition = load_font("arial.ttf", 22)
edition_text = "Pro Edition  /  Phase 1"
ew = draw.textlength(edition_text, font=font_edition)
draw.text(
    (W - ew - 40, H - 50),
    edition_text,
    font=font_edition,
    fill=(120, 170, 220),
)

# 4) 保存
bg.save(OUT_PATH, "PNG", optimize=True)
print(f"saved: {OUT_PATH}")
print(f"  size: {W}x{H}, {os.path.getsize(OUT_PATH) // 1024} KB")
