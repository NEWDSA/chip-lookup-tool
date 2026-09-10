# -*- coding: utf-8 -*-
"""排查 pix2tex 识别失败根因：排版质量 / 图像缩放器 / 输入尺寸。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from PIL import Image, ImageDraw, ImageFont
from math_canvas import MathRecognizer

r = MathRecognizer()
model = r._get_model()

FONTS = ["times.ttf", "cambria.ttc", "georgia.ttf", "arial.ttf"]


def render(text, font_name="times.ttf", size=64, italic=False):
    """渲染紧贴文字的公式图（白底黑字），模仿 LaTeX 排版分布。"""
    img = Image.new("RGB", (10, 10), "white")
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(font_name, size)
    bbox = d.textbbox((0, 0), text, font=f)
    w, h = bbox[2] - bbox[0] + 40, bbox[3] - bbox[1] + 40
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    d.text((20 - bbox[0], 20 - bbox[1]), text, fill="black", font=f)
    return img


def try_one(label, img, resize=True):
    try:
        out = model(img, resize=resize)
    except Exception as e:
        out = "EXC: %s" % e
    print("  %-34s resize=%-5s → %r" % (label, resize, out))
    return out


print("=" * 72)
print("[A] 不同字体渲染的 '2+3=5'（size=64，紧贴裁剪）")
for fn in FONTS:
    try:
        try_one(fn, render("2+3=5", fn))
    except Exception as e:
        print("  %-34s 渲染失败: %s" % (fn, e))

print()
print("[B] 不同字号（times）渲染 '2+3=5'")
for size in (32, 48, 64, 96, 128):
    try_one("size=%d" % size, render("2+3=5", "times.ttf", size))

print()
print("[C] 关掉图像缩放器（resize=False）")
for size in (48, 96):
    try_one("2+3=5 size=%d" % size, render("2+3=5", "times.ttf", size), resize=False)

print()
print("[D] 其它表达式（times, 96）")
for t in ["4x4", "4+4", "x^2+1", "12+34", "8-3", "7*6"]:
    try_one(repr(t), render(t, "times.ttf", 96))

print()
print("[E] 手写折线 vs 印刷体，同尺寸对比")
def hand_four():
    img = Image.new("RGB", (300, 140), "white")
    d = ImageDraw.Draw(img)
    d.line([30, 30, 30, 100], fill="black", width=5)
    d.line([30, 100, 80, 100], fill="black", width=5)
    d.line([80, 30, 80, 100], fill="black", width=5)
    return img
try_one("手写折线 4", hand_four())
try_one("印刷体 4", render("4", "times.ttf", 96))
print("=" * 72)
