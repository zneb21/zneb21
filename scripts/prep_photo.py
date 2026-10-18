"""
Prepare the dp (a flat illustration) for clean ASCII conversion:
  1. remove the background (rembg) so the subject is isolated
  2. bilateral-smooth away the paper texture (otherwise skin turns to `::::`
     noise and the eyes drown in it) while keeping the drawn lines sharp
  3. stretch tones so skin lands near white and hair/lines stay dark
  4. darken the line work (difference-of-gaussians ridges) -- eyelids, pupils,
     glasses frame and smile are thin dark strokes that plain luminance
     averaging washes out at ascii resolution
  5. composite onto white and crop square around the subject

Output: source-prepped.png (grayscale), consumed by make_ascii_svg.py.

    python scripts/prep_photo.py <input.png> [output.png]
"""
import os
import sys

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
if len(sys.argv) < 2:
    raise SystemExit("usage: python scripts/prep_photo.py <source-image> [output.png]")
INP = sys.argv[1]
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "source-prepped.png")

LINE_WEIGHT = 1.05    # strengthen glasses, eyes, hairline, nose, and jaw

# 1. cut out the subject. The supplied cartoon already has a transparent
# background, so reuse its alpha channel and avoid a second AI cutout pass.
original = Image.open(INP).convert("RGBA")
# The supplied 500x500 cartoon includes the torso. Keep the head/face region
# before the reference pipeline computes its square subject crop.
original = original.crop((315, 120, 475, 295))
if np.asarray(original.getchannel("A")).min() < 250:
    cut = original
else:
    from rembg import remove
    cut = remove(original)
rgb = np.array(cut.convert("RGB"))
alpha = np.array(cut.split()[-1])                 # 0 = background
gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

# 2. smooth texture, keep edges
smooth = gray
for _ in range(2):
    smooth = cv2.bilateralFilter(smooth, 9, 40, 9)
smooth = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(smooth)

# 3. tone stretch over the subject only
lo, hi = np.percentile(smooth[alpha > 128], [2, 92])
tone = np.clip((smooth.astype(np.float32) - lo) / (hi - lo), 0, 1)

# 4. dark-on-light ridges -> darken
fine = cv2.GaussianBlur(smooth, (0, 0), 1.0).astype(np.float32)
coarse = cv2.GaussianBlur(smooth, (0, 0), 5).astype(np.float32)
lines = np.clip((coarse - fine) / 28.0, 0, 1)
out = np.clip(tone - LINE_WEIGHT * lines, 0, 1) * 255

# 5. paste onto white (feathered a hair to avoid a halo), square crop
mask = cv2.GaussianBlur(alpha.astype(np.float32) / 255.0, (0, 0), 1.0)
out = out * mask + 255.0 * (1.0 - mask)

ys, xs = np.where(alpha > 20)
side = max(xs.max() - xs.min(), ys.max() - ys.min()) + 60
cx, cy = (xs.min() + xs.max()) // 2, (ys.min() + ys.max()) // 2
canvas = np.full((side, side), 255, np.uint8)
x0, y0 = cx - side // 2, cy - side // 2
sx0, sy0 = max(x0, 0), max(y0, 0)
sx1, sy1 = min(x0 + side, out.shape[1]), min(y0 + side, out.shape[0])
canvas[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = out[sy0:sy1, sx0:sx1].astype(np.uint8)

Image.fromarray(canvas, mode="L").save(OUT)
print("wrote", OUT, canvas.shape)
