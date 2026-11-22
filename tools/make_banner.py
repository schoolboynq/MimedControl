"""Draw the README banner: two cameras watching a gloved hand inside an invisible box."""
from math import atan2, cos, hypot, sin
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from make_assets import glove

ROOT = Path(__file__).resolve().parents[1]
FONT = "DMSans[opsz,wght].ttf"  # put the variable font next to this script, or install it
BG, INK, MUTED, AMBER = (22, 24, 29), (242, 239, 233), (167, 161, 149), (255, 181, 71)
S = 2  # supersampling


def font(size, weight="Bold"):
    try:
        f = ImageFont.truetype(FONT, size * S)  # DM Sans, from Google Fonts
    except OSError:
        return ImageFont.load_default(size * S)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def dashed(draw, a, b, color, width, dash=14, gap=10):
    (x1, y1), (x2, y2) = a, b
    length = hypot(x2 - x1, y2 - y1)
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    t = 0.0
    while t < length:
        e = min(length, t + dash * S)
        draw.line((x1 + ux * t, y1 + uy * t, x1 + ux * e, y1 + uy * e), fill=color, width=width)
        t += (dash + gap) * S


def camera(draw, x, y, angle, color=(236, 232, 224)):
    """A webcam body with a lens, rotated to look along angle."""
    w, h = 96 * S, 46 * S
    c, s = cos(angle), sin(angle)
    def rot(px, py):
        return x + px * c - py * s, y + px * s + py * c
    body = [rot(-h / 2, -w / 2), rot(h / 2, -w / 2), rot(h / 2, w / 2), rot(-h / 2, w / 2)]
    draw.polygon(body, fill=color)
    lx, ly = rot(h / 2 - 2 * S, 0)
    for r, fill in ((19 * S, (40, 42, 48)), (12 * S, (18, 19, 23)), (5 * S, AMBER)):
        draw.ellipse((lx - r, ly - r, lx + r, ly + r), fill=fill)
    return lx, ly


def banner(width=1280, height=640, path=None):
    W, H = width * S, height * S
    image = Image.new("RGBA", (W, H), BG + (255,))
    # Spotlight from above onto the hand.
    light = Image.new("L", (W, H), 0)
    ImageDraw.Draw(light).polygon([(0.66 * W, -0.05 * H), (0.80 * W, -0.05 * H), (0.98 * W, 0.98 * H), (0.48 * W, 0.98 * H)], fill=48)
    ImageDraw.Draw(light).ellipse((0.50 * W, 0.70 * H, 0.96 * W, 1.02 * H), fill=55)
    light = light.filter(ImageFilter.GaussianBlur(40 * S))
    image = Image.composite(Image.new("RGBA", (W, H), (255, 190, 96, 255)), image, light)
    draw = ImageDraw.Draw(image, "RGBA")
    cx, cy, size = 0.725 * W, 0.56 * H, 300 * S
    # The invisible box, drawn in dashes.
    front = 250 * S
    back_dx, back_dy = 70 * S, -60 * S
    f = [(cx - front / 2, cy - front / 2 + 20 * S), (cx + front / 2, cy - front / 2 + 20 * S),
         (cx + front / 2, cy + front / 2 + 20 * S), (cx - front / 2, cy + front / 2 + 20 * S)]
    b = [(x + back_dx, y + back_dy) for x, y in f]
    edge = (242, 239, 233, 110)
    for i in range(4):
        dashed(draw, b[i], b[(i + 1) % 4], edge, 2 * S)
        dashed(draw, b[i], f[i], edge, 2 * S)
        dashed(draw, f[i], f[(i + 1) % 4], (242, 239, 233, 150), 2 * S)
    # Two cameras, each looking at the hand along dashed sight lines.
    target = (cx + 10 * S, cy - 10 * S)
    for x, y in ((0.53 * W, 0.12 * H), (0.93 * W, 0.15 * H)):
        angle = atan2(target[1] - y, target[0] - x)
        lens = camera(draw, x, y, angle)
        for dx, dy in ((-60, -80), (0, -110), (55, -70), (0, 60)):
            dashed(draw, lens, (target[0] + dx * S, target[1] + dy * S), (255, 181, 71, 150), 2 * S, dash=8, gap=8)
    # The gloved hand.
    hand = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glove(ImageDraw.Draw(hand), size)
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow.putalpha(hand.split()[3].point(lambda a: a * 0.45))
    shadow = shadow.filter(ImageFilter.GaussianBlur(12 * S))
    image.alpha_composite(shadow, (int(cx - size / 2 + 14 * S), int(cy - size / 2 + 22 * S)))
    image.alpha_composite(hand, (int(cx - size / 2), int(cy - size / 2)))
    for f_ in ((0.37, 0.215), (0.472, 0.165), (0.572, 0.195), (0.665, 0.285), (0.245, 0.505)):
        x, y = cx + (f_[0] - 0.5) * size, cy + (f_[1] - 0.5) * size
        r = 8 * S
        draw.ellipse((x - r, y - r, x + r, y + r), fill=AMBER, outline=(27, 20, 6), width=2 * S)
    # Wordmark and tagline.
    x0, y0 = 72 * S, 190 * S
    room = f[0][0] - 40 * S - x0  # keep the wordmark clear of the box
    size_pt = 104
    title = font(size_pt)
    while size_pt > 60 and draw.textlength("MimedControl", font=title) > room:
        size_pt -= 2
        title = font(size_pt)
    draw.text((x0, y0), "Mimed", font=title, fill=INK)
    draw.text((x0 + draw.textlength("Mimed", font=title), y0), "Control", font=title, fill=AMBER)
    tag = font(34, "Medium")
    for i, line in enumerate(("Two webcams. One invisible box.", "Your hands are the mouse.")):
        draw.text((x0 + 4 * S, y0 + 140 * S + i * 46 * S), line, font=tag, fill=INK if i else MUTED)
    small = font(20, "Regular")
    draw.text((x0 + 4 * S, y0 + 260 * S), "Point · click · drag · scroll · zoom  —  free and open source", font=small, fill=MUTED)
    out = image.convert("RGB").resize((width, height), Image.LANCZOS)
    if path:
        out.save(path, optimize=True)
    return out


if __name__ == "__main__":
    banner(path=ROOT / "docs" / "images" / "banner.png")
    print("banner written")
