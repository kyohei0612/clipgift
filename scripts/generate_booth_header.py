"""
BOOTH ショップヘッダー画像生成スクリプト

AI 生成の背景画像にロゴ + テキスト「ClipGift」+ タグラインを重ねて
BOOTH 推奨サイズ 1200×400 のヘッダー画像を出力する。
"""

from PIL import Image, ImageDraw, ImageFont
import os

# 入力
BG_PATH = r"C:\Users\kyohei\Downloads\ChatGPT Image 2026年5月11日 22_30_57.png"
LOGO_PATH = r"C:\Users\kyohei\ClipGift\static\ClipGiftLog.png"

# 出力
OUT_PATH = r"C:\Users\kyohei\Downloads\booth_header_clipgift_1200x400.png"

# BOOTH ヘッダー推奨サイズ
W, H = 1200, 400

# 1) 背景: 3:1 比率にリサイズ
bg = Image.open(BG_PATH).convert("RGB")
bg = bg.resize((W, H), Image.LANCZOS)

# 2) ロゴ: 透過 PNG として読み込み、適切なサイズに
logo = Image.open(LOGO_PATH).convert("RGBA")
LOGO_SIZE = 220
logo_resized = logo.resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)

# 中央少し左寄りにロゴ配置（テキストとのバランスで）
logo_x = 320
logo_y = (H - LOGO_SIZE) // 2
bg.paste(logo_resized, (logo_x, logo_y), logo_resized)

# 3) テキスト描画
draw = ImageDraw.Draw(bg)

# フォントロード（Windows 標準フォントを使用）
WINDOWS_FONTS = os.environ.get("WINDIR", r"C:\Windows") + r"\Fonts"


def load_font(name, size):
    try:
        return ImageFont.truetype(os.path.join(WINDOWS_FONTS, name), size)
    except Exception:
        return ImageFont.load_default()


font_title = load_font("arialbd.ttf", 84)     # ClipGift（英字、太字）
font_tag = load_font("meiryob.ttc", 26)        # 日本語タグライン（メイリオ Bold）

# "ClipGift" タイトル（ロゴの右）
title_x = logo_x + LOGO_SIZE + 30
title_y = 130
draw.text((title_x, title_y), "ClipGift", font=font_title, fill=(255, 255, 255))

# タグライン（タイトルの下）
tag_text = "配信切り抜きの作業時間を 1/3 に"
tag_x = title_x + 6
tag_y = title_y + 100
# 薄水色（ブランド色寄り）
draw.text((tag_x, tag_y), tag_text, font=font_tag, fill=(150, 210, 255))

# 4) 保存
bg.save(OUT_PATH, "PNG", optimize=True)
print(f"✅ 保存完了: {OUT_PATH}")
print(f"   サイズ: {W}x{H} / {os.path.getsize(OUT_PATH) // 1024} KB")
