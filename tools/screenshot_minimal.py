# -*- coding: utf-8 -*-
"""极简模式截图驱动：完整模式 / 极简模式 各截一张，验证布局视觉效果。

注意：使用临时配置文件，避免运行期间的真实配置（窗口尺寸/极简偏好）被覆盖。
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, ROOT)
sys.path.insert(0, SRC)

from config import MODE_LOCAL, Settings
from paths import ensure_user_database
from sources import LocalSource
from ui import App


def pump(app, ms):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.update()
        time.sleep(0.01)


def grab(app, path):
    from PIL import ImageGrab
    app.lift()
    app.attributes("-topmost", True)
    app.update()
    app.attributes("-topmost", False)
    for _ in range(3):
        app.update_idletasks()
        app.update()
    full = ImageGrab.grab()
    sx = full.size[0] / max(1, app.winfo_screenwidth())
    sy = full.size[1] / max(1, app.winfo_screenheight())
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    img = ImageGrab.grab(bbox=(int(x * sx), int(y * sy),
                               int((x + w) * sx), int((y + h) * sy)))
    img.save(path)
    print("saved:", path)


def main():
    s = Settings(mode=MODE_LOCAL, csv_path=ensure_user_database(),
                 config_path=os.path.join(tempfile.mkdtemp(), "cfg.json"))
    app = App(s, LocalSource(s))
    pump(app, 400)  # 默认尺寸 1040x720（新默认，供 UI 评估）

    # 查出候选列表，让两种模式都有真实内容
    app.var_query.set("K4")
    app._run_query()
    pump(app, 300)

    out_dir = os.path.join(ROOT, "screenshots")
    os.makedirs(out_dir, exist_ok=True)

    grab(app, os.path.join(out_dir, "ui_review_default_full.png"))

    app._set_minimal_mode(True, animate=False)
    pump(app, 400)
    grab(app, os.path.join(out_dir, "ui_review_minimal.png"))

    app._set_minimal_mode(False, animate=False)
    pump(app, 300)
    app.geometry("720x560")  # 最小允许尺寸（#5 降档后）
    pump(app, 400)
    grab(app, os.path.join(out_dir, "ui_review_minsize.png"))

    app.destroy()


if __name__ == "__main__":
    main()
