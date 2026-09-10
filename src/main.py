# -*- coding: utf-8 -*-
"""
chip_lookup.main
----------------
程序入口。可直接：
    python src/main.py
也可被 src/ 当包运行：
    python -m chip_lookup (假设已 PYTHONPATH=src)

支持三种运行模式（--mode / chiplookup.json 配置）：
    local     本地数据
    upstream  上游数据源
    hybrid    混合（本地 + 上游）
"""

from __future__ import annotations

import argparse
import io
import os
import sys

# 兜底：让打包后 exe 的 stdout/stderr 是 UTF-8，避免中文乱码
try:
    if sys.stdout and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if sys.stderr and hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(HERE))

from config import MODE_LABELS, add_arguments, build_settings  # noqa: E402
from sources import SourceLoadError, make_source  # noqa: E402
from ui import run as run_ui  # noqa: E402


def _parse_args():
    parser = argparse.ArgumentParser(
        description="ChipLookup - 跨平台芯片料号查询器（支持本地数据/上游/混合三种模式）",
    )
    add_arguments(parser)
    parser.add_argument(
        "--headless-stats",
        action="store_true",
        help="不启动 UI，只打印数据源统计信息后退出",
    )
    parser.add_argument(
        "--screenshot",
        help="截图调试：启动 UI → 跑查询 → 截图保存到该路径后退出（需要 Pillow）",
    )
    parser.add_argument(
        "--screenshot-query",
        help="截图前自动填入并查询的料号",
    )
    parser.add_argument(
        "--screenshot-stitch",
        action="store_true",
        help="截图模式（拼接）：截两张（顶部+滚到底部）并垂直拼成一张完整图",
    )
    parser.add_argument(
        "--screenshot-mode",
        choices=["paginate", "load_more"],
        help="截图前设置分页模式",
    )
    parser.add_argument(
        "--selftest-whiteboard",
        action="store_true",
        help="白板自检：合成一张手写算式图，跑完整识别链路并打印结果后退出"
             "（用于验证打包后 torch/transformers/模型权重是否可用）",
    )
    parser.add_argument(
        "--selftest-log",
        help="自检日志输出路径；不指定则写到 exe 旁边的 whiteboard_selftest.log",
    )
    return parser.parse_args()


def _selftest_whiteboard(log_path: str | None) -> int:
    """白板端到端自检：不依赖 Tk，直接构造图像走识别 + 计算。

    这是打包后的关键验证手段：exe 是 windowed（无控制台），白板又只在用户
    点击时才加载模型，出问题很难被发现。用它可以在命令行上一次性确认
    torch / transformers / 模型权重 / sympy 四条链路都通。

    注意 windowed exe 里 sys.stdout 是 None，print 会静默丢弃，所以结果
    必须同时落盘。
    """
    from paths import program_root

    lines: list[str] = []

    def say(msg: str) -> None:
        lines.append(msg)
        print(msg)

    if not log_path:
        log_path = os.path.join(program_root(), "whiteboard_selftest.log")

    code = 1
    try:
        try:
            from PIL import Image, ImageDraw
        except ImportError as exc:
            say("[selftest] 缺少 pillow：%s" % exc)
            raise SystemExit

        from math_canvas import MathRecognizer

        # 合成 "4x4" 的折线笔迹（与 tests/test_whiteboard.py 的样本一致）
        img = Image.new("RGB", (380, 150), "white")
        d = ImageDraw.Draw(img)

        def four(x0):
            d.line([x0 + 30, 32, x0 + 8, 68], fill="black", width=3)
            d.line([x0 + 8, 68, x0 + 46, 68], fill="black", width=3)
            d.line([x0 + 30, 32, x0 + 30, 90], fill="black", width=3)

        four(20)
        d.line([70, 32, 100, 82], fill="black", width=3)
        d.line([100, 32, 70, 82], fill="black", width=3)
        four(120)

        rec = MathRecognizer()
        say("[selftest] 模型目录：%s" % rec._resolve_model_dir())
        latex = rec.recognize(img)
        say("[selftest] 识别结果：%r" % latex)

        result = rec.calculate(latex)
        if "error" in result:
            say("[selftest] 计算失败：%s" % result["error"])
        else:
            say("[selftest] 计算结果：%s" % result["result"])
            if result["result"] == "16":
                say("[selftest] OK：白板识别链路在打包环境下可用")
                code = 0
            else:
                say("[selftest] 结果不是 16，白板链路异常")
    except SystemExit:
        pass
    except Exception as exc:
        import traceback
        say("[selftest] 异常：%s" % exc)
        say(traceback.format_exc())
    finally:
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            print("[selftest] 日志已写入 %s" % log_path)
        except OSError:
            pass
    return code


def _enable_dpi_awareness():
    """Windows 高分屏 DPI 感知：让进程按真实 DPI 渲染，避免被系统位图拉伸发虚。

    必须在创建 Tk 窗口之前调用。逐级尝试：
      1. SetProcessDpiAwareness(1)  —— Win 8.1+（system DPI aware）
      2. SetProcessDPIAware()       —— Vista~Win8 回退
    非 Windows / 已设置过 / 调用失败一律静默忽略（不影响启动）。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main() -> int:
    _enable_dpi_awareness()
    args = _parse_args()

    # 白板自检：不碰数据源，尽早返回
    if args.selftest_whiteboard:
        return _selftest_whiteboard(args.selftest_log)

    settings = build_settings(args)

    for w in settings.warnings:
        print(f"[config] {w}")

    print(f"[ChipLookup] 模式：{MODE_LABELS.get(settings.mode, settings.mode)}")
    try:
        source = make_source(settings)
    except SourceLoadError as exc:
        print(f"[ChipLookup] 启动失败：{exc}", file=sys.stderr)
        return 1
    for w in source.warnings:
        print(f"[source] {w}")
    print(f"[ChipLookup] 数据源：{source.describe()} 共 {source.count()} 条")

    if args.headless_stats:
        recs = source.list_records()
        print(f"[stats] 共 {len(recs)} 条记录")
        vendors: dict[str, int] = {}
        types: dict[str, int] = {}
        for r in recs:
            v = r.get("manufacturer") or "未知"
            t = r.get("type") or "未知"
            vendors[v] = vendors.get(v, 0) + 1
            types[t] = types.get(t, 0) + 1
        for k, v in sorted(vendors.items(), key=lambda x: -x[1]):
            print(f"  厂商 {k}: {v}")
        for k, v in sorted(types.items(), key=lambda x: -x[1]):
            print(f"  类型 {k}: {v}")
        return 0

    run_ui(
        settings,
        source,
        screenshot_to=args.screenshot,
        screenshot_query=args.screenshot_query,
        screenshot_mode=args.screenshot_mode,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())