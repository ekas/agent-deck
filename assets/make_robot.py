"""Robot-head app icon generator for Agent Deck.

Frost look: dark rounded head, cyan glowing eyes (the app's "thinking" color),
green antenna tip (the "done" color). Rendered with supersampling for clean AA;
each target size is drawn from a detail level appropriate to that size.
"""
import os
from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.dirname(os.path.abspath(__file__))

CYAN   = (126, 203, 255, 255)   # #7ecbff  – eyes / border (thinking glow)
GREEN  = (110, 231, 168, 255)   # #6ee7a8  – antenna tip (done glow)
# Head gradient: a MID frost-steel, not near-black. A dark body vanishes on Win11's
# dark taskbar/titlebar (they are dark too); this mid tone reads on dark AND light.
BODY_T = (92, 102, 138, 255)    # head gradient top   (#5c668a)
BODY_B = (56, 63, 90, 255)      # head gradient bottom (#383f5a)
DIM    = (120, 178, 222, 255)   # cyan for grille / side nubs (reads on the lighter body)


def _grad(size, top, bot):
    """Vertical gradient RGBA image."""
    w, h = size
    base = Image.new("RGBA", size)
    px = base.load()
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        for x in range(w):
            px[x, y] = (r, g, b, 255)
    return base


def render(size, detail=None, body_t=None, body_b=None, glow_mul=1.4):
    """Draw the robot head. Small sizes use a bolder, simplified layout
    (bigger head + eyes, no ears/mouth) so it stays legible in a 16px titlebar.

    body_t/body_b override the head gradient (top/bottom) so the icon can be tuned
    to read on BOTH dark and light taskbars – a near-black body vanishes on Win11's
    dark chrome, so the shipped values are a mid frost-steel."""
    body_t = body_t or BODY_T
    body_b = body_b or BODY_B
    compact = size < 28
    if detail is None:
        detail = size >= 40
    SS = 8
    W = size * SS
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    glow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    gd = ImageDraw.Draw(glow)

    u = W / 100.0  # unit = 1% of canvas, layout in "percent" coords

    # Layout in percent coords; compact pushes the head bigger and the eyes wider.
    if compact:
        hx0, hy0, hx1, hy1 = 14, 22, 86, 90
        rad = 20
        eye = [(27, 45), (55, 73)]            # (x0, x1)
        ey0, ey1 = 46, 66
        eye_r = 10
        ant_top, dot_r = 7, 7.0
        head_top = hy0
    else:
        hx0, hy0, hx1, hy1 = 20, 25, 80, 83
        rad = 17
        eye = [(31, 46), (54, 69)]
        ey0, ey1 = 43, 60
        eye_r = 7.5
        ant_top, dot_r = 8, 5.2
        head_top = hy0

    # ── Antenna (behind head): stalk + glowing tip ────────
    ant_x = 50 * u
    if not compact:
        d.line([(ant_x, (ant_top + 3) * u), (ant_x, (head_top + 2) * u)],
               fill=(150, 170, 190, 255), width=max(1, int(2.2 * u)))
    dr = dot_r * u
    gd.ellipse([ant_x - dr * 1.9, (ant_top) * u - dr * 1.9,
                ant_x + dr * 1.9, (ant_top) * u + dr * 1.9], fill=GREEN)
    d.ellipse([ant_x - dr, ant_top * u - dr, ant_x + dr, ant_top * u + dr],
              fill=GREEN)
    d.ellipse([ant_x - dr * 0.42, ant_top * u - dr * 0.42,
               ant_x + dr * 0.42, ant_top * u + dr * 0.42],
              fill=(235, 255, 245, 255))

    # ── Side sensors / ears (skipped when compact) ────────
    ear_fill = tuple(min(255, c + 8) for c in body_t[:3]) + (255,)
    if not compact:
        ear_w, ear_h = 6 * u, 20 * u
        ear_y0, ear_y1 = 44 * u, 44 * u + ear_h
        for exx in (13 * u, 81 * u):
            d.rounded_rectangle([exx, ear_y0, exx + ear_w, ear_y1],
                                radius=3 * u, fill=ear_fill,
                                outline=CYAN, width=max(1, int(1.4 * u)))

    # ── Head body (gradient fill + cyan glowing border) ───
    box = [hx0 * u, hy0 * u, hx1 * u, hy1 * u]
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=rad * u, fill=255)
    grad = _grad((W, W), body_t, body_b)
    img.paste(grad, (0, 0), mask)

    bw = 3.6 if compact else 3.2
    gd.rounded_rectangle(box, radius=rad * u, outline=CYAN, width=max(2, int(bw * u)))
    d.rounded_rectangle(box, radius=rad * u, outline=CYAN,
                        width=max(1, int((2.4 if compact else 2.0) * u)))

    # ── Eyes (capsule, glowing cyan, bright core) ─────────
    for ex0, ex1 in eye:
        gd.rounded_rectangle([(ex0 - 2) * u, (ey0 - 2) * u, (ex1 + 2) * u, (ey1 + 2) * u],
                             radius=(eye_r + 1) * u, fill=CYAN)
        d.rounded_rectangle([ex0 * u, ey0 * u, ex1 * u, ey1 * u],
                            radius=eye_r * u, fill=CYAN)
        cw = (ex1 - ex0) * 0.24     # horizontal inset of the bright core
        d.rounded_rectangle([(ex0 + cw) * u, (ey0 + 2.5) * u,
                             (ex1 - cw) * u, (ey1 - 2.5) * u],
                            radius=(eye_r - 2) * u, fill=(240, 252, 255, 255))

    # ── Mouth grille (only at larger sizes) ───────────────
    if detail:
        gy0, gy1 = 68 * u, 74 * u
        bars = 4
        bx0, bx1 = 38 * u, 62 * u
        gap = (bx1 - bx0) / (bars * 2 - 1)
        for i in range(bars):
            x = bx0 + i * 2 * gap
            d.rounded_rectangle([x, gy0, x + gap, gy1], radius=1.2 * u, fill=DIM)

    # ── Compose glow under crisp art ──────────────────────
    blur = max(2, int((1.6 if compact else 2.2) * u))
    glow = glow.filter(ImageFilter.GaussianBlur(blur))
    glow = Image.eval(glow, lambda a: min(255, int(a * 0.85 * glow_mul)))
    out = Image.alpha_composite(glow, img)

    return out.resize((size, size), Image.LANCZOS)


def make():
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    imgs = {s: render(s) for s in sizes}
    ico_path = os.path.join(OUT, "robot.ico")
    imgs[256].save(ico_path, format="ICO",
                   sizes=[(s, s) for s in sizes])
    imgs[256].save(os.path.join(OUT, "robot_256.png"))
    imgs[64].save(os.path.join(OUT, "robot_64.png"))
    print("wrote", ico_path)

    if not os.environ.get("ROBOT_PREVIEW"):
        return

    # ── Preview sheet for visual inspection ───────────────
    pad = 24
    tile = 300
    W = tile * 2 + pad * 3
    H = tile + pad * 2 + 180
    sheet = Image.new("RGBA", (W, H), (18, 18, 24, 255))
    # dark tile
    big = imgs[256]
    sheet.alpha_composite(big.resize((tile, tile)), (pad, pad))
    # light tile
    light = Image.new("RGBA", (tile, tile), (232, 232, 238, 255))
    light.alpha_composite(big.resize((tile, tile)))
    sheet.alpha_composite(light, (pad * 2 + tile, pad))
    # small sizes row (actual pixel size, scaled x4 nearest to inspect crispness)
    y = pad * 2 + tile
    x = pad
    for s in [16, 24, 32, 48]:
        crop = imgs[s]
        show = crop.resize((s * 3, s * 3), Image.NEAREST)
        sheet.alpha_composite(show, (x, y + (48 * 3 - s * 3)))
        # actual size next to it
        sheet.alpha_composite(crop, (x, y + 48 * 3 + 12))
        x += s * 3 + 30
    sheet.convert("RGB").save(os.path.join(OUT, "preview.png"))
    print("wrote preview.png")


if __name__ == "__main__":
    make()
