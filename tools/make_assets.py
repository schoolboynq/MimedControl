"""Draw MimedControl's logo and app icons (white mime glove in an amber spotlight)."""
from math import atan2, cos, hypot, sin
from pathlib import Path
from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parents[1] / "src" / "mimedcontrol" / "assets"
AMBER, AMBER_DEEP, GLOVE, CUFF = (255, 181, 71), (233, 154, 44), (251, 248, 242), (226, 218, 205)


def capsule(draw, a, b, width, fill):
    (x1, y1), (x2, y2) = a, b
    angle = atan2(y2 - y1, x2 - x1)
    dx, dy = -sin(angle) * width / 2, cos(angle) * width / 2
    draw.polygon([(x1 + dx, y1 + dy), (x2 + dx, y2 + dy), (x2 - dx, y2 - dy), (x1 - dx, y1 - dy)], fill=fill)
    for x, y in (a, b):
        draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=fill)


def glove(draw, n, ox=0.0, oy=0.0, scale=1.0):
    def p(x, y):
        return ox + (0.5 + (x - 0.5) * scale) * n, oy + (0.5 + (y - 0.5) * scale) * n
    s = scale * n
    # Fingers, slightly fanned, with gaps where the spotlight shows through.
    for (bx, by), (tx, ty) in (((0.395, 0.52), (0.37, 0.215)), ((0.478, 0.50), (0.472, 0.165)),
                               ((0.561, 0.51), (0.572, 0.195)), ((0.638, 0.55), (0.665, 0.285))):
        capsule(draw, p(bx, by), p(tx, ty), 0.078 * s, GLOVE)
    capsule(draw, p(0.40, 0.66), p(0.245, 0.505), 0.086 * s, GLOVE)  # thumb
    x1, y1 = p(0.335, 0.45); x2, y2 = p(0.685, 0.76)
    draw.rounded_rectangle((x1, y1, x2, y2), radius=0.11 * s, fill=GLOVE)  # palm
    x1, y1 = p(0.355, 0.73); x2, y2 = p(0.665, 0.855)
    draw.rounded_rectangle((x1, y1, x2, y2), radius=0.035 * s, fill=CUFF)  # cuff
    for k in range(3):  # the cuff's ribbing
        yy = y1 + (y2 - y1) * (0.3 + 0.2 * k)
        draw.line((x1 + 0.03 * s, yy, x2 - 0.03 * s, yy), fill=(200, 190, 174), width=max(1, round(0.008 * s)))


def emblem(size, background=None):
    n = size * 8
    image = Image.new("RGBA", (n, n), background or (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Spotlight disc with a soft rim.
    draw.ellipse((0.02 * n, 0.02 * n, 0.98 * n, 0.98 * n), fill=AMBER_DEEP)
    draw.ellipse((0.05 * n, 0.04 * n, 0.95 * n, 0.94 * n), fill=AMBER)
    glove(draw, n, scale=0.9, oy=0.01 * n)
    return image.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    ASSETS.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 48, 64, 128, 256):
        emblem(size).save(ASSETS / f"icon-{size}.png", optimize=True)
    for size in (40, 50, 60, 80):
        emblem(size).save(ASSETS / f"logo-{size}.png", optimize=True)
    big = emblem(512)
    big.save(ASSETS.parents[2] / "docs" / "images" / "logo.png", optimize=True)
    big.save(ASSETS.parents[2] / "docs" / "images" / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(sorted(p.name for p in ASSETS.iterdir()))
