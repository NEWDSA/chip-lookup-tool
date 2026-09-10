# -*- coding: utf-8 -*-
"""
chip_lookup.math_canvas
------------------------
手写数学公式识别模块：
1. Tkinter Canvas 手写板
2. 将 Canvas 内容转换为 PIL Image
3. 调用 pix2tex 识别为 LaTeX
4. 使用 sympy 解析并计算结果
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from typing import Callable, Optional, Tuple

# 配色（与 ui.py 保持一致）
COLOR_CANVAS_BG = "#ffffff"       # 画布背景（白色，pix2tex 需要白底黑字）
COLOR_CANVAS_FG = "#000000"       # 笔迹颜色（黑色）
COLOR_CANVAS_BORDER = "#2a3a4f"   # 边框颜色
COLOR_PANEL = "#16202c"           # 面板背景
COLOR_TEXT = "#e6edf3"            # 主文字
COLOR_ACCENT = "#16c47e"          # 强调绿


class MathCanvas:
    """深色主题手写板，与现有 UI 风格一致"""

    def __init__(
        self,
        parent: tk.Widget,
        width: int = 400,
        height: int = 150,
        stroke_width: int = 3,
    ):
        self.canvas = tk.Canvas(
            parent,
            width=width,
            height=height,
            bg=COLOR_CANVAS_BG,
            highlightthickness=1,
            highlightbackground=COLOR_CANVAS_BORDER,
            cursor="crosshair",
        )
        self._last_x: Optional[int] = None
        self._last_y: Optional[int] = None
        self._stroke_width = stroke_width
        self._strokes: list = []  # 存储所有笔画的 ID

        # 绑定鼠标事件
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

    def _on_press(self, event: tk.Event) -> None:
        """鼠标按下：记录起始点"""
        self._last_x = event.x
        self._last_y = event.y

    def _on_drag(self, event: tk.Event) -> None:
        """鼠标拖动：绘制线条"""
        if self._last_x is not None and self._last_y is not None:
            line_id = self.canvas.create_line(
                self._last_x,
                self._last_y,
                event.x,
                event.y,
                fill=COLOR_CANVAS_FG,
                width=self._stroke_width,
                capstyle=tk.ROUND,
                smooth=True,
            )
            self._strokes.append(line_id)
        self._last_x = event.x
        self._last_y = event.y

    def _on_release(self, event: tk.Event) -> None:
        """鼠标释放：清除起始点"""
        self._last_x = None
        self._last_y = None

    def clear(self) -> None:
        """清空画布

        删除 canvas 上「所有」项目，而不是只删 _strokes 里登记过的 ID。
        get_image() 是按 find_all() 遍历全部项目渲染的，两者必须用同一
        数据源，否则任何非 _on_drag 途径画上去的图元都会残留：用户看到
        画布已空，识别却仍拿到旧笔迹。
        """
        for item in self.canvas.find_all():
            self.canvas.delete(item)
        self._strokes.clear()

    def get_image(self):
        """将 Canvas 内容转换为 PIL Image（白底黑字）

        注意：pix2tex 需要白底黑字的图片，所以这里创建白色背景，
        并用黑色绘制所有笔画。
        """
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            raise ImportError("需要安装 pillow 库：pip install pillow")

        # 创建白色背景图片
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        # 画布尚未映射（未 pack/未 update）时 winfo_* 返回 1，会产出 1x1 的
        # 空图并误判为「画布为空」。回退到构造时声明的尺寸。
        if width <= 1:
            width = int(float(self.canvas.cget("width")))
        if height <= 1:
            height = int(float(self.canvas.cget("height")))
        img = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)

        # 获取所有线条坐标并绘制（遍历所有 canvas 项目）
        for item_id in self.canvas.find_all():
            item_type = self.canvas.type(item_id)
            if item_type == "line":
                coords = self.canvas.coords(item_id)
                # 获取线条宽度
                width_val = self.canvas.itemcget(item_id, "width")
                try:
                    line_width = int(float(width_val))
                except (ValueError, TypeError):
                    line_width = self._stroke_width
                # 每个线条有 4 个坐标点：x1, y1, x2, y2
                for i in range(0, len(coords) - 2, 2):
                    x1, y1, x2, y2 = coords[i], coords[i + 1], coords[i + 2], coords[i + 3]
                    draw.line(
                        [x1, y1, x2, y2],
                        fill="black",
                        width=line_width,
                    )
        return img

    def is_empty(self) -> bool:
        """画布是否为空（纯白）。

        用 getextrema() 判定（每个通道的 min==max==255），O(1) 完成；
        旧实现把整幅图的像素取出来逐个比较，大画布下白白耗时，且依赖
        Pillow 已废弃的 Image.getdata()。
        """
        return self.get_image().getextrema() == ((255, 255), (255, 255), (255, 255))


class MathRecognizer:
    """手写数学识别器：TrOCR 识别 + sympy 计算。

    模型选型（实测见 tools/bench_handwriting_models.py，10 组鼠标手写样本）：

        pix2tex（原方案）          语义正确  0/10   0.5s/次   0.12GB
        tjoab/latex_finetuned      语义正确  7/10   2.7s/次   1.3GB   ← 采用
        fhswf/TrOCR_Math_...       语义正确  8/10   6.3s/次   2.3GB

    pix2tex 训练分布是「印刷体 LaTeX 排版公式」，鼠标手写折线完全在分布外，
    单个手写数字就会被认成 \\bigcup 之类的符号，功能上等于不可用。TrOCR 系
    模型基于 Google MathWriting 手写数据集微调，才是对症的选型。

    这里选 TrOCR-base（tjoab）而非更准的 TrOCR-large（fhswf）：体积只有一半、
    速度快 2.3 倍，准确率仅差 1 组；且它原生输出 LaTeX，能直接喂给 sympy，
    不必为 pure-text 输出额外做归一化。

    换模型只需改 MODEL_SUBDIR / MODEL_ID 两个常量。
    """

    # 权重目录：源码模式取项目根 models/；打包后取 _MEIPASS/models/
    MODEL_SUBDIR = os.path.join("models", "trocr-math")
    MODEL_ID = "tjoab/latex_finetuned"

    # 生成参数：use_cache 必须开。模型自带 config 里 use_cache=false，
    # 逐 token 重算整个 decoder，实测慢 3 倍以上。
    MAX_NEW_TOKENS = 32

    def __init__(self, model_dir: Optional[str] = None):
        self._model = None  # (processor, model)，延迟加载
        self._model_dir = model_dir
        self._lock = threading.Lock()

    # ---------------- 模型定位与加载 ----------------

    def _resolve_model_dir(self) -> str:
        """按「项目内固化权重 → HF 缓存」顺序定位模型目录。"""
        if self._model_dir:
            return self._model_dir
        from paths import bundle_root
        local = os.path.join(bundle_root(), self.MODEL_SUBDIR)
        if os.path.isdir(local) and os.path.exists(
                os.path.join(local, "model.safetensors")):
            return local
        return self.MODEL_ID  # 退回仓库 id，由 huggingface_hub 解析缓存

    @staticmethod
    def _fix_meta_position_embeddings(model) -> int:
        """修掉 transformers 5.x 下 TrOCR 的正弦位置编码 meta 张量问题。

        TrOCR 的 decoder 用正弦位置编码（use_learned_position_embeddings=false），
        结果存在普通张量属性 ``self.weights`` 上——既不注册成 buffer，也不进
        state_dict。transformers 5.x 默认先在 meta device 上建模型再灌权重，
        于是 ``self.weights`` 是个没有数据的 meta 张量；forward 里
        ``if self.weights is None or max_pos > self.weights.size(0)`` 的自愈
        分支又因为 meta 张量的 shape 正常而不会触发，最后报
        ``NotImplementedError: Cannot copy out of meta tensor``。

        修法：按原尺寸把正弦表重算一遍。注意不能简单置 None 交给 forward
        惰性重建——那样表长只有 ``padding_idx + 1 + seq_len``，而
        ``use_cache=True`` 逐步解码时 position_ids 带 past 偏移，会越界
        报 ``IndexError: index out of range``。
        """
        import torch

        fixed = 0
        for m in model.modules():
            if type(m).__name__ == "TrOCRSinusoidalPositionalEmbedding":
                w = getattr(m, "weights", None)
                if isinstance(w, torch.Tensor) and w.is_meta:
                    m.weights = m.get_embedding(w.size(0), m.embedding_dim,
                                                m.padding_idx)
                    fixed += 1
        return fixed

    def _get_model(self):
        """延迟加载模型（首次调用时加载，避免启动卡顿）"""
        if self._model is None:
            with self._lock:
                if self._model is None:  # 双重检查锁定
                    try:
                        from transformers import (TrOCRProcessor,
                                                  VisionEncoderDecoderModel)
                    except ImportError:
                        raise ImportError(
                            "白板识别需要 transformers 与 torch：\n"
                            "    pip install torch transformers")
                    src = self._resolve_model_dir()
                    local_only = os.path.isdir(src)
                    processor = TrOCRProcessor.from_pretrained(
                        src, local_files_only=local_only)
                    model = VisionEncoderDecoderModel.from_pretrained(
                        src, local_files_only=local_only)
                    model.eval()
                    self._fix_meta_position_embeddings(model)
                    self._model = (processor, model)
        return self._model

    # ---------------- 识别 ----------------

    def recognize(self, image) -> str:
        """识别图片中的手写公式，返回 LaTeX 字符串"""
        import torch

        processor, model = self._get_model()
        pixel_values = processor(
            images=image.convert("RGB"), return_tensors="pt").pixel_values
        with torch.no_grad():
            ids = model.generate(
                pixel_values,
                max_new_tokens=self.MAX_NEW_TOKENS,
                use_cache=True,
                num_beams=1,
                do_sample=False,
            )
        return processor.batch_decode(ids, skip_special_tokens=True)[0].strip()

    # 希腊字母等合法多字符符号：parse_latex 会把 \alpha 解析成 Symbol('alpha')，
    # 不能和「识别噪声」一概而论。
    _LEGIT_SYMBOLS = frozenset({
        "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta",
        "eta", "theta", "vartheta", "iota", "kappa", "lambda", "mu", "nu",
        "xi", "pi", "varpi", "rho", "varrho", "sigma", "varsigma", "tau",
        "upsilon", "phi", "varphi", "chi", "psi", "omega",
        "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon",
        "Phi", "Psi", "Omega", "infty", "infinity",
    })

    # 模型偶尔把乘号认成字母 x/X，或在符号之间插入 '.' 分隔（如 '4.X.4'）
    _TIDY_PATTERNS = (
        (r"(?<=[\dA-Za-z])\.(?=[\dA-Za-z])", ""),   # 4.X.4 → 4X4
        (r"(?<=\d)[xX](?=\d)", r"\\times "),        # 4X4   → 4\times 4
        (r"(?<=\d)\s*[*]\s*(?=\d)", r"\\times "),   # 4*4   → 4\times 4
    )

    @classmethod
    def _tidy(cls, text: str) -> str:
        """把识别结果整理成 sympy 更容易吃下的 LaTeX。

        只做「明确无歧义」的替换：数字之间的 x/X/* 一定是乘号，
        夹在字符之间的孤立点一定是分隔噪声。不动其它内容。
        """
        import re
        out = text.strip()
        out = out.replace("×", r"\times ").replace("÷", r"\div ")
        out = out.replace("−", "-").replace("–", "-")
        for pat, rep in cls._TIDY_PATTERNS:
            out = re.sub(pat, rep, out)
        return out

    def calculate(self, latex_str: str) -> dict:
        """解析 LaTeX 并计算结果"""
        try:
            from sympy.parsing.latex import parse_latex
            from sympy import latex, simplify

            cleaned = self._tidy(latex_str)
            try:
                expr = parse_latex(cleaned)
            except Exception:
                expr = parse_latex(latex_str)  # 整理过头了就用原文
            if expr is None:
                return {"error": "无法解析为数学表达式"}

            # 识别噪声防护：模型认错时会吐出 \bigstar 之类命令，
            # parse_latex 不报错而是静默生成多字符 Symbol（如 bigstar、
            # thisIsNotALatexCommand），若原样当「结果」展示会严重误导用户。
            noise = {str(s) for s in expr.free_symbols
                     if len(str(s)) > 2 and str(s) not in self._LEGIT_SYMBOLS}
            if noise:
                return {"error": "未能识别出有效的数学公式（疑似噪声：%s）"
                                 % "、".join(sorted(noise))}

            simplified = simplify(expr)
            if simplified.is_number:
                # 精确形式优先：避免 4×4 显示成 16.0000000000000
                result_str = str(simplified)
            else:
                result_str = str(simplified.evalf())

            return {
                "latex": latex_str,
                "expr": str(simplified),
                "result": result_str,
                "latex_result": latex(simplified),
            }
        except Exception as e:
            return {"error": str(e)}


class MathCanvasWithRecognizer:
    """集成手写板和识别器的组合类"""

    def __init__(
        self,
        parent: tk.Widget,
        width: int = 400,
        height: int = 150,
        on_result: Optional[Callable[[str, dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        """
        参数:
            parent: 父窗口
            width: 画布宽度
            height: 画布高度
            on_result: 识别成功回调 (latex_str, result_dict)
            on_error: 识别失败回调 (error_message)
        """
        self.parent = parent
        self.on_result = on_result
        self.on_error = on_error

        # 创建手写板
        self.canvas = MathCanvas(parent, width=width, height=height)

        # 创建识别器
        self.recognizer = MathRecognizer()

        # 结果队列（用于线程间通信）
        self._queue: queue.Queue = queue.Queue()
        self._is_recognizing = False
        self._polling = False

    def pack(self, **kwargs) -> None:
        """打包画布"""
        self.canvas.canvas.pack(**kwargs)

    def grid(self, **kwargs) -> None:
        """网格布局画布"""
        self.canvas.canvas.grid(**kwargs)

    def clear(self) -> None:
        """清空画布"""
        self.canvas.clear()

    def recognize_async(self) -> None:
        """异步识别手写公式"""
        if self._is_recognizing:
            return  # 正在识别中

        # 获取画布图像
        image = self.canvas.get_image()

        # 检查画布是否为空（全白）
        if self.canvas.is_empty():
            if self.on_error:
                self.on_error("画布为空，请先手写公式")
            return

        self._is_recognizing = True
        self._polling = False

        def _recognize():
            try:
                latex_str = self.recognizer.recognize(image)
                result = self.recognizer.calculate(latex_str)
                self._queue.put(("ok", latex_str, result))
            except Exception as e:
                self._queue.put(("error", str(e)))

        threading.Thread(target=_recognize, daemon=True).start()
        self._poll_queue()

    def _poll_queue(self) -> None:
        """轮询识别结果"""
        self._polling = True
        
        try:
            while True:
                item = self._queue.get_nowait()
                self._is_recognizing = False
                self._polling = False
                if item[0] == "ok":
                    latex_str, result = item[1], item[2]
                    if self.on_result:
                        self.on_result(latex_str, result)
                else:
                    if self.on_error:
                        self.on_error(item[1])
                return
        except queue.Empty:
            root = self.parent.winfo_toplevel()
            root.after(50, self._poll_queue)
