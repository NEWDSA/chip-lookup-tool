# -*- coding: utf-8 -*-
"""
tools/fetch_models.py
---------------------
把白板（手写数学公式识别）所需的 TrOCR 模型权重下载并固化到
``models/trocr-math/``，供 PyInstaller 打包进 exe。

为什么单独放 models/ 而不是塞进 src/：
    权重约 1.3 GB，不适合进 git（已在 .gitignore 忽略）。构建前跑一次
    本脚本即可；运行期完全不联网。

用法：
    python tools/fetch_models.py            # 下载到 models/trocr-math/
    python tools/fetch_models.py --check    # 只检查本地是否已就绪
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "models", "trocr-math")

# 选型依据见 tools/bench_handwriting_models.py 的实测对比：
#   pix2tex（原模型）       语义正确 0/10
#   tjoab/latex_finetuned  语义正确 7/10，2.7s/次，1.3GB  ← 采用
#   fhswf/TrOCR_Math_...   语义正确 8/10，6.3s/次，2.3GB
MODEL_ID = "tjoab/latex_finetuned"

# 推理只需要这些文件；.gitattributes / README 之类不拉
ALLOW = ["*.json", "*.txt", "*.safetensors", "*.bin", "*.model"]

REQUIRED = ["config.json", "model.safetensors", "preprocessor_config.json",
            "tokenizer.json", "tokenizer_config.json", "vocab.json",
            "merges.txt", "special_tokens_map.json", "generation_config.json"]


def is_ready() -> bool:
    return all(os.path.exists(os.path.join(DEST, f)) for f in REQUIRED)


def fetch(force: bool = False) -> str:
    if is_ready() and not force:
        print("[skip] 模型已就绪：%s" % DEST)
        return DEST

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        sys.exit("需要 huggingface_hub：pip install huggingface_hub")

    os.makedirs(DEST, exist_ok=True)
    print("[1/2] 下载 %s ..." % MODEL_ID)
    src = snapshot_download(MODEL_ID, allow_patterns=ALLOW)

    print("[2/2] 拷贝到 %s ..." % DEST)
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(DEST, name)
        if not os.path.isfile(s):
            continue
        if os.path.exists(d) and os.path.getsize(d) == os.path.getsize(s):
            continue
        shutil.copyfile(s, d)
        print("    %s" % name)

    missing = [f for f in REQUIRED if not os.path.exists(os.path.join(DEST, f))]
    if missing:
        sys.exit("下载不完整，缺失：%s" % missing)

    total = sum(os.path.getsize(os.path.join(DEST, f))
                for f in os.listdir(DEST)
                if os.path.isfile(os.path.join(DEST, f)))
    print("\n完成：%s（%.0f MB）" % (DEST, total / 1048576))
    return DEST


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查本地是否就绪")
    ap.add_argument("--force", action="store_true", help="强制重新下载")
    a = ap.parse_args()
    if a.check:
        print("就绪" if is_ready() else "未就绪：%s" % DEST)
        sys.exit(0 if is_ready() else 1)
    fetch(force=a.force)
