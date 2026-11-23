"""Renders the HOME menu art for a multi-set card (the game tools/build/build_tr_sdmc.py ships): per-set icon and banner.
    python tools/build/make_set_art.py            all sets; writes tools/build/art/sets/<set>/icon.png and banner.png
The HOME menu shows two pictures: the SMDH's 48 x 48 icon (the list rows and the selected tile's corner) and a
banner's picture (the selected tile and the boot splash): its top-left 256 x 128 goes on the console's top screen,
the bottom-right on the touch screen (3dbrew, CBNR layout). Each set keeps its own pair under tools/build/art/sets/:
make_cia.py packs the set's icon.png into its SMDH (make_cia.py, the set parameter); the banner goes on the pages
and mock-ups that draw a flat picture (tools/build/make_cia.py shows the layout). The CIA's own banner is the main
title's 3D scene (make_art.py's banner.bnr): bannertool's CBMD holds a 3D model or a flat picture, never both, so
every set tile opens with the same stereo scene and its icon tells them apart. The outputs are committed; rerun
this only to change the art.
The backgrounds match the main banner's dark stone (make_art.py), so the tiles sit together quietly:
  exp  the base game with both add-ons: the Morrowind wordmark over the Tribunal and Bloodmoon wordmarks
  tr   Tamriel Rebuilt's splash logo
The wordmark sources (Wikipedia/Wikimedia fair-use and tamriel-rebuilt.org) are kept in the set's folder.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "tools" / "build" / "art"
SETS = ART / "sets"
TOP, BOTTOM = (48, 46, 43), (30, 28, 28)         # the stone gradient of tools/build/banner.png (make_art.py)


def bg(w, h):
    """The dark stone backdrop, a vertical gradient between the main banner's top and bottom tones."""
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    return img


def paste_fit(canvas, src, box, margin=0):
    """src.png under the set folder (or an image), scaled to fit box (x, y, w, h) and centered in it."""
    x, y, w, h = box
    im = src if hasattr(src, "size") else Image.open(canvas.folder / src)
    im = im.convert("RGBA")                                        # (the TR logo source is a JPEG: no alpha)
    scale = min((w - 2 * margin) / im.width, (h - 2 * margin) / im.height)
    im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
    canvas.alpha_composite(im, (x + (w - im.width) // 2, y + (h - im.height) // 2))


def screen(folder, w, h):
    canvas = bg(w, h)
    canvas.folder = folder
    return canvas


def wordmarks(c):
    """The base-game-with-add-ons stack: the Morrowind wordmark over the two add-on wordmarks."""
    w, h = c.size
    paste_fit(c, "morrowind.png", (0, int(h * 0.06), w, int(h * 0.52)))
    half = w // 2
    paste_fit(c, "tribunal.png", (0, int(h * 0.60), half, int(h * 0.34)))
    paste_fit(c, "bloodmoon.png", (half, int(h * 0.60), w - half, int(h * 0.34)))


def banner_of(folder, top_fn, bottom_fn=None):
    """The 512 x 256 banner from two 256 x 128 screens: top left, touch screen bottom right (3dbrew, CBNR)."""
    top = screen(folder, 256, 128)
    top_fn(top)
    bottom = screen(folder, 256, 128)
    (bottom_fn or top_fn)(bottom)
    banner = bg(512, 256)
    banner.alpha_composite(top, (0, 0))
    banner.alpha_composite(bottom, (256, 128))
    return top, banner


def save(out, icon, top, banner):
    icon.save(out / "icon.png")
    top.save(out / "banner_top.png")                              # the top screen alone, for pages that show it
    banner.save(out / "banner.png")


def render_exp(out):
    """Base game with the add-ons: the three wordmarks on both screens."""
    icon = screen(out, 48, 48)
    wordmarks(icon)
    top, banner = banner_of(out, wordmarks)
    save(out, icon, top, banner)


def render_tr(out):
    """Tamriel Rebuilt: its splash logo, letterboxed on the stone backdrop; the touch screen a smaller one."""
    icon = screen(out, 48, 48)
    paste_fit(icon, "logo.jpg", (0, 0, 48, 48), 1)
    top, banner = banner_of(out, lambda c: paste_fit(c, "logo.jpg", (0, 0, 256, 128), 6),
                            lambda c: paste_fit(c, "logo.jpg", (0, 0, 256, 128), 22))
    save(out, icon, top, banner)


def main():
    for name, fn in (("exp", render_exp), ("tr", render_tr)):
        out = SETS / name
        fn(out)
        print(f"rendered {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
