# -*- coding: utf-8 -*-
"""测试 TrOCR-large 的体积压缩方案：fp16 与 int8 动态量化。"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import torch
from bench_handwriting_models import (EXPECTED, SAMPLES, load_trocr, sample,
                                      to_value)

MODEL = "fhswf/TrOCR_Math_handwritten"


def run(proc, model, label, dtype=None):
    ok, times = 0, []
    print("\n" + "=" * 66)
    print("[%s]" % label)
    for name, chars in SAMPLES:
        img = sample(chars)
        pv = proc(images=img.convert("RGB"), return_tensors="pt").pixel_values
        if dtype is not None:
            pv = pv.to(dtype)
        t1 = time.time()
        with torch.no_grad():
            ids = model.generate(pv, max_new_tokens=32, use_cache=True,
                                 num_beams=1, do_sample=False)
        dt = time.time() - t1
        times.append(dt)
        text = proc.batch_decode(ids, skip_special_tokens=True)[0]
        val = to_value(text)
        exp = EXPECTED.get(name)
        hit = "✓" if (val is not None and exp is not None
                      and abs(val - exp) < 1e-6) else "✗"
        if hit == "✓":
            ok += 1
        print("  %-8s → %-22r → %-8s %s (%.2fs)" % (name, text, val, hit, dt))
    print("  ---- 正确 %d/%d，平均 %.2fs ----" % (ok, len(SAMPLES), sum(times)/len(times)))
    return ok


def model_size_mb(m):
    total = 0
    for p in m.parameters():
        total += p.numel() * p.element_size()
    return total / 1048576


proc, model = load_trocr(MODEL)

print("\n参数体积(fp32): %.0f MB" % model_size_mb(model))

base = run(proc, model, "fp32 基线")

try:
    model16 = load_trocr(MODEL)[1].half()
    print("\n参数体积(fp16): %.0f MB" % model_size_mb(model16))
    run(proc, model16, "fp16", dtype=torch.float16)
except Exception as e:
    print("\n[fp16] 失败:", type(e).__name__, e)

try:
    q = torch.quantization.quantize_dynamic(
        load_trocr(MODEL)[1], {torch.nn.Linear}, dtype=torch.qint8)
    run(proc, q, "int8 动态量化")
except Exception as e:
    print("\n[int8] 失败:", type(e).__name__, e)
