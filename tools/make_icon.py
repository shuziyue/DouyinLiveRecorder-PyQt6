#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成程序图标 assets/icon.ico

设计：紫色渐变圆角方块 + 白色播放三角 + 红色录制圆点
配色与主界面保持一致（#667eea -> #764ba2）

用法：
    python tools/make_icon.py
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFilter

# 配色
COLOR_START = (0x66, 0x7E, 0xEA)
COLOR_END = (0x76, 0x4B, 0xA2)
COLOR_REC = (0xFF, 0x3B, 0x4E)
COLOR_WHITE = (0xFF, 0xFF, 0xFF)

SUPERSAMPLE = 4          # 先按 4 倍绘制再缩小，边缘更干净
SIZE = 1024              # 最终母版尺寸（再降采样到各档）
ICO_SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]


def lerp_color(c1, c2, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))


def diagonal_gradient(size, c1, c2):
    """生成从左上到右下的对角线性渐变"""
    big = size * 2
    grad = Image.new('RGB', (big, big))
    draw = ImageDraw.Draw(grad)
    for x in range(big):
        draw.line([(x, 0), (x, big)], fill=lerp_color(c1, c2, x / (big - 1)))
    grad = grad.rotate(-45, resample=Image.BICUBIC, expand=False)
    offset = (big - size) // 2
    return grad.crop((offset, offset, offset + size, offset + size))


def rounded_mask(size, radius):
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def rounded_triangle_mask(size, blur_radius):
    """画一个播放三角，再用『模糊 + 阈值』把尖角磨圆"""
    mask = Image.new('L', (size, size), 0)
    cx, cy = size * 0.485, size * 0.5
    width, height = size * 0.30, size * 0.40
    points = [
        (cx + width * 0.55, cy),
        (cx - width * 0.45, cy - height * 0.5),
        (cx - width * 0.45, cy + height * 0.5),
    ]
    ImageDraw.Draw(mask).polygon(points, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(blur_radius))
    return mask.point(lambda value: 255 if value > 128 else 0)


def build_icon(size):
    """绘制正方形图标（含透明圆角）"""
    canvas = size * SUPERSAMPLE
    icon = diagonal_gradient(canvas, COLOR_START, COLOR_END).convert('RGBA')

    # 圆角裁切
    icon.putalpha(rounded_mask(canvas, int(canvas * 0.225)))

    # 白色播放三角
    triangle = rounded_triangle_mask(canvas, canvas * 0.022)
    white_layer = Image.new('RGBA', (canvas, canvas), COLOR_WHITE + (0,))
    white_layer.putalpha(triangle)
    icon = Image.alpha_composite(icon, white_layer)

    # 右上角红色录制圆点（带白色描边，保证在渐变底上足够醒目）
    draw = ImageDraw.Draw(icon)
    dot_cx, dot_cy = canvas * 0.735, canvas * 0.262
    dot_r = canvas * 0.105
    ring_r = dot_r + canvas * 0.032
    draw.ellipse(
        [dot_cx - ring_r, dot_cy - ring_r, dot_cx + ring_r, dot_cy + ring_r],
        fill=COLOR_WHITE + (255,)
    )
    draw.ellipse(
        [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
        fill=COLOR_REC + (255,)
    )

    return icon.resize((size, size), Image.LANCZOS)


def build_arrow(up=False, size=24):
    """生成下拉框/数字框用的小箭头（chevron），带抗锯齿"""
    scale = 8
    canvas = size * scale
    image = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = (0x9A, 0xA7, 0xBD, 0xFF)
    width = int(canvas * 0.11)
    points = [
        (canvas * 0.28, canvas * (0.63 if up else 0.38)),
        (canvas * 0.5, canvas * (0.37 if up else 0.64)),
        (canvas * 0.72, canvas * (0.63 if up else 0.38)),
    ]
    draw.line(points, fill=color, width=width, joint='curve')
    for point in (points[0], points[-1]):
        r = width / 2
        draw.ellipse([point[0] - r, point[1] - r, point[0] + r, point[1] + r], fill=color)
    return image.resize((size, size), Image.LANCZOS)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    assets_dir = os.path.join(project_dir, 'assets')
    os.makedirs(assets_dir, exist_ok=True)

    master = build_icon(SIZE)
    ico_path = os.path.join(assets_dir, 'icon.ico')
    master.save(ico_path, format='ICO', sizes=ICO_SIZES)

    png_path = os.path.join(assets_dir, 'icon.png')
    master.resize((256, 256), Image.LANCZOS).save(png_path, format='PNG')

    build_arrow(up=False).save(os.path.join(assets_dir, 'arrow_down.png'), format='PNG')
    build_arrow(up=True).save(os.path.join(assets_dir, 'arrow_up.png'), format='PNG')

    print(f'已生成: {ico_path}')
    print(f'已生成: {png_path}')
    print(f'已生成: {os.path.join(assets_dir, "arrow_down.png")}')
    print(f'已生成: {os.path.join(assets_dir, "arrow_up.png")}')


if __name__ == '__main__':
    sys.exit(main())
