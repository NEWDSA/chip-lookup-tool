# -*- coding: utf-8 -*-
"""对比评测手写数学识别模型：TrOCR 系（MathWriting 微调）vs 现有 pix2tex。

用手写折线笔迹（模拟鼠标在 380x150 画布上的输入）作为输入，
比较各模型对「4」「4x4」「2+3」「8-3」等表达式的识别结果。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from PIL import Image, ImageDraw

# ---------------------------------------------------------------- 测试样本
def canvas(w=380, h=150):
    return Image.new("RGB", (w, h), "white")


def glyph(d, ch, x, y, s=1.0, w=3):
    def L(*pts):
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
        L((30, 2), (8, 38), (46, 38)); L((30, 2), (30, 60))
    elif ch == "5":
        L((42, 4), (14, 4), (12, 26), (30, 24), (42, 36), (38, 54), (18, 60), (8, 52))
    elif ch == "7":
        L((10, 6), (44, 6), (24, 60))
    elif ch == "8":
        L((24, 30), (12, 20), (16, 6), (32, 4), (38, 18), (24, 30), (12, 42),
          (16, 58), (32, 60), (40, 46), (24, 30))
    elif ch == "+":
        L((12, 32), (44, 32)); L((28, 14), (28, 50))
    elif ch == "-":
        L((12, 32), (44, 32))
    elif ch == "=":
        L((12, 24), (44, 24)); L((12, 42), (44, 42))
    elif ch == "x":
        L((12, 12), (44, 52)); L((44, 12), (12, 52))
    elif ch == "*":
        L((12, 12), (44, 52)); L((44, 12), (12, 52)); L((28, 4), (28, 60))


def sample(chars, gap=58, s=1.0, w=3):
    img = canvas(40 + len(chars) * gap, 120)
    d = ImageDraw.Draw(img)
    for i, c in enumerate(chars):
        glyph(d, c, 16 + i * gap, 28, s=s, w=w)
    return img


SAMPLES = [
    ("4", ["4"]),
    ("4x4", ["4", "x", "4"]),
    ("2+3", ["2", "+", "3"]),
    ("8-3", ["8", "-", "3"]),
    ("1+1", ["1", "+", "1"]),
    ("12+34", ["1", "2", "+", "3", "4"]),
    ("7x6", ["7", "x", "6"]),
    ("5+5", ["5", "+", "5"]),
    ("9-4", ["9", "-", "4"]),
    ("3x3", ["3", "x", "3"]),
]


# ---------------------------------------------------------------- 评测
def load_trocr(model_id):
    """加载 TrOCR，规避 transformers 5.x 的 meta device 兼容问题。

    TrOCR 的 decoder 用正弦位置编码（use_learned_position_embeddings=false），
    它把结果存在普通张量属性 self.weights 上（不是注册 buffer、也不进
    state_dict）。transformers 5.x 默认在 meta device 上先建模型再灌权重，
    于是 self.weights 是个没有数据的 meta 张量；forward 里
    `if self.weights is None or max_pos > self.weights.size(0)` 的自愈分支
    又因为 meta 张量 shape 正常而不会触发，最终报
    NotImplementedError: Cannot copy out of meta tensor。

    修法：把 meta 的 weights 置 None，走库自带的按需重算分支。
    """
    import torch
    from transformers import TrOCRProcessor, VisionEncoderDecoderModel

    proc = TrOCRProcessor.from_pretrained(model_id, local_files_only=True)
    model = VisionEncoderDecoderModel.from_pretrained(model_id, local_files_only=True)
    model.eval()

    fixed = 0
    for m in model.modules():
        if type(m).__name__ == "TrOCRSinusoidalPositionalEmbedding":
            w = getattr(m, "weights", None)
            if isinstance(w, torch.Tensor) and w.is_meta:
                # 按「原尺寸」重算，不能置 None 交给 forward 惰性重建：
                # 惰性重建只用 padding_idx+1+seq_len 算长度，而 use_cache=True
                # 逐步解码时 position_ids 含 past_key_values_length 偏移，
                # 会越过重建表的上界，报 IndexError: index out of range。
                m.weights = m.get_embedding(w.size(0), m.embedding_dim, m.padding_idx)
                fixed += 1
    if fixed:
        print("  [fix] 重置 %d 处 meta 位置编码，改为按需重算" % fixed)

    # 兜底体检：仍有 meta 张量说明加载有问题，早点报出来
    leftover = [n for n, t in list(model.named_parameters()) + list(model.named_buffers())
                if t.is_meta]
    if leftover:
        raise RuntimeError("仍有 meta 张量未具体化：%s" % leftover[:5])
    return proc, model


def normalize(text):
    """把模型输出归一到 sympy 能解析的表达式。

    模型之间输出风格不统一：tjoab 出 LaTeX（4\\times4），fhswf 出纯文本
    且常把符号间插入 '.' 分隔（4.X.4）。统一成 * / - 之后再比对数值。
    """
    import re
    t = text.strip()
    t = (t.replace("\\times", "*").replace("\\cdot", "*")
          .replace("×", "*").replace("÷", "/")
          .replace("−", "-").replace("–", "-").replace("—", "-")
          .replace("\\div", "/").replace("\\pm", "+"))
    t = t.replace(" ", "").replace("$", "")
    t = t.replace("\\left", "").replace("\\right", "")
    # 去掉夹在字符之间的孤立点（'4.X.4' → '4X4'）
    t = re.sub(r"(?<=[\dA-Za-z])\.(?=[\dA-Za-z])", "", t)
    # 数字之间的 x/X 视为乘号
    t = re.sub(r"(?<=\d)[xX](?=\d)", "*", t)
    t = re.sub(r"(?<=\d)[xX](?=$)", "", t)
    return t


def to_value(text):
    """把识别文本算成数值；算不出来返回 None。"""
    from sympy.parsing.latex import parse_latex
    import sympy

    for cand in (normalize(text), text.strip()):
        if not cand:
            continue
        for fn in (parse_latex, sympy.sympify):
            try:
                v = fn(cand)
            except Exception:
                continue
            try:
                if v.free_symbols:
                    continue
                return float(v.evalf())
            except Exception:
                continue
    return None


EXPECTED = {
    "4": 4, "4x4": 16, "2+3": 5, "8-3": 5, "1+1": 2,
    "12+34": 46, "7x6": 42, "5+5": 10, "9-4": 5, "3x3": 9,
}


def eval_trocr(model_id, use_cache=True, max_new_tokens=32):
    import torch

    print("\n" + "=" * 70)
    print("[TrOCR] %s  (use_cache=%s, max_new_tokens=%d)"
          % (model_id, use_cache, max_new_tokens))
    t0 = time.time()
    proc, model = load_trocr(model_id)
    print("  加载耗时 %.1fs" % (time.time() - t0))

    ok = 0
    times = []
    for name, chars in SAMPLES:
        img = sample(chars)
        t1 = time.time()
        pv = proc(images=img.convert("RGB"), return_tensors="pt").pixel_values
        with torch.no_grad():
            ids = model.generate(pv, max_new_tokens=max_new_tokens,
                                 use_cache=use_cache, num_beams=1, do_sample=False)
        text = proc.batch_decode(ids, skip_special_tokens=True)[0]
        dt = time.time() - t1
        times.append(dt)
        val = to_value(text)
        exp = EXPECTED.get(name)
        hit = "✓" if (val is not None and exp is not None
                      and abs(val - exp) < 1e-6) else "✗"
        if hit == "✓":
            ok += 1
        print("  %-8s → %-24r → %-10s 期望 %-4s %s  (%.2fs)"
              % (name, text, val, exp, hit, dt))
    print("  ---- 语义正确 %d/%d，平均 %.2fs/次 ----"
          % (ok, len(SAMPLES), sum(times) / len(times)))
    return ok


def eval_pix2tex():
    from pix2tex.cli import LatexOCR
    print("\n" + "=" * 70)
    print("[pix2tex] 现有模型（基线）")
    t0 = time.time()
    m = LatexOCR()
    print("  加载耗时 %.1fs" % (time.time() - t0))
    ok = 0
    times = []
    for name, chars in SAMPLES:
        img = sample(chars)
        t1 = time.time()
        try:
            text = m(img)
        except Exception as e:
            text = "EXC: %s" % e
        dt = time.time() - t1
        times.append(dt)
        val = to_value(text)
        exp = EXPECTED.get(name)
        hit = "✓" if (val is not None and exp is not None
                      and abs(val - exp) < 1e-6) else "✗"
        if hit == "✓":
            ok += 1
        print("  %-8s → %-24r → %-10s 期望 %-4s %s  (%.2fs)"
              % (name, text, val, exp, hit, dt))
    print("  ---- 语义正确 %d/%d，平均 %.2fs/次 ----"
          % (ok, len(SAMPLES), sum(times) / len(times)))
    return ok


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    results = {}
    if which in ("all", "pix2tex"):
        results["pix2tex"] = eval_pix2tex()
    if which in ("all", "fhswf"):
        results["fhswf/TrOCR_Math_handwritten"] = eval_trocr("fhswf/TrOCR_Math_handwritten")
    if which in ("all", "tjoab"):
        results["tjoab/latex_finetuned"] = eval_trocr("tjoab/latex_finetuned")
    print("\n" + "=" * 70)
    print("汇总：", results)
