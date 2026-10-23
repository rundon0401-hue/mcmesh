# -*- coding: utf-8 -*-
"""生成 MCMesh 的图标（一个绿色等距方块 + 深色圆角底板），输出：
   icon.ico   给 exe 用（多尺寸：256/128/64/48/32/24/16）
   icon.png   给窗口/预览用（256）

设计取色和界面一致：底板 #15181d，主色 #4ade80。
"""
import pathlib

from PIL import Image, ImageDraw, ImageFilter

OUT = pathlib.Path(__file__).resolve().parent.parent
ICON_ICO = OUT / "icon.ico"
ICON_PNG = OUT / "icon.png"

BG_TOP = (35, 48, 60, 255)      # #23303c
BG_BOTTOM = (16, 20, 26, 255)   # #10141a
ACCENT = (74, 222, 128, 255)    # #4ade80
TOP_FACE = (185, 246, 202, 255)  # #b9f6ca
LEFT_FACE = (74, 222, 128, 255)  # #4ade80
RIGHT_FACE = (28, 143, 77, 255)  # #1c8f4d
EDGE = (12, 26, 19, 255)        # #0c1a13


def rounded_mask(size: int, radius_ratio: float = 0.22) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    r = int(size * radius_ratio)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    return m


def vertical_gradient(size: int, top, bottom) -> Image.Image:
    g = Image.new("RGBA", (size, size))
    d = ImageDraw.Draw(g)
    for y in range(size):
        t = y / max(1, size - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(4))
        d.line([(0, y), (size, y)], fill=c)
    return g


def draw_icon(size: int = 1024) -> Image.Image:
    base = vertical_gradient(size, BG_TOP, BG_BOTTOM)

    # 内发光：中间偏上一点亮
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([int(size * 0.08), int(-size * 0.28), int(size * 0.92), int(size * 0.62)],
               fill=(74, 222, 128, 46))
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.09))
    base = Image.alpha_composite(base, glow)

    # 圆角
    base.putalpha(rounded_mask(size))

    # 细边
    d = ImageDraw.Draw(base)
    bw = max(2, int(size * 0.022))
    d.rounded_rectangle([bw // 2, bw // 2, size - 1 - bw // 2, size - 1 - bw // 2],
                        radius=int(size * 0.22), outline=(74, 222, 128, 110), width=bw)

    # 等距方块
    cx, cy = size * 0.5, size * 0.5
    s = size * 0.275                      # 半宽
    h = s * 0.92                          # 顶点到中心的高度
    top = (cx, cy - h)
    up_r = (cx + s, cy - h / 2)
    lo_r = (cx + s, cy + h / 2)
    bot = (cx, cy + h)
    lo_l = (cx - s, cy + h / 2)
    up_l = (cx - s, cy - h / 2)
    mid = (cx, cy)

    d.polygon([top, up_r, mid, up_l], fill=TOP_FACE)          # 顶面
    d.polygon([up_l, mid, bot, lo_l], fill=LEFT_FACE)         # 左面
    d.polygon([up_r, mid, bot, lo_r], fill=RIGHT_FACE)        # 右面

    ew = max(2, int(size * 0.012))
    d.line([top, up_r, lo_r, bot, lo_l, up_l, top], fill=EDGE, width=ew, joint="curve")
    d.line([mid, top], fill=EDGE, width=ew)
    d.line([mid, lo_l], fill=EDGE, width=ew)
    d.line([mid, lo_r], fill=EDGE, width=ew)

    # 方块上的一点高光
    hl = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hd.polygon([top, up_r, mid, up_l], fill=(255, 255, 255, 40))
    base = Image.alpha_composite(base, hl.filter(ImageFilter.GaussianBlur(size * 0.01)))
    return base


def main() -> None:
    master = draw_icon(1024)
    master.resize((256, 256), Image.LANCZOS).save(ICON_PNG)
    sizes = [256, 128, 64, 48, 32, 24, 16]
    master.save(ICON_ICO, format="ICO", sizes=[(s, s) for s in sizes])
    print("写出:", ICON_ICO, "和", ICON_PNG)


if __name__ == "__main__":
    main()
