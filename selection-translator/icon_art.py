"""
SweetSpot 图标：渐变圆角方块 + 白色四角星光（寓意“润色/点睛”）。
纯代码绘制，托盘图标和 exe 图标共用同一份图形。

单独运行本文件会生成 app.ico（供 PyInstaller 打包使用）和 icon_preview.png。
"""

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

COLOR_START = (99, 102, 241)   # indigo
COLOR_END = (236, 72, 153)     # pink
SS = 4                          # 超采样倍数，抗锯齿


def _sparkle(cx, cy, r, steps=96):
    """星形线 (astroid) 四角星：x = r·cos³t, y = r·sin³t，指数系数 k 控制星芒粗细"""
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        k = 1.15  # 越大星芒越尖细
        x = r * math.copysign(abs(c) ** (3 * k), c)
        y = r * math.copysign(abs(s) ** (3 * k), s)
        pts.append((cx + x, cy + y))
    return pts


def make_icon(size: int = 256) -> Image.Image:
    n = size * SS

    # 对角线渐变：右上 indigo -> 左下 pink
    g = Image.linear_gradient("L").resize((n, n))
    mask = ImageChops.add(g, g.rotate(90), scale=2)
    mask = ImageChops.invert(mask.transpose(Image.FLIP_LEFT_RIGHT))
    grad = Image.composite(
        Image.new("RGBA", (n, n), COLOR_START + (255,)),
        Image.new("RGBA", (n, n), COLOR_END + (255,)),
        mask,
    )

    # 圆角方块遮罩
    shape = Image.new("L", (n, n), 0)
    pad = round(n * 0.04)
    ImageDraw.Draw(shape).rounded_rectangle(
        (pad, pad, n - pad, n - pad), radius=round(n * 0.23), fill=255
    )
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    img.paste(grad, (0, 0), shape)

    d = ImageDraw.Draw(img)
    # 主星光
    d.polygon(_sparkle(n * 0.45, n * 0.55, n * 0.33), fill=(255, 255, 255, 255))
    # 右上小星光
    d.polygon(_sparkle(n * 0.73, n * 0.27, n * 0.11), fill=(255, 255, 255, 235))

    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    big = make_icon(256)
    big.save(here / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    # 预览：大图 + 托盘常见尺寸
    preview = Image.new("RGBA", (256 + 3 * 72 + 40, 256), (245, 245, 245, 255))
    preview.paste(big, (0, 0), big)
    x = 276
    for s in (64, 32, 16):
        im = make_icon(s)
        preview.paste(im, (x, 100), im)
        x += 72
    preview.save(here / "icon_preview.png")
    print("written app.ico / icon_preview.png")
