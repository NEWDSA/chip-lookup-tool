# -*- coding: utf-8 -*-
"""
synthetic_card.py
-----------------
为"详情卡片"在屏幕截图模式无法装下所有内容时，提供一个数据驱动的备用方案：
直接读取数据库记录，用 Pillow 画一张完整的卡片 PNG。

这样无论屏幕多小，都能 100% 拿到完整字段。
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont  # type: ignore


# 配色（与 ui.py 一致）
COLOR_BG = "#0e1620"
COLOR_CARD = "#1c2735"
COLOR_BORDER = "#2a3a4f"
COLOR_TEXT = "#e6edf3"
COLOR_TEXT_DIM = "#8aa0b4"
COLOR_ACCENT = "#16c47e"
COLOR_FOCUS = "#2dd4bf"


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """优先找一个支持中文的字体，失败回退到默认。"""
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_record_card(record: dict, out_path: str, width: int = 1100) -> str:
    """画一张完整的 detail 卡片 PNG，覆盖记录的全部字段。"""
    # 卡片分组（与 ui.py._render_detail 保持一致）
    head = [
        ("part_number", "料号"),
        ("model", "型号"),
    ]
    basic = [
        ("manufacturer", "厂商 Manufacturer"),
        ("type", "类型 Type"),
        ("model", "型号 Model"),
    ]
    storage = [
        ("capacity", "容量 Capacity"),
        ("bit_width", "位宽 Bit Width"),
        ("speed", "速度 Speed"),
        ("voltage", "电压 Voltage"),
    ]
    physical = [
        ("package", "封装 Package"),
        ("dimensions", "尺寸 Dimensions"),
        ("die_count", "Die 数 Die Count"),
        ("cs_count", "CS 数 CS Count"),
        ("die_revision", "Die 版本 Die Rev"),
    ]
    usage = [
        ("op_temp", "工作温度 Op Temp"),
        ("notes", "备注 Notes"),
    ]

    title = str(record.get("part_number") or "")
    subtitle = str(record.get("model") or "")
    # 头部 tag：厂商 类型 容量 位宽
    tags = []
    for k in ("manufacturer", "type", "capacity", "bit_width"):
        v = record.get(k)
        if v:
            tags.append(f"[{v}]")
    tag_line = " ".join(tags)

    # 计算总高度
    pad_x = 24
    pad_y = 22
    head_h = 110  # 头部卡片
    section_h = 32  # 每段标题
    row_h = 30
    card_gap = 12
    card_pad = 18

    def _section_height(items: list) -> int:
        rows = sum(1 for k, _ in items if record.get(k))
        return section_h + row_h * max(rows, 1) + card_pad * 2

    total_h = (
        pad_y
        + head_h
        + card_gap
        + _section_height(basic)
        + card_gap
        + _section_height(storage)
        + card_gap
        + _section_height(physical)
        + card_gap
        + _section_height(usage)
        + pad_y
    )

    img = Image.new("RGB", (width, total_h), COLOR_BG)
    d = ImageDraw.Draw(img)

    f_title = _load_font(36)
    f_sub = _load_font(22)
    f_tag = _load_font(18)
    f_section = _load_font(20)
    f_label = _load_font(17)
    f_value = _load_font(19)

    def _round_rect(xy, radius, fill, outline=None, width=1):
        d.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)

    def _draw_card(x, y, w, h, fill=COLOR_CARD, border=COLOR_BORDER):
        _round_rect((x, y, x + w, y + h), 12, fill=fill, outline=border, width=1)

    # 头部
    cx, cy = pad_x, pad_y
    cw = width - pad_x * 2
    _draw_card(cx, cy, cw, head_h)
    d.text((cx + 20, cy + 16), title, fill=COLOR_TEXT, font=f_title)
    d.text((cx + 20, cy + 60), subtitle, fill=COLOR_TEXT, font=f_sub)
    if tag_line:
        d.text((cx + 20, cy + 88), tag_line, fill=COLOR_TEXT_DIM, font=f_tag)
    cy += head_h + card_gap

    # 基本
    sh = _section_height(basic)
    _draw_card(cx, cy, cw, sh)
    d.text((cx + card_pad, cy + 14), "基本", fill=COLOR_TEXT, font=f_section)
    # 字段
    label_w = int((cw - card_pad * 2) * 0.42)
    yy = cy + section_h + 4
    for k, label in basic:
        val = record.get(k)
        if not val:
            continue
        d.text((cx + card_pad, yy), label, fill=COLOR_TEXT_DIM, font=f_label)
        d.text((cx + card_pad + label_w, yy), str(val), fill=COLOR_TEXT, font=f_value)
        yy += row_h
    cy += sh + card_gap

    # 存储参数
    sh = _section_height(storage)
    _draw_card(cx, cy, cw, sh)
    d.text((cx + card_pad, cy + 14), "存储参数", fill=COLOR_TEXT, font=f_section)
    yy = cy + section_h + 4
    for k, label in storage:
        val = record.get(k)
        if not val:
            continue
        d.text((cx + card_pad, yy), label, fill=COLOR_TEXT_DIM, font=f_label)
        d.text((cx + card_pad + label_w, yy), str(val), fill=COLOR_TEXT, font=f_value)
        yy += row_h
    cy += sh + card_gap

    # 物理信息
    sh = _section_height(physical)
    _draw_card(cx, cy, cw, sh)
    d.text((cx + card_pad, cy + 14), "物理信息", fill=COLOR_TEXT, font=f_section)
    yy = cy + section_h + 4
    for k, label in physical:
        val = record.get(k)
        if not val:
            continue
        d.text((cx + card_pad, yy), label, fill=COLOR_TEXT_DIM, font=f_label)
        d.text((cx + card_pad + label_w, yy), str(val), fill=COLOR_TEXT, font=f_value)
        yy += row_h
    cy += sh + card_gap

    # 使用条件
    sh = _section_height(usage)
    _draw_card(cx, cy, cw, sh)
    d.text((cx + card_pad, cy + 14), "使用条件", fill=COLOR_TEXT, font=f_section)
    yy = cy + section_h + 4
    for k, label in usage:
        val = record.get(k)
        if not val:
            continue
        d.text((cx + card_pad, yy), label, fill=COLOR_TEXT_DIM, font=f_label)
        d.text((cx + card_pad + label_w, yy), str(val), fill=COLOR_TEXT, font=f_value)
        yy += row_h

    img.save(out_path, "PNG")
    return out_path


if __name__ == "__main__":
    import argparse
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from database import ChipDatabase
    here = os.path.dirname(os.path.abspath(__file__))
    default_csv = os.path.normpath(os.path.join(here, "..", "data", "chip_database.csv"))
    default_out = os.path.normpath(os.path.join(here, "..", "screenshots"))

    parser = argparse.ArgumentParser(description="根据 CSV 记录画一张完整 detail 卡片 PNG")
    parser.add_argument("--db", default=default_csv)
    parser.add_argument("--out-dir", default=default_out)
    parser.add_argument("--part", action="append", help="要画哪几条料号（可多次）；不传则画前 2 条")
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    db = ChipDatabase(args.db)
    recs = db.list_records()
    if args.part:
        targets = [r for r in recs if r.get("part_number") in args.part]
    else:
        targets = recs[:2]
    for r in targets:
        pn = r.get("part_number", "record")
        out = os.path.join(args.out_dir, f"card_{pn}.png")
        render_record_card(r, out)
        print(f"saved: {out} -> {r.get('part_number')} / {r.get('model')}")
