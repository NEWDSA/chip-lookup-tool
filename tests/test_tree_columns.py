# -*- coding: utf-8 -*-
"""
tests/test_tree_columns.py
--------------------------
候选表列宽三段式分配回归：常规 / 缺口 / 极窄三种宽度场景，以及 DPI 缩放。
核心保障：料号列（查询工具的核心数据）在任何宽度下都不被挤到不可读。

    python tests/test_tree_columns.py
"""
from __future__ import annotations

import os
import sys
import tempfile
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


def make_settings(cfg_path, **kw) -> Settings:
    """等价于真实启动路径 build_settings：构造后从配置文件回填。"""
    base = dict(mode=MODE_LOCAL, csv_path=LOCAL_CSV, config_path=cfg_path)
    base.update(kw)
    s = Settings(**base)
    s.apply_dict(load_config_file(cfg_path))
    return s


class TreeColumnTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(self._tmp.name, "chiplookup.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _app(self, ui_scale=1.0) -> App:
        # 注意：必须保存/还原原方法。此前用 del「恢复」——但类属性已被 lambda
        # 覆盖，del 会把真实方法一并删掉，污染同进程后续所有测试文件
        # （unittest discover 按字母序全量跑时 test_window_size 全军覆没）。
        orig = App._compute_ui_scale
        App._compute_ui_scale = lambda self: ui_scale  # type: ignore[method-assign]
        try:
            s = make_settings(self.cfg)
            return App(s, LocalSource(s))
        finally:
            App._compute_ui_scale = orig

    def _widths(self, app) -> tuple:
        return tuple(app.tree.column(c, "width") for c in app._TREE_COLS)

    def _avail(self, app, width: int) -> int:
        """_fit_tree_columns 的实际可用宽：入参扣除内边距（与实现一致）。"""
        return width - app._s(4)

    def test_normal_fit_prefers_part_number(self):
        """常规宽度（≥ ΣPREFERRED）：PREFERRED 起步按权重加宽，料号列最大。"""
        app = self._app()
        try:
            app._fit_tree_columns(500)
            widths = self._widths(app)
            self.assertEqual(sum(widths), self._avail(app, 500),
                             "五列总宽必须严格铺满可视宽")
            self.assertGreaterEqual(widths[0],
                                    app._TREE_COL_PREFERRED["part_number"])
            self.assertEqual(widths[0], max(widths), "料号列应为最宽列")
        finally:
            app.destroy()

    def test_deficit_case_part_number_not_squeezed(self):
        """缺口宽度（288px，旧版料号列只剩 24px 的场景）：料号 ≥ 硬底线且最宽。"""
        app = self._app()
        try:
            app._fit_tree_columns(288)
            widths = self._widths(app)
            self.assertEqual(sum(widths), self._avail(app, 288))
            floor = app._TREE_COL_FLOOR
            for c, w in zip(app._TREE_COLS, widths):
                self.assertGreaterEqual(w, min(floor[c], 20),
                                        f"{c} 列低于硬底线")
            self.assertGreaterEqual(widths[0], floor["part_number"],
                                    "料号列不得低于硬底线（旧版此场景仅 24px）")
            self.assertEqual(widths[0], max(widths))
        finally:
            app.destroy()

    def test_extreme_narrow_proportional(self):
        """极窄（< ΣFLOOR）：按硬底线等比压缩，总宽铺满、料号保持最大占比。"""
        app = self._app()
        try:
            app._fit_tree_columns(200)
            widths = self._widths(app)
            self.assertEqual(sum(widths), self._avail(app, 200))
            self.assertGreaterEqual(widths[0], 60, "料号列等比压缩后仍应最大")
            self.assertEqual(widths[0], max(widths))
        finally:
            app.destroy()

    def test_dpi_scaled_deficit(self):
        """DPI 125%：宽度常量整体 ×1.25，缺口分配在缩放后同样成立。"""
        app = self._app(ui_scale=1.25)
        try:
            floor = app._col_metrics()[1]
            self.assertEqual(floor["part_number"], 110)  # 88 × 1.25
            app._fit_tree_columns(360)  # 缩放后的「288px 场景」
            widths = self._widths(app)
            self.assertEqual(sum(widths), self._avail(app, 360))
            self.assertGreaterEqual(widths[0], floor["part_number"])
            self.assertEqual(widths[0], max(widths))
        finally:
            app.destroy()

    def test_custom_widths_floor_clamped(self):
        """用户拖过列宽后：等比缩放保留手感，但以硬底线托底（总宽不溢出）。"""
        app = self._app()
        try:
            app._tree_col_custom = True
            app._tree_col_cache = (400, 300, 200, 200, 100)  # 用户拖出的比例
            app._fit_tree_columns(288)
            widths = self._widths(app)
            self.assertEqual(sum(widths), self._avail(app, 288),
                             "托底后总宽必须仍等于可视宽")
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
