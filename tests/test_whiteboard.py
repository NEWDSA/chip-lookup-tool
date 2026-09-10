# -*- coding: utf-8 -*-
"""
tests/test_whiteboard.py
------------------------
手写白板识别功能测试（src/math_canvas.py + ui.py 集成）。

分四组：
    A. 计算层：MathRecognizer.calculate 对各类 LaTeX 的行为（纯离线，无需模型）
    B. 画布层：MathCanvas.get_image 尺寸 / 白底黑字 / 笔画还原 / 空画布判定
    C. 集成层：App 内空画布 → on_error；画线 → get_image 非空；识别中防重入
    D. 精度层：真实 pix2tex 端到端识别（慢，首次加载模型约 60-90s，
              需设置环境变量 WB_SLOW=1 才运行）

运行：
    python tests/test_whiteboard.py            # A/B/C
    set WB_SLOW=1 && python tests/test_whiteboard.py   # 含 D
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
for _p in (ROOT, SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tkinter as tk

from math_canvas import (
    COLOR_CANVAS_BG,
    MathCanvas,
    MathCanvasWithRecognizer,
    MathRecognizer,
)

SLOW = os.environ.get("WB_SLOW") == "1"


def pump(widget, ms: int):
    """跑事件循环约 ms 毫秒，驱动布局与 after 定时器。"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        try:
            widget.update()
        except tk.TclError:
            return
        time.sleep(0.01)


def draw_four(canvas: tk.Canvas, x0: int = 20, y0: int = 30, scale: float = 1.0):
    """在 canvas 上画一个 '4'（三段折线，模拟手写）。"""
    def p(x, y):
        return x0 + x * scale, y0 + y * scale

    a = p(0, 0)
    b = p(0, 50)
    c = p(30, 50)
    d = p(30, 0)
    canvas.create_line(*a, *b, fill="black", width=3)
    canvas.create_line(*b, *c, fill="black", width=3)
    canvas.create_line(*d, *c, fill="black", width=3)


# =====================================================================
# A. 计算层
# =====================================================================
class CalculateLayerTests(unittest.TestCase):
    """MathRecognizer.calculate：sympy 解析与求值。"""

    def setUp(self):
        self.r = MathRecognizer()

    def test_simple_expression(self):
        r"""4 \times 4 应算出 16（回归：曾被格式化成 16.0000000000000）。"""
        out = self.r.calculate(r"4 \times 4")
        self.assertNotIn("error", out, "纯算式不应报错，实际：%s" % out)
        self.assertEqual(out["result"], "16",
                         "浮点尾巴未清理，UI 会显示 16.0000000000000")

    def test_fraction(self):
        r"""\frac{1}{2} + \frac{1}{3} 应给出精确结果 5/6。"""
        out = self.r.calculate(r"\frac{1}{2} + \frac{1}{3}")
        self.assertNotIn("error", out)
        self.assertEqual(out["result"], "5/6", "精确分数不应退化为小数")

    def test_power(self):
        """2^{10} 应算出 1024（回归：曾为 1024.00000000000）。"""
        out = self.r.calculate(r"2^{10}")
        self.assertNotIn("error", out)
        self.assertEqual(out["result"], "1024")

    def test_symbolic_expression(self):
        """含变量表达式 x^2 不应崩溃，能返回表达式。"""
        out = self.r.calculate(r"x^2")
        self.assertNotIn("error", out)
        self.assertIn("expr", out)

    def test_single_letter_symbol_allowed(self):
        """单个字母是合法未知数，不能被噪声过滤器误杀。"""
        out = self.r.calculate(r"x + 1")
        self.assertNotIn("error", out, "单字母变量被误判为噪声")

    def test_greek_letter_allowed(self):
        r"""\alpha 是合法符号，不应被误判为识别噪声。"""
        out = self.r.calculate(r"\alpha + 1")
        self.assertNotIn("error", out, "希腊字母被误判为噪声")

    def test_garbage_command_rejected(self):
        r"""识别噪声（\bigstar 等长名字命令）必须判为失败，不能当结果展示。"""
        out = self.r.calculate(r"\bigstar\bigstar")
        self.assertIn("error", out,
                      "未知长名符号被当成有效结果，会误导用户：%s" % out)

    def test_invalid_latex_returns_error(self):
        """非法 LaTeX 必须走 error 分支，而不是抛异常。"""
        out = self.r.calculate(r"\thisIsNotALatexCommand{{{")
        self.assertIn("error", out, "非法输入应返回 error 字段，实际：%s" % out)

    def test_empty_string_returns_error(self):
        """空字符串应返回 error（parse_latex 无法解析空表达式）。"""
        out = self.r.calculate("")
        self.assertIn("error", out)

    def test_equation_not_supported(self):
        r"""方程 x=2 目前只做 evalf，语义上等价于 True/False，属于已知限制。"""
        out = self.r.calculate(r"x = 2")
        # 记录现状：要么报错，要么给出一个非数值结果——都不算「算出了 x」
        if "error" not in out:
            self.assertNotEqual(out["result"], "2",
                                "方程不应被当作表达式求出 2")


# =====================================================================
# B. 画布层
# =====================================================================
class CanvasLayerTests(unittest.TestCase):
    """MathCanvas.get_image：尺寸、颜色、笔画还原、空判定。"""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.geometry("420x200+50+50")
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def _make(self):
        mc = MathCanvas(self.root, width=400, height=150, stroke_width=3)
        mc.canvas.pack()
        pump(self.root, 150)  # 等画布真正映射，winfo_width 才有真实值
        return mc

    def test_canvas_gets_real_size(self):
        mc = self._make()
        self.assertGreater(mc.canvas.winfo_width(), 1,
                           "画布未映射时 winfo_width()==1，get_image 会产出 1x1 图")
        mc.canvas.destroy()

    def test_image_size_matches_canvas(self):
        mc = self._make()
        img = mc.get_image()
        self.assertEqual(img.size,
                         (mc.canvas.winfo_width(), mc.canvas.winfo_height()))
        mc.canvas.destroy()

    def test_empty_image_is_all_white(self):
        mc = self._make()
        img = mc.get_image()
        colors = set(img.getdata())
        self.assertEqual(colors, {(255, 255, 255)},
                         "空画布必须是纯白，否则空画布检测会失效")
        mc.canvas.destroy()

    def test_strokes_become_black_pixels(self):
        """画线后图像必须出现黑色像素（白底黑字是 pix2tex 的前置要求）。"""
        mc = self._make()
        draw_four(mc.canvas)
        self.root.update()
        img = mc.get_image()
        colors = set(img.getdata())
        self.assertIn((0, 0, 0), colors, "笔画未转成黑色像素，实际颜色：%s"
                      % sorted(colors)[:5])
        self.assertNotEqual(colors, {(255, 255, 255)}, "画线后图像不应仍是纯白")
        mc.canvas.destroy()

    def test_clear_restores_blank(self):
        """clear() 后图像必须回到纯白（回归：旧实现只删 _strokes 登记项）。"""
        mc = self._make()
        draw_four(mc.canvas)
        self.root.update()
        self.assertNotEqual(set(mc.get_image().getdata()), {(255, 255, 255)})
        mc.clear()
        self.root.update()
        self.assertEqual(set(mc.get_image().getdata()), {(255, 255, 255)},
                         "clear() 未彻底清除笔画")
        mc.canvas.destroy()

    def test_clear_removes_untracked_items(self):
        """clear() 必须按 find_all() 清理，而非只删 _strokes 里的 ID。"""
        mc = self._make()
        # 绕过 _on_drag 直接画（模拟任何非拖拽途径上屏的图元）
        mc.canvas.create_line(10, 10, 100, 100, fill="black", width=3)
        self.root.update()
        self.assertEqual(len(mc._strokes), 0)
        mc.clear()
        self.root.update()
        self.assertEqual(mc.canvas.find_all(), (),
                         "画布上仍有残留图元：用户看到已清空，识别却拿到旧笔迹")
        mc.canvas.destroy()

    def test_is_empty_on_blank(self):
        mc = self._make()
        self.assertTrue(mc.is_empty(), "空画布应判定为空")
        mc.canvas.destroy()

    def test_is_empty_after_draw(self):
        mc = self._make()
        draw_four(mc.canvas)
        self.root.update()
        self.assertFalse(mc.is_empty(), "有笔画时不应判定为空")
        mc.canvas.destroy()

    def test_get_image_fallback_when_unmapped(self):
        """画布未映射时 winfo_width()==1，应回退到构造尺寸而非产出 1x1 空图。"""
        mc = MathCanvas(tk.Toplevel(self.root), width=320, height=140)
        # 故意不 pack、不 update：winfo_* 保持 1
        img = mc.get_image()
        self.assertEqual(img.size, (320, 140),
                         "未映射画布未回退到配置尺寸，会被误判为「画布为空」")
        mc.canvas.destroy()

    def test_bg_constant_matches_image_bg(self):
        """常量 COLOR_CANVAS_BG 与实际画布/图像底色一致性（防重构漂移）。"""
        self.assertEqual(COLOR_CANVAS_BG.lower(), "#ffffff")
        mc = self._make()
        self.assertEqual(str(mc.canvas.cget("bg")).lower(), COLOR_CANVAS_BG.lower())
        mc.canvas.destroy()

    def test_mouse_drag_creates_strokes(self):
        """模拟真实鼠标拖拽事件，验证事件绑定链路。"""
        mc = self._make()

        class E:
            def __init__(self, x, y):
                self.x, self.y = x, y

        mc.canvas.event_generate("<ButtonPress-1>", x=10, y=10)
        for x in range(10, 110, 10):
            mc.canvas.event_generate("<B1-Motion>", x=x, y=50)
        mc.canvas.event_generate("<ButtonRelease-1>", x=110, y=50)
        self.root.update()
        self.assertGreater(len(mc._strokes), 0, "鼠标拖拽未产生任何笔画")
        self.assertNotEqual(set(mc.get_image().getdata()), {(255, 255, 255)})
        mc.canvas.destroy()


# =====================================================================
# C. 集成层（不加载模型）
# =====================================================================
class IntegrationLayerTests(unittest.TestCase):
    """MathCanvasWithRecognizer 的回调与防重入。"""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.geometry("460x260+80+80")
        cls.root.update()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def _make(self, on_result=None, on_error=None):
        mw = MathCanvasWithRecognizer(self.root, width=400, height=150,
                                      on_result=on_result, on_error=on_error)
        mw.pack()
        pump(self.root, 150)
        return mw

    def test_empty_canvas_reports_error_without_model(self):
        """空画布点识别：必须立刻回调 on_error，且不得加载模型（不卡 70 秒）。"""
        got = {}
        mw = self._make(on_error=lambda m: got.setdefault("err", m))
        t0 = time.time()
        mw.recognize_async()
        pump(self.root, 300)
        elapsed = time.time() - t0
        self.assertEqual(got.get("err"), "画布为空，请先手写公式")
        self.assertLess(elapsed, 5.0, "空画布判定应在毫秒级完成，实际 %.1fs" % elapsed)
        self.assertFalse(mw._is_recognizing, "空画布不应把状态置为识别中")

    def test_reentrancy_guard(self):
        """识别进行中再次点击应被 _is_recognizing 拦住（不重复起线程）。"""
        mw = self._make()
        mw._is_recognizing = True
        before = len(mw._queue.queue)
        mw.recognize_async()  # 应直接 return
        self.assertEqual(len(mw._queue.queue), before)

    def test_queue_result_dispatch_ok(self):
        """向结果队列塞入 ok 结果，_poll_queue 应回调 on_result。"""
        got = {}
        mw = self._make(on_result=lambda l, r: got.update(latex=l, result=r))
        mw._is_recognizing = True
        mw._queue.put(("ok", r"4 \times 4", {"result": "16", "expr": "16",
                                             "latex_result": "16"}))
        mw._poll_queue()
        self.assertEqual(got.get("latex"), r"4 \times 4")
        self.assertEqual(got["result"]["result"], "16")
        self.assertFalse(mw._is_recognizing, "取到结果后应复位识别中状态")

    def test_queue_result_dispatch_error(self):
        """错误结果应回调 on_error 并复位状态。"""
        got = {}
        mw = self._make(on_error=lambda m: got.setdefault("err", m))
        mw._is_recognizing = True
        mw._queue.put(("error", "boom"))
        mw._poll_queue()
        self.assertEqual(got.get("err"), "boom")
        self.assertFalse(mw._is_recognizing)


# =====================================================================
# D. 精度层（真实模型，慢）
# =====================================================================
@unittest.skipUnless(SLOW, "设置 WB_SLOW=1 才运行真实模型识别")
class AccuracyTests(unittest.TestCase):
    """端到端：手写笔画 → TrOCR → LaTeX → sympy 结果。

    模型是 TrOCR（MathWriting 手写数学数据集微调），对手写输入才是对症的。
    实测 10 组鼠标手写样本语义正确 7/10，单次约 2.4s；换用前的 pix2tex
    是 0/10（它只认印刷体 LaTeX 排版公式）。

    本组只覆盖「最基础的两种输入必须对」，作为换模型后的回归护栏。
    更全面的对比跑 tools/bench_handwriting_models.py。
    """

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.geometry("460x260+80+80")
        cls.root.update()
        cls.r = MathRecognizer()
        cls.r._get_model()  # 预热，避免把 20s+ 加载时间算进单次断言

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def _recognize_canvas(self, draw_fn):
        mw = MathCanvasWithRecognizer(self.root, width=400, height=150)
        mw.pack()
        pump(self.root, 200)
        draw_fn(mw.canvas.canvas)
        self.root.update()
        img = mw.canvas.get_image()
        latex = self.r.recognize(img)
        res = self.r.calculate(latex)
        mw.canvas.canvas.destroy()
        return latex, res

    def test_digit_four(self):
        """单个手写 '4' 应识别出 4（换模型前会被认成 \\bigcup{}）。"""
        latex, _ = self._recognize_canvas(lambda c: draw_four(c))
        print("\n[精度] 单个 4 → %r" % latex)
        self.assertIn("4", latex, "手写 4 未识别出数字 4")

    def test_four_times_four(self):
        """手写 '4x4' 应算出 16（换模型前是 |\\bigstar\\bigstar|）。"""
        def draw(c):
            draw_four(c, 20, 30)
            c.create_line(70, 30, 100, 80, fill="black", width=3)
            c.create_line(100, 30, 70, 80, fill="black", width=3)
            draw_four(c, 120, 30)
        latex, res = self._recognize_canvas(draw)
        print("\n[精度] 4x4 → %r / %s" % (latex, res.get("result", res)))
        self.assertNotIn("error", res, "识别结果无法被 sympy 解析：%s" % res)
        self.assertEqual(res.get("result"), "16")


# =====================================================================
# F. 输出整理层（不加载模型）
# =====================================================================
class TidyTests(unittest.TestCase):
    """MathRecognizer._tidy：把模型输出整理成 sympy 吃得下的 LaTeX。

    模型偶尔把乘号认成字母 x/X，或在符号之间插入 '.' 分隔（'4.X.4'）。
    """

    def test_dot_separators_removed(self):
        self.assertEqual(MathRecognizer._tidy("4.X.4"), r"4\times 4")

    def test_letter_x_between_digits_is_times(self):
        self.assertEqual(MathRecognizer._tidy("4X4"), r"4\times 4")

    def test_star_between_digits_is_times(self):
        self.assertEqual(MathRecognizer._tidy("7*6"), r"7\times 6")

    def test_unicode_operators_normalised(self):
        self.assertEqual(MathRecognizer._tidy("6×7"), r"6\times 7")
        self.assertEqual(MathRecognizer._tidy("8÷2"), r"8\div 2")

    def test_plain_expression_untouched(self):
        self.assertEqual(MathRecognizer._tidy("12+34"), "12+34")

    def test_leading_letter_not_converted(self):
        """字母在数字前面时不是乘号，不能乱替换（x2 是变量 x 乘 2）。"""
        self.assertEqual(MathRecognizer._tidy("x2"), "x2")

    def test_tidied_result_is_calculable(self):
        """整理后的串必须真能算出结果，而不只是字符串长得对。"""
        r = MathRecognizer()
        out = r.calculate("4.X.4")
        self.assertNotIn("error", out, out)
        self.assertEqual(out["result"], "16")


# =====================================================================
# G. 模型定位层（不加载模型）
# =====================================================================
class ModelResolutionTests(unittest.TestCase):
    """权重目录定位：必须命中项目内固化权重，保证打包后不联网。"""

    def test_prefers_bundled_model_dir(self):
        from paths import bundle_root
        r = MathRecognizer()
        src = r._resolve_model_dir()
        expect = os.path.join(bundle_root(), MathRecognizer.MODEL_SUBDIR)
        if os.path.isdir(expect):
            self.assertEqual(src, expect,
                             "应优先用项目内固化权重，而不是走 HF 缓存")
            self.assertTrue(os.path.exists(os.path.join(src, "model.safetensors")))
        else:
            # 权重没拉下来时退回仓库 id，属于预期降级
            self.assertEqual(src, MathRecognizer.MODEL_ID)

    def test_explicit_dir_wins(self):
        r = MathRecognizer(model_dir="/tmp/custom-model")
        self.assertEqual(r._resolve_model_dir(), "/tmp/custom-model")


# =====================================================================
# E. UI 层（不加载模型）
# =====================================================================
class UILayerTests(unittest.TestCase):
    """App 内「识别公式」按钮的交互反馈。"""

    def setUp(self):
        import tempfile as _tf
        from config import MODE_LOCAL, Settings
        from sources import LocalSource
        from ui import App

        self._tmp = _tf.TemporaryDirectory()
        cfg = os.path.join(self._tmp.name, "chiplookup.json")
        local_csv = os.path.join(HERE, "fixtures", "local_sample.csv")
        s = Settings(mode=MODE_LOCAL, csv_path=local_csv, config_path=cfg)
        self.app = App(s, LocalSource(s))
        self.app.geometry("1200x680+40+40")
        pump(self.app, 300)

    def tearDown(self):
        try:
            self.app._cancel_pending_query()
            self.app.destroy()
        except Exception:
            pass
        self._tmp.cleanup()

    def test_recognize_button_gets_busy_state(self):
        """识别进行中重复点击不应改写按钮状态（防重入）。"""
        btn = self.app.btn_recognize
        self.assertEqual(btn.cget("text"), "识别公式")
        self.app.math_canvas._is_recognizing = True
        self.app._start_recognize()
        self.assertEqual(btn.cget("text"), "识别公式",
                         "识别中重复点击不应改写按钮状态")
        self.app.math_canvas._is_recognizing = False

    def test_empty_canvas_click_restores_button(self):
        """空画布点识别：走 on_error 分支后按钮必须复位，不能卡在禁用态。"""
        btn = self.app.btn_recognize
        self.app._start_recognize()
        pump(self.app, 200)
        self.assertEqual(btn.cget("text"), "识别公式", "按钮卡在忙碌态")
        self.assertEqual(str(btn.cget("state")), "normal", "按钮未恢复可用")


if __name__ == "__main__":
    unittest.main(verbosity=2)
