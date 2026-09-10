# -*- coding: utf-8 -*-
"""验证：预处理（加粗/放大/留白/居中）能否把手写笔迹拉回 pix2tex 的识别范围。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from PIL import Image, ImageDraw, ImageFilter
from math_canvas import MathRecognizer

r = MathRecognizer()
model = r._get_model()


def blank(w=300, h=120):
    return Image.new("RGB", (w, h), "white")


def draw_digit(img, d, ch, x, y, s=1.0, w=3):
    """用折线画一个手写数字（模拟真人笔迹的控制点）。"""
    def L(*pts):
        coords = [x + px * s for px, py in pts] + [y + py * s for px, py in pts]
        flat = []
        for px, py in pts:
            flat.extend([x + px * s, y + py * s])
        d.line(flat, fill="black", width=w, joint="curve")
    if ch == "1":
        L((20, 0), (35, 10), (35, 60))
    elif ch == "2":
        L((10, 15), (20, 2), (38, 4), (42, 18), (30, 34), (10, 58), (45, 58))
    elif ch == "3":
        L((12, 8), (30, 2), (40, 12), (28, 26), (40, 34), (44, 50), (28, 60), (10, 54))
    elif ch == "4":
        L((30, 2), (8, 38), (46, 38))
        L((30, 2), (30, 60))
    elif ch == "5":
        L((42, 4), (14, 4), (12, 26), (30, 24), (42, 36), (38, 54), (18, 60), (8, 52))
    elif ch == "8":
        L((24, 30), (12, 20), (16, 6), (32, 4), (38, 18), (24, 30), (12, 42),
          (16, 58), (32, 60), (40, 46), (24, 30))
    elif ch == "+":
        L((12, 32), (44, 32)); L((28, 14), (28, 50))
    elif ch == "-":
        L((12, 32), (44, 32))
    elif ch == "x":
        L((12, 12), (44, 52)); L((44, 12), (12, 52))


def compose(chars, gap=62, s=1.0, w=3):
    img = blank(40 + len(chars) * gap, 120)
    d = ImageDraw.Draw(img)
    for i, c in enumerate(chars):
        draw_digit(img, d, c, 20 + i * gap, 30, s=s, w=w)
    return img


def preprocess(img, scale=4, thicken=1, pad_ratio=0.15):
    """放大 + 膨胀加粗 + 留白（模拟印刷体笔画粗细与版心）。"""
    w, h = img.size
    big = img.resize((w * scale, h * scale), Image.LANCZOS)
    for _ in range(thicken):
        big = big.filter(ImageFilter.MaxFilter(5))  # 白底黑字：MaxFilter 让黑变粗
    bbox = Image.eval(big.convert("L"), lambda p: 255 - p).getbbox()
    if bbox:
        big = big.crop(bbox)
    pw = int(big.size[1] * pad_ratio)
    out = Image.new("RGB", (big.size[0] + pw * 2, big.size[1] + pw * 2), "white")
    out.paste(big, (pw, pw))
    return out


def rec(label, img):
    try:
        out = model(img)
    except Exception as e:
        out = "EXC: %s" % e
    print("  %-40s → %r" % (label, out))
    return out


print("=" * 78)
print("[A] 手写折线，原始（宽 3，画布 300x120）")
for chars in [["1"], ["4"], ["2", "+", "3"], ["4", "x", "4"], ["8", "-", "3"]]:
    rec("原始 " + "".join(chars), compose(chars))

print()
print("[B] 手写折线 + 预处理（4x 放大 + 膨胀加粗 + 留白）")
for chars in [["1"], ["4"], ["2", "+", "3"], ["4", "x", "4"], ["8", "-", "3"]]:
    rec("预处理 " + "".join(chars), preprocess(compose(chars)))

print()
print("[C] 粗笔迹手写（width=8）+ 预处理")
for chars in [["4"], ["2", "+", "3"], ["8", "-", "3"]]:
    rec("粗笔 " + "".join(chars), preprocess(compose(chars, w=8)))

print()
print("[D] 大尺寸手写（s=2.0, gap=110）+ 预处理")
for chars in [["2", "+", "3"], ["8", "-", "3"]]:
    rec("大尺寸 " + "".join(chars),
        preprocess(compose(chars, gap=110, s=2.0, w=6)))
print("=" * 78)
