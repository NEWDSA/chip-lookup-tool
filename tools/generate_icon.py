"""
ChipLookup 应用图标生成器
输入：内嵌 SVG（芯片+放大镜，深底荧光绿主题）
输出：installer/chip_lookup.ico（多分辨率）
"""
import io
import os
from pathlib import Path
from PIL import Image, ImageDraw

SIZES = [16, 24, 32, 48, 64, 128, 256]

BG = (15, 22, 32, 255)
PANEL = (26, 35, 50, 255)
PANEL_EDGE = (0, 217, 126, 110)
ACCENT = (0, 217, 126, 255)
PIN = (42, 53, 72, 255)
PIN_ACTIVE = (0, 217, 126, 255)
LENS_RIM = (15, 22, 32, 140)
DOT_DIM = (58, 104, 120, 255)
TEXT_MAIN = (0, 217, 126, 255)
TEXT_SUB = (90, 104, 120, 255)


def render(size: int) -> Image.Image:
    """在高分辨率画布上画，再降采样到目标尺寸（保 sharp 小细节）。"""
    work = 512
    img = Image.new("RGBA", (work, work), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = int(work * 0.09)
    r = int(work * 0.17)

    d.rounded_rectangle(
        [pad, pad, work - pad, work - pad],
        radius=r,
        fill=BG,
    )

    cx, cy = work // 2, int(work * 0.53)
    pw, ph = int(work * 0.62), int(work * 0.50)
    px0, py0 = cx - pw // 2, cy - ph // 2
    px1, py1 = cx + pw // 2, cy + ph // 2

    d.rounded_rectangle([px0, py0, px1, py1], radius=int(work * 0.03), fill=PANEL)
    inset = int(work * 0.015)
    d.rounded_rectangle(
        [px0 + inset, py0 + inset, px1 - inset, py1 - inset],
        radius=int(work * 0.02),
        outline=PANEL_EDGE,
        width=2,
    )

    pin_w, pin_h = int(work * 0.022), int(work * 0.025)
    pin_gap = int(work * 0.004)

    pin_positions = []
    n = 14
    total_w = n * pin_w + (n - 1) * pin_gap
    start_x = cx - total_w // 2
    for i in range(n):
        x = start_x + i * (pin_w + pin_gap)
        pin_positions.append(("top", x, py0 - pin_h))
        pin_positions.append(("bot", x, py1))

    m = 11
    side_pin_h = int(work * 0.02)
    side_gap = int(work * 0.006)
    total_h = m * side_pin_h + (m - 1) * side_gap
    start_y = cy - total_h // 2
    for i in range(m):
        y = start_y + i * (side_pin_h + side_gap)
        pin_positions.append(("left", px0 - int(work * 0.025), y))
        pin_positions.append(("right", px1, y))

    active_idx = set([7, 10, 16, 22, 25, 33, 41, 47, 52, 55])
    for idx, (side, x, y) in enumerate(pin_positions):
        is_active = idx in active_idx
        color = PIN_ACTIVE if is_active else PIN
        if side in ("top", "bot"):
            d.rectangle([x, y, x + pin_w, y + pin_h], fill=color)
        else:
            sw = int(work * 0.025)
            d.rectangle([x, y, x + sw, y + side_pin_h], fill=color)

    dot_y = py0 + int(work * 0.04)
    dot_r = int(work * 0.008)
    for dx in [-0.36, -0.16, 0.04, 0.24]:
        col = ACCENT if abs(dx - 0.04) < 0.001 else DOT_DIM
        x = cx + int(dx * pw)
        d.ellipse(
            [x - dot_r, dot_y - dot_r, x + dot_r, dot_y + dot_r],
            fill=(*col[:3], 180),
        )

    lx = int(work * 0.66)
    ly = int(work * 0.38)
    lr = int(work * 0.115)
    d.ellipse(
        [lx - lr, ly - lr, lx + lr, ly + lr],
        fill=(15, 22, 32, 170),
        outline=ACCENT,
        width=int(work * 0.014),
    )
    d.ellipse(
        [lx - lr + int(work * 0.012), ly - lr + int(work * 0.012),
         lx + lr - int(work * 0.012), ly + lr - int(work * 0.012)],
        outline=(15, 22, 32, 100),
        width=int(work * 0.003),
    )
    handle_w = int(work * 0.025)
    handle_len = int(lr * 1.2)
    d.line(
        [
            (lx + int(lr * 0.7), ly + int(lr * 0.7)),
            (lx + int(lr * 0.7) + handle_len, ly + int(lr * 0.7) + handle_len),
        ],
        fill=ACCENT,
        width=handle_w,
    )

    if size >= 48:
        try:
            from PIL import ImageFont

            font_paths = [
                r"C:\Windows\Fonts\segoeuib.ttf",
                r"C:\Windows\Fonts\arialbd.ttf",
                r"C:\Windows\Fonts\arial.ttf",
            ]
            font_main = None
            font_sub = None
            for fp in font_paths:
                if os.path.exists(fp):
                    f = ImageFont.truetype(fp, size=int(work * 0.075))
                    fs = ImageFont.truetype(fp, size=int(work * 0.035))
                    font_main = f
                    font_sub = fs
                    break

            if font_main:
                label = "CHIP"
                bbox = d.textbbox((0, 0), label, font=font_main)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                d.text(
                    (cx - tw // 2, cy - th // 2 - int(work * 0.01)),
                    label,
                    fill=TEXT_MAIN,
                    font=font_main,
                )
                sub = "FBGA"
                bbox2 = d.textbbox((0, 0), sub, font=font_sub)
                sw = bbox2[2] - bbox2[0]
                d.text(
                    (cx - sw // 2, cy + int(work * 0.04)),
                    sub,
                    fill=TEXT_SUB,
                    font=font_sub,
                )
        except Exception:
            pass

    out = img.resize((size, size), Image.LANCZOS)
    return out


def main():
    out_dir = Path(r"E:\study\zhicun\chip-lookup-tool\installer")
    out_dir.mkdir(parents=True, exist_ok=True)
    ico_path = out_dir / "chip_lookup.ico"

    largest = render(256)
    largest.save(out_dir / "chip_lookup_preview.png")

    images = [render(s) for s in SIZES]
    images[-1].save(
        ico_path,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=images[:-1],
    )

    print(f"OK -> {ico_path}")
    print("Sizes:", SIZES)
    print("Bytes:", ico_path.stat().st_size)


if __name__ == "__main__":
    main()
