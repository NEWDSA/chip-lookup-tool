# -*- coding: utf-8 -*-
"""
tests/test_minimal_mode.py
--------------------------
极简模式回归测试：布局切换与动画收尾、偏好持久化、启动恢复、
详情入口兼容（双击/Enter 退出极简，↑↓ 浏览保持极简）、Esc 行为。
全部离线（本地 CSV fixture），直接可运行：

    python tests/test_minimal_mode.py
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

from config import MODE_LOCAL, Settings
from sources import LocalSource
from ui import App

LOCAL_CSV = os.path.join(HERE, "fixtures", "local_sample.csv")


def pump(app, ms: int):
    """跑事件循环约 ms 毫秒（驱动 after 定时器与布局重排）。"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.update()
        time.sleep(0.01)


class MinimalModeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        cfg = os.path.join(self._tmp.name, "chiplookup.json")
        s = Settings(mode=MODE_LOCAL, csv_path=LOCAL_CSV, config_path=cfg)
        self.source = LocalSource(s)
        self.app = App(s, self.source)
        self.app.geometry("1200x680+40+40")
        pump(self.app, 300)  # 等首帧布局稳定（_on_body_configure 已按 30% 设左栏）

    def tearDown(self):
        try:
            self.app._cancel_pending_query()  # 取消输入防抖，避免销毁后 after 回调报错
            self.app.destroy()
        except Exception:
            pass
        self._tmp.cleanup()

    # ---------- 断言辅助 ----------

    def assert_full_mode(self, app: App):
        self.assertFalse(app._minimal_mode)
        self.assertEqual(app.right.winfo_manager(), "grid")
        self.assertEqual(app.sash.winfo_manager(), "grid")
        self.assertEqual(app.body.grid_columnconfigure(0)["weight"], 0)
        self.assertEqual(app.body.grid_columnconfigure(2)["weight"], 1)
        self.assertEqual(app.btn_minimal.cget("text"), "极简模式")
        self.assertGreater(app.right.winfo_width(), 100)

    def assert_minimal_mode(self, app: App):
        self.assertTrue(app._minimal_mode)
        # grid_remove 后 manager 为空串
        self.assertEqual(app.right.winfo_manager(), "")
        self.assertEqual(app.sash.winfo_manager(), "")
        # 列权重全程恒定（极简模式不依赖权重，见 _minimal_finish_enter）
        self.assertEqual(app.body.grid_columnconfigure(0)["weight"], 0)
        self.assertEqual(app.body.grid_columnconfigure(2)["weight"], 1)
        self.assertEqual(app.btn_minimal.cget("text"), "退出极简")
        # 左栏吃满 body 内部宽（widget 宽 = 内部宽 - 自身 padx 7）
        interior = app.body.winfo_width() - 28
        left_w = app.left.winfo_width()
        self.assertAlmostEqual(left_w, interior - 7, delta=3)

    def saved_config(self) -> dict:
        path = self.app.settings.config_path
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ---------- 用例 ----------

    def test_initial_full_mode(self):
        self.assert_full_mode(self.app)

    def test_enter_exit_minimal_animation(self):
        app = self.app
        app._set_minimal_mode(True, animate=True)
        pump(app, 500)  # 动画 ~180ms + 收尾布局
        self.assert_minimal_mode(app)
        # 偏好已持久化
        self.assertTrue(app.settings.minimal_mode)
        self.assertTrue(self.saved_config().get("minimal_mode"))

        # 出极简：右栏恢复、左栏回到默认比例宽
        app._set_minimal_mode(False, animate=True)
        pump(app, 500)
        self.assert_full_mode(app)
        self.assertFalse(app.settings.minimal_mode)
        self.assertFalse(self.saved_config().get("minimal_mode"))
        expected = app._default_pane_width(app.body.winfo_width())
        self.assertAlmostEqual(app.left.winfo_width(), expected, delta=3)

    def test_minimal_window_resize_keeps_full_width(self):
        app = self.app
        app._set_minimal_mode(True, animate=False)
        pump(app, 200)
        app.geometry("1400x680+20+20")
        pump(app, 300)
        self.assert_minimal_mode(app)  # 内含「左栏 = 新内部宽-7」断言

    def test_startup_restore_minimal(self):
        # 独立实例：配置里 minimal_mode=True，启动后应自动进入极简
        cfg = os.path.join(self._tmp.name, "restore.json")
        s = Settings(mode=MODE_LOCAL, csv_path=LOCAL_CSV, config_path=cfg,
                     minimal_mode=True)
        app2 = App(s, LocalSource(s))
        try:
            app2.geometry("1200x680+40+40")
            pump(app2, 400)  # 覆盖 after(80) 的启动恢复回调
            self.assert_minimal_mode(app2)
        finally:
            app2.destroy()

    def _query_candidates(self, app: App):
        app.var_query.set("D8DK")
        app._run_query()
        pump(app, 100)
        if not app._candidates:
            self.skipTest("fixture 未产出候选，跳过交互用例")
        app.tree.selection_set(app.tree.get_children()[0])
        app.tree.focus(app.tree.get_children()[0])
        pump(app, 50)

    def test_double_click_stays_minimal(self):
        app = self.app
        app._set_minimal_mode(True, animate=False)
        pump(app, 150)
        self._query_candidates(app)
        app._on_candidate_activate()  # 双击不再打断极简模式
        pump(app, 400)
        self.assert_minimal_mode(app)
        self.assertTrue(self.saved_config().get("minimal_mode"))

    def test_enter_key_stays_minimal(self):
        app = self.app
        app._set_minimal_mode(True, animate=False)
        pump(app, 150)
        self._query_candidates(app)
        app._on_enter()
        pump(app, 400)
        self.assert_minimal_mode(app)
        self.assertTrue(self.saved_config().get("minimal_mode"))

    def test_search_flow_never_exits_minimal(self):
        """用户报告场景回归：极简模式下 输入搜索→回车→↑↓浏览 全程不退出，
        偏好保持 true（否则重启后"没记住"）。"""
        app = self.app
        app._set_minimal_mode(True, animate=False)
        pump(app, 150)
        app.var_query.set("D8DK")
        app._run_query()      # 输入触发的查询
        pump(app, 100)
        app._on_fuzzy()       # 「搜索」按钮
        pump(app, 100)
        app._on_enter()       # 回车
        pump(app, 100)
        if app._candidates:
            app._move_selection(1)  # ↑↓ 浏览
            pump(app, 100)
        self.assert_minimal_mode(app)
        self.assertTrue(app.settings.minimal_mode)
        self.assertTrue(self.saved_config().get("minimal_mode"))

    def test_arrow_browse_stays_minimal(self):
        app = self.app
        app._set_minimal_mode(True, animate=False)
        pump(app, 150)
        self._query_candidates(app)
        app._move_selection(1)  # ↑↓ 浏览：详情在后台更新，不弹回完整模式
        pump(app, 150)
        self.assert_minimal_mode(app)

    def test_escape_exits_minimal(self):
        app = self.app
        app._set_minimal_mode(True, animate=False)
        pump(app, 150)
        app._on_escape()
        pump(app, 300)
        self.assert_full_mode(app)
        # 完整模式下 Esc 进入极简模式（切换行为），不清空查询
        app.var_query.set("abc")
        app._on_escape()
        self.assertTrue(app._minimal_mode)
        self.assertEqual(app.var_query.get(), "abc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
