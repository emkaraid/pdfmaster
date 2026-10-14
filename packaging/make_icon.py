"""Рисует иконку приложения и собирает .icns (нужен iconutil из macOS).

python packaging/make_icon.py  ->  pdfmaster/resources/icon.png, build/icon.icns
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(ROOT, "pdfmaster", "resources", "fonts")


def draw(size: int = 1024) -> Image.Image:
    S = size / 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # «скруглённый квадрат» по сетке macOS: 824x824 с отступом 100
    m, r = int(100 * S), int(185 * S)
    box = (m, m, size - m, size - m)
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((box[0], box[1] + 14 * S, box[2], box[3] + 14 * S), r,
                                             fill=(0, 0, 0, 110))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18 * S)))
    # фон — тёплый красный градиент
    grad = Image.new("RGBA", (size, size))
    gd = ImageDraw.Draw(grad)
    for y in range(size):
        t = y / size
        c = (int(236 - 52 * t), int(92 - 44 * t), int(62 - 30 * t), 255)
        gd.line([(0, y), (size, y)], fill=c)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, r, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    # лист бумаги с загнутым углом
    px0, py0, px1, py1 = int(262 * S), int(190 * S), int(762 * S), int(840 * S)
    fold = int(120 * S)
    sh = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon([(px0, py0 + 10 * S), (px1 - fold, py0 + 10 * S), (px1, py0 + fold + 10 * S),
                                (px1, py1 + 10 * S), (px0, py1 + 10 * S)], fill=(60, 10, 0, 120))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(14 * S)))
    d.polygon([(px0, py0), (px1 - fold, py0), (px1, py0 + fold), (px1, py1), (px0, py1)], fill=(255, 255, 255, 255))
    d.polygon([(px1 - fold, py0), (px1 - fold, py0 + fold), (px1, py0 + fold)], fill=(222, 226, 233, 255))
    # строки текста
    try:
        fb = ImageFont.truetype(os.path.join(FONTS, "PT_Sans-Web-Bold.ttf"), int(118 * S))
    except Exception:
        fb = ImageFont.load_default()
    d.text((px0 + 58 * S, py0 + 58 * S), "PDF", font=fb, fill=(214, 69, 47, 255))
    y = py0 + 230 * S
    for k, w in enumerate((380, 340, 380, 300, 360, 220)):
        d.rounded_rectangle((px0 + 58 * S, y, px0 + (58 + w) * S, y + 22 * S), 11 * S, fill=(196, 202, 212, 255))
        y += 58 * S
    # синяя печать
    cx, cy, R = int(640 * S), int(700 * S), int(150 * S)
    st = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(st)
    blue = (38, 72, 190, 235)
    sd.ellipse((cx - R, cy - R, cx + R, cy + R), outline=blue, width=int(20 * S))
    sd.ellipse((cx - R * 0.72, cy - R * 0.72, cx + R * 0.72, cy + R * 0.72), outline=blue, width=int(9 * S))
    sd.line((cx - R * 0.45, cy + R * 0.05, cx - R * 0.1, cy + R * 0.38), fill=blue, width=int(22 * S))
    sd.line((cx - R * 0.1, cy + R * 0.38, cx + R * 0.48, cy - R * 0.3), fill=blue, width=int(22 * S))
    img.alpha_composite(st.rotate(-12, center=(cx, cy), resample=Image.BICUBIC))
    return img


def main() -> None:
    res = os.path.join(ROOT, "pdfmaster", "resources")
    build = os.path.join(ROOT, "build")
    os.makedirs(build, exist_ok=True)
    big = draw(1024)
    big.save(os.path.join(res, "icon.png"))
    iconset = os.path.join(build, "icon.iconset")
    shutil.rmtree(iconset, ignore_errors=True)
    os.makedirs(iconset)
    for s in (16, 32, 128, 256, 512):
        big.resize((s, s), Image.LANCZOS).save(os.path.join(iconset, f"icon_{s}x{s}.png"))
        big.resize((2 * s, 2 * s), Image.LANCZOS).save(os.path.join(iconset, f"icon_{s}x{s}@2x.png"))
    if sys.platform == "darwin":
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", os.path.join(build, "icon.icns")], check=True)
    print("icon ok")


if __name__ == "__main__":
    main()
