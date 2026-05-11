"""
BOOTH サブ画像 4: 「先着 10 名限定 6,980 円」価格訴求バナー

世界観重視のメインサムネに対し、こちらは派手目で価格・希少性を伝える役割。
ピュア Python (PIL) で生成。
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os

OUT_PATH = r"C:\Users\kyohei\Downloads\booth_sub4_price_1280x720.png"

W, H = 1280, 720

# Windows 標準フォント
WINDOWS_FONTS = os.environ.get("WINDIR", r"C:\Windows") + r"\Fonts"


def load_font(name, size):
    try:
        return ImageFont.truetype(os.path.join(WINDOWS_FONTS, name), size)
    except Exception:
        return ImageFont.load_default()


# 1) 背景: 紺グラデーション（メインサムネと統一）
bg = Image.new("RGB", (W, H), (10, 20, 38))  # #0A1426
# 上から下へ #0A1426 → #163A6F → #0A1426 の縦グラデ
for y in range(H):
    t = y / H
    # 中央が少し明るい
    factor = 1.0 - abs(t - 0.5) * 1.4
    r = int(10 + (22 - 10) * factor)
    g = int(20 + (58 - 20) * factor)
    b = int(38 + (111 - 38) * factor)
    ImageDraw.Draw(bg).line([(0, y), (W, y)], fill=(r, g, b))

# 2) 左上に大きな赤い斜め帯 (緊急性アピール) - 上端の派手目アクセント
overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
od = ImageDraw.Draw(overlay)

# 上部赤グラデバンド
for y in range(120):
    alpha = int(255 * (1 - y / 120) ** 1.5)
    od.line([(0, y), (W, y)], fill=(220, 38, 38, alpha))

bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
draw = ImageDraw.Draw(bg)

# 3) 上部の「★ 先着 10 名限定 早期割引」
font_top_label = load_font("meiryob.ttc", 48)
top_text = "★ 先着 10 名限定  早期割引 ★"
tw = draw.textlength(top_text, font=font_top_label)
draw.text(
    ((W - tw) // 2, 30),
    top_text,
    font=font_top_label,
    fill=(255, 255, 255),
)

# 4) サブテキスト: 「BOOTH 販売はこの 10 名で終了」
font_sub_warning = load_font("meiryo.ttc", 24)
warn_text = "BOOTH での販売は 10 名で終了 → Phase 2 サブスク移行"
ww = draw.textlength(warn_text, font=font_sub_warning)
draw.text(
    ((W - ww) // 2, 92),
    warn_text,
    font=font_sub_warning,
    fill=(255, 220, 220),
)

# 5) 中央メインエリア: 価格表示
# 「通常 9,800 円」(取り消し線) — 日本語があるので meiryob.ttc を使う
font_normal_price = load_font("meiryob.ttc", 52)
normal_text = "通常価格  9,800 円"
nw = draw.textlength(normal_text, font=font_normal_price)
normal_x = (W - nw) // 2
normal_y = 220
draw.text((normal_x, normal_y), normal_text, font=font_normal_price, fill=(160, 175, 195))
# 取り消し線
line_y = normal_y + 35
draw.line(
    [(normal_x - 20, line_y), (normal_x + nw + 20, line_y)],
    fill=(220, 100, 100),
    width=4,
)

# 矢印
font_arrow = load_font("arialbd.ttf", 60)
arrow = "▼"
aw = draw.textlength(arrow, font=font_arrow)
draw.text(((W - aw) // 2, 305), arrow, font=font_arrow, fill=(255, 100, 100))

# 大きな価格「6,980 円」
font_big_price = load_font("arialbd.ttf", 220)
big_text = "6,980"
bw = draw.textlength(big_text, font=font_big_price)
big_x = (W - bw) // 2 - 60  # 円文字分だけ左にずらす
big_y = 380
# 軽く影
draw.text((big_x + 4, big_y + 4), big_text, font=font_big_price, fill=(60, 0, 0))
# 本体（白〜黄色のグラデっぽく単色で）
draw.text((big_x, big_y), big_text, font=font_big_price, fill=(255, 245, 100))

# 「円」を右に
font_yen = load_font("meiryob.ttc", 100)
yen_x = big_x + bw + 10
yen_y = big_y + 90
draw.text((yen_x + 4, yen_y + 4), "円", font=font_yen, fill=(60, 0, 0))
draw.text((yen_x, yen_y), "円", font=font_yen, fill=(255, 245, 100))

# 6) OFF 額バッジ（右上から少し下）
# 赤丸バッジ「2,820 円 OFF」風
badge_size = 200
badge = Image.new("RGBA", (badge_size, badge_size), (0, 0, 0, 0))
bd = ImageDraw.Draw(badge)
# 円
bd.ellipse([(0, 0), (badge_size, badge_size)], fill=(220, 38, 38, 255), outline=(255, 255, 255, 255), width=4)
# テキスト
font_badge1 = load_font("meiryob.ttc", 28)
font_badge2 = load_font("arialbd.ttf", 36)
t1 = "29%"
t2 = "OFF"
t1w = bd.textlength(t1, font=font_badge2)
t2w = bd.textlength(t2, font=font_badge2)
bd.text(((badge_size - t1w) // 2, 50), t1, font=font_badge2, fill=(255, 255, 255))
bd.text(((badge_size - t2w) // 2, 100), t2, font=font_badge2, fill=(255, 255, 255))

# 軽く回転（傾けて貼る）
badge = badge.rotate(-15, resample=Image.BICUBIC, expand=True)
bg = bg.convert("RGBA")
bg.alpha_composite(badge, (W - badge.width - 40, 180))
bg = bg.convert("RGB")

# 7) 下部: アップデート無料 / 商用利用 OK 訴求
draw = ImageDraw.Draw(bg)
font_bottom = load_font("meiryob.ttc", 26)
bottom_text = "アップデート無料  /  商用利用 OK  /  ライセンス 2 台"
bw2 = draw.textlength(bottom_text, font=font_bottom)
draw.text(
    ((W - bw2) // 2, H - 70),
    bottom_text,
    font=font_bottom,
    fill=(180, 220, 255),
)

# 保存
bg.save(OUT_PATH, "PNG", optimize=True)
print(f"saved: {OUT_PATH}")
print(f"  size: {W}x{H}, {os.path.getsize(OUT_PATH) // 1024} KB")
