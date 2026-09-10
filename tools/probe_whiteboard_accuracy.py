# -*- coding: utf-8 -*-
"""对照实验：pix2tex 对「印刷体」vs「鼠标手写折线」的识别能力差异。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from PIL import Image, ImageDraw, ImageFont
from math_canvas import MathRecognizer

r = MathRecognizer()


def printed(text, fontsize=48, w=400, h=140):
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    for name in ("cambria.ttc", "times.ttf", "arial.ttf", "consola.ttf"):
        try:
            f = ImageFont.truetype(name, fontsize)
            break
        except Exception:
            f = None
    if f is None:
        f = ImageFont.load_default()
    d.text((20, 40), text, fill="black", font=f)
    return img


def handwritten_four(x0=20, y0=30, s=1.0):
    img = Image.new("RGB", (400, 140), "white")
    d = ImageDraw.Draw(img)
    d.line([x0, y0, x0, y0 + 50 * s], fill="black", width=4)
    d.line([x0, y0 + 50 * s, x0 + 30 * s, y0 + 50 * s], fill="black", width=4)
    d.line([x0 + 30 * s, y0, x0 + 30 * s, y0 + 50 * s], fill="black", width=4)
    return img


def handwritten_plus():
    img = Image.new("RGB", (400, 140), "white")
    d = ImageDraw.Draw(img)
    d.line([40, 60, 90, 60], fill="black", width=4)
    d.line([65, 35, 65, 85], fill="black", width=4)
    return img


cases = [
    ("印刷体 4x4 (ASCII)", printed("4x4")),
    ("印刷体 4+4 (ASCII)", printed("4+4")),
    ("印刷体 2+3=5", printed("2+3=5")),
    ("印刷体 x^2+1", printed("x^2+1")),
    ("手写折线 4", handwritten_four()),
    ("手写折线 4 (放大)", handwritten_four(20, 20, 1.8)),
    ("手写折线 +", handwritten_plus()),
]

print("=" * 62)
for label, img in cases:
    try:
        out = r.recognize(img)
    except Exception as e:
        out = "EXC: %s" % e
    calc = r.calculate(out) if not out.startswith("EXC") else {}
    res = calc.get("result", calc.get("error", "-"))
    print("%-22s → %-28r | 结果: %s" % (label, out, res))
print("=" * 62)
