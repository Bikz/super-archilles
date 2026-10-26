"""Make an image's white background transparent (for the mascot on the dark UI).

usage: python scripts/cutout.py in.png out.png
Flood-fills near-white from the image edges only, so white inside the character (eyes,
highlights) stays, then softens the edge so it sits cleanly on dark backgrounds.
"""

import sys
from collections import deque

from PIL import Image, ImageFilter

src, dst = sys.argv[1], sys.argv[2]
img = Image.open(src).convert("RGBA")
w, h = img.size
px = img.load()


def whiteish(p):
    # near-white, plus the light grey of soft floor shadows (bright and unsaturated)
    lo, hi = min(p[:3]), max(p[:3])
    return lo > 188 and hi - lo < 22


bg = bytearray(w * h)
q = deque((x, y) for x in range(w) for y in (0, h - 1)) + deque((x, y) for y in range(h) for x in (0, w - 1))
while q:
    x, y = q.popleft()
    i = y * w + x
    if bg[i] or not whiteish(px[x, y]):
        continue
    bg[i] = 1
    for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
        if 0 <= nx < w and 0 <= ny < h and not bg[ny * w + nx]:
            q.append((nx, ny))

# pull the edge in a pixel before softening it, so no light fringe shows on dark backgrounds
mask = Image.frombytes("L", (w, h), bytes(0 if b else 255 for b in bg))
mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.9))
img.putalpha(mask)
img = img.crop(img.getbbox())  # trim the empty margins
img.thumbnail((900, 900))
img.save(dst, optimize=True)
print(f"saved {dst} {img.size}")
