# -*- coding: utf-8 -*-
"""
tests/test_window_size.py
-------------------------
窗口尺寸功能回归：默认尺寸、手动调整持久化、下次启动恢复、
越界收敛与非法值回落。全部离线，直接可运行：

    python tests/test_window_size.py
"""
from __future__ import annotations

import json
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

from config import MODE_LOCAL, Settings, load_config_file
from sources import LocalSource
from ui import App

LOCAL_CSV = os.path.join(HERE, "fixtures", "local_sample.csv")


def pump(app, ms):
    """跑事件循环约 ms 毫秒（驱动 after 定时器与布局重排）。"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.update()
        time.sleep(0.01)


def make_settings(cfg_path, **kw) -> Settings:
    """等价于真实启动路径 build_settings：构造后从配置文件回填。"""
    base = dict(mode=MODE_LOCAL, csv_path=LOCAL_CSV, config_path=cfg_path)
    base.update(kw)
    s = Settings(**base)
    s.apply_dict(load_config_file(cfg_path))
    return s


class WindowSizeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(self._tmp.name, "chiplookup.json")

    def tearDown(self):
        self._tmp.cleanup()

    def saved_config(self) -> dict:
        with open(self.cfg, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_default_size_when_never_saved(self):
        """从未手动调整：使用默认尺寸，且不产生配置文件。"""
        s = make_settings(self.cfg)
        self.assertIsNone(s.window_size)
        app = App(s, LocalSource(s))
        try:
            pump(app, 800)  # 覆盖 600ms 就绪门槛
            self.assertEqual(app.winfo_width(), 1040)
            self.assertEqual(app.winfo_height(), 720)
            self.assertFalse(os.path.exists(self.cfg),
                             "未手动调整过不应写配置文件")
        finally:
            app.destroy()

    def test_resize_persisted_and_restored(self):
        """手动调整 → 防抖持久化 → 新实例启动恢复。"""
        s = make_settings(self.cfg)
        app = App(s, LocalSource(s))
        try:
            pump(app, 800)            # 布局稳定 + 尺寸跟踪就绪
            app.geometry("1150x780")  # 模拟用户拖拽调整（终态）
            pump(app, 1200)           # 防抖 500ms + 写盘 + 布局
            self.assertEqual(self.saved_config().get("window_size"), "1150x780")
        finally:
            app.destroy()

        s2 = make_settings(self.cfg)
        app2 = App(s2, LocalSource(s2))
        try:
            pump(app2, 900)
            self.assertEqual(app2.winfo_width(), 1150)
            self.assertEqual(app2.winfo_height(), 780)
        finally:
            app2.destroy()

    def test_size_clamped_to_minsize(self):
        """保存的尺寸小于 minsize 时收敛到 1000x680。"""
        s = make_settings(self.cfg, window_size="500x400")
        app = App(s, LocalSource(s))
        try:
            pump(app, 800)
            self.assertEqual(app.winfo_width(), 1000)
            self.assertEqual(app.winfo_height(), 680)
        finally:
            app.destroy()

    def test_invalid_window_size_ignored(self):
        """非法格式：validate 置回 None 并告警，UI 回落默认尺寸。"""
        s = make_settings(self.cfg, window_size="abc")
        s.validate()
        self.assertIsNone(s.window_size)
        self.assertTrue(any("窗口尺寸" in w for w in s.warnings))
        app = App(s, LocalSource(s))
        try:
            pump(app, 800)
            self.assertEqual(app.winfo_width(), 1040)
            self.assertEqual(app.winfo_height(), 720)
        finally:
            app.destroy()

    def test_config_roundtrip(self):
        """window_size 随整份配置 save/apply_dict 往返不丢。"""
        s = make_settings(self.cfg, window_size="1150x780")
        s.save()
        s2 = make_settings(self.cfg)
        self.assertEqual(s2.window_size, "1150x780")

    # ---------------- DPI 缩放语义 ----------------
    # 模拟 125% 缩放（monkeypatch _compute_ui_scale）：窗口物理尺寸 = 逻辑 × 1.25，
    # 配置存逻辑像素，跨缩放比例恢复视觉大小一致。

    def test_dpi_default_window_scaled(self):
        """DPI 125%：默认窗口物理尺寸 = 逻辑 1040x720 × 1.25（受屏幕/minsize 收敛）。"""
        s = make_settings(self.cfg)
        orig = App._compute_ui_scale
        App._compute_ui_scale = lambda self: 1.25  # type: ignore[method-assign]
        try:
            app = App(s, LocalSource(s))
            try:
                pump(app, 800)
                ms_w, ms_h = app.minsize()
                exp_w = max(ms_w, min(int(1040 * 1.25), app.winfo_screenwidth()))
                # 高度收敛预留标题栏/任务栏余量（逻辑 60），矮屏下回落 minsize
                exp_h = max(ms_h, min(int(720 * 1.25),
                                      app.winfo_screenheight() - app._s(60)))
                self.assertEqual(app.winfo_width(), exp_w)
                self.assertEqual(app.winfo_height(), exp_h)
            finally:
                app.destroy()
        finally:
            App._compute_ui_scale = orig

    def test_dpi_save_stores_logical(self):
        """DPI 125%：物理尺寸写入配置时 ÷1.25 落为逻辑值。

        本环境屏幕（逻辑 864 高）容不下 850 物理高 + 标题栏，任何更矮请求
        都会被 Tk 的 minsize 强制抬回 850——故高度落点确定为 850（÷1.25 = 680），
        宽度 1500 正常生效（÷1.25 = 1200）。
        """
        s = make_settings(self.cfg)
        orig = App._compute_ui_scale
        App._compute_ui_scale = lambda self: 1.25  # type: ignore[method-assign]
        try:
            app = App(s, LocalSource(s))
            try:
                pump(app, 800)
                app.geometry("1500x900")
                pump(app, 1200)  # 防抖 500ms + 写盘
                self.assertEqual(self.saved_config().get("window_size"),
                                 "1200x680")
            finally:
                app.destroy()
        finally:
            App._compute_ui_scale = orig

    def test_dpi_restore_from_logical(self):
        """DPI 125%：配置逻辑 960x720 → 启动物理 1200x900（受 minsize/屏幕收敛）。"""
        s = make_settings(self.cfg, window_size="960x720")
        orig = App._compute_ui_scale
        App._compute_ui_scale = lambda self: 1.25  # type: ignore[method-assign]
        try:
            app = App(s, LocalSource(s))
            try:
                pump(app, 800)
                ms_w, ms_h = app.minsize()
                exp_w = max(ms_w, min(int(960 * 1.25), app.winfo_screenwidth()))
                exp_h = max(ms_h, min(int(720 * 1.25),
                                      app.winfo_screenheight() - app._s(60)))
                self.assertEqual(app.winfo_width(), exp_w)
                self.assertEqual(app.winfo_height(), exp_h)
            finally:
                app.destroy()
        finally:
            App._compute_ui_scale = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
