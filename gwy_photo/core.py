"""Image pipeline, reverse-engineered from PhotoProcess.exe 1.0.

The original program (MFC + OpenCV 1.0 + CxImage) does, in order:

1. Reject files whose first two bytes are not FF D8 (JPEG SOI).
2. Reject photos narrower than min_width or shorter than min_height.
3. Background check: average three 3x3 patches at (0.1w,0.1h),
   (0.1w,0.25h) and (0.9w,0.167h).  Each patch is converted to HSI.
   All three patches must be classified the same way (white or blue),
   otherwise the photo is rejected.
4. Viola-Jones face detection on the grayscale image
   (haarcascade_frontalface_alt2, scale 1.1, minNeighbors 2,
   Canny pruning, min size 30x30).  The widest face wins.
5. face_width / image_width must be >= 0.32 (head-proportion check).
6. Crop a golden-ratio window around the face, adjust the window to the
   target aspect ratio, verify the face margins, then resize the crop to
   exactly dst_width x dst_height (295x413 by default).
7. Saving: cvSaveImage (quality 95), re-encode with libjpeg at quality 40,
   then append the 10-byte marker b"\\xff\\x02\\x00\\x08gjgwy2".

The public functions below preserve the original status codes:
0 = success, 1 = not JPEG, 2 = too few pixels, 3 = bad background,
4 = bad head proportion, 5 = no face found.
"""
from __future__ import annotations

import io
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
from PIL import Image

from .config import Params

MARKER = b"\xff\x02\x00\x08gjgwy2"  # 10 bytes appended after the JPEG EOI

MESSAGES = {
    0: "照片符合要求，可以保存。",
    1: "打开照片文件类型不是JPG/JPEG格式，请选择JPG/JPEG格式照片文件。",
    2: "打开照片文件像素数低。请选择高于或符合像素数要求的照片文件。",
    3: "打开照片文件背景不符合要求。请选择白色或蓝色背景照片文件。",
    4: "打开照片文件人像头部比例不符合要求。请选择头部比例适当的照片文件。",
    5: "打开照片文件不符合要求。请选择正面人像标准证件照片文件。",
}

TWO_PI = 6.28318  # exactly the constant used by the original binary


@dataclass
class Patch:
    x: int
    y: int
    b: float
    g: float
    r: float
    h: float
    s: float
    i: float
    kind: int


@dataclass
class Result:
    status: int
    message: str
    image: Optional[np.ndarray] = None       # processed BGR image (dst size)
    source: Optional[np.ndarray] = None      # original BGR image
    face: Optional[Tuple[int, int, int, int]] = None
    crop: Optional[Tuple[int, int, int, int]] = None
    patches: list[Patch] = field(default_factory=list)
    ratios: Dict[str, float] = field(default_factory=dict)
    info: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == 0


# --------------------------------------------------------------------------
# JPEG / file helpers
# --------------------------------------------------------------------------

def is_jpeg_file(path: str | Path) -> bool:
    """The original only checks the SOI marker, not the extension."""
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"\xff\xd8"
    except OSError:
        return False


def read_image(path: str | Path) -> Optional[np.ndarray]:
    """BGR image, EXIF orientation ignored (OpenCV 1.0 had no EXIF support)."""
    import cv2

    img = cv2.imread(
        str(path), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION
    )
    return img


# --------------------------------------------------------------------------
# Background check (FUN_401000 / FUN_401070)
# --------------------------------------------------------------------------

def _hs_i(b: float, g: float, r: float) -> Tuple[float, float, float]:
    mn = min(r, g, b)
    total = r + g + b
    i = total / 3.0
    # Saturation: S = 1 - 3*min(r,g,b)/(r+g+b)
    s = 1.0 - (3.0 / total) * mn if total != 0 else 0.0
    num = 0.5 * ((r - g) + (r - b))
    den = math.sqrt((r - g) ** 2 + (r - b) * (g - b)) if (r - g) ** 2 + (r - b) * (g - b) > 0 else 0.0
    if den == 0:
        h = 0.0
    else:
        arg = max(-1.0, min(1.0, num / den))
        h = math.acos(arg)
        if g < b:
            h = TWO_PI - h
    return h, s, i


def classify_hsi(h: float, s: float, i: float, p: Params) -> int:
    """0 = white background, 1 = blue background, 3 = invalid."""
    if i >= p.i_min:
        if i >= p.i_max and s <= p.s_min:
            return 0
        if p.blue_h_min <= h <= p.blue_h_max and s >= p.s_max:
            return 1
    return 3


def background_patches(bgr: np.ndarray, p: Params) -> Tuple[int, list[Patch]]:
    h_img, w_img = bgr.shape[:2]
    xs = [w_img // 10, w_img // 10, w_img - w_img // 10]
    ys = [h_img // 10, h_img // 4, h_img // 6]
    patches: list[Patch] = []
    for x, y in zip(xs, ys):
        # 3x3 block, average normalized by 9*255 (0.00043572984749455336)
        block = bgr[max(0, y - 1): y + 2, max(0, x - 1): x + 2].astype(np.float64)
        if block.size == 0:
            block = bgr[y:y + 1, x:x + 1].astype(np.float64)
        b = float(block[..., 0].sum()) / 2295.0
        g = float(block[..., 1].sum()) / 2295.0
        r = float(block[..., 2].sum()) / 2295.0
        hh, ss, ii = _hs_i(b, g, r)
        patches.append(Patch(x, y, b, g, r, hh, ss, ii, classify_hsi(hh, ss, ii, p)))
    kinds = {pt.kind for pt in patches}
    if len(kinds) != 1:
        return 3, patches
    return kinds.pop(), patches


# --------------------------------------------------------------------------
# Face detection & crop geometry (FUN_4013a0)
# --------------------------------------------------------------------------

def detect_face(
    bgr: np.ndarray, cascade, p: Params
) -> Optional[Tuple[int, int, int, int]]:
    import cv2

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(
        gray,
        scaleFactor=p.face_scale,
        minNeighbors=p.face_min_neighbors,
        minSize=(p.face_min_size, p.face_min_size),
    )
    if len(faces) == 0:
        return None
    # The original keeps the detection with the largest width (not area).
    best = max(faces, key=lambda r: int(r[2]))
    x, y, w, h = (int(v) for v in best)
    return x, y, w, h


def crop_geometry(
    face: Tuple[int, int, int, int],
    img_w: int,
    img_h: int,
    p: Params,
) -> Tuple[Optional[Tuple[int, int, int, int]], Dict[str, float]]:
    fx, fy, fw, fh = face
    info: Dict[str, float] = {}
    if img_w <= 0 or img_h <= 0:
        return None, info

    # Head size gate: face width / image width >= 0.32
    head_ratio = fw / float(img_w)
    info["head_ratio"] = head_ratio
    if head_ratio < p.head_ratio_min:
        return None, info

    crop_w = int(fw * p.crop_scale)
    # trunc(crop_w * dst_height / dst_width + 0.5)
    crop_h = int((crop_w * p.dst_height) / float(p.dst_width) + 0.5)

    x0 = fx - (crop_w - fw) // 2
    y0 = fy - (crop_h - fh) // 2

    if crop_w > img_w:
        x0 = 0
        crop_w = img_w
    else:
        x0 = max(x0, 0)
        if x0 + crop_w > img_w:
            x0 = img_w - crop_w
    if crop_h > img_h:
        y0 = 0
        crop_h = img_h
    else:
        y0 = max(y0, 0)
        if y0 + crop_h > img_h:
            y0 = img_h - crop_h

    # Keep the crop aspect ratio inside 0.9 .. 1.1 of the target ratio.
    ratio = (crop_h * p.dst_width) / float(crop_w * p.dst_height)
    if ratio < 0.9:
        new_w = int((crop_h * p.dst_width) / float(p.dst_height))
        x0 += (crop_w - new_w) // 2
        crop_w = new_w
    elif ratio > 1.1:
        new_h = int((crop_w * p.dst_height) / float(p.dst_width))
        y0 += (crop_h - new_h) // 2
        crop_h = new_h
    info["aspect_ratio_error"] = ratio

    # Margins around the face, as fractions of the crop.
    left = (fx - x0) / float(crop_w)
    right = ((crop_w - (fx - x0)) - fw) / float(crop_w)
    top = (fy - y0) / float(crop_h)
    info.update(left=left, right=right, top=top)

    if not (p.ratio_x_min <= left <= p.ratio_x_max):
        return None, info
    if not (p.ratio_x_min <= right <= p.ratio_x_max):
        return None, info
    if not (p.ratio_y_min <= top <= p.ratio_y_max):
        return None, info
    return (x0, y0, crop_w, crop_h), info


def process_image(
    bgr: np.ndarray,
    p: Params,
    cascade,
) -> Result:
    h, w = bgr.shape[:2]
    info: Dict[str, Any] = {"width": w, "height": h}

    if w < p.min_width or h < p.min_height:
        return Result(2, MESSAGES[2], source=bgr, info=info)

    kind, patches = background_patches(bgr, p)
    info["background"] = {0: "white", 1: "blue"}.get(kind, "invalid")
    if kind == 3:
        return Result(3, MESSAGES[3], source=bgr, patches=patches, info=info)

    face = detect_face(bgr, cascade, p)
    if face is None:
        return Result(5, MESSAGES[5], source=bgr, patches=patches, info=info)
    info["faces"] = 1

    rect, ratios = crop_geometry(face, w, h, p)
    if rect is None:
        return Result(
            4, MESSAGES[4], source=bgr, face=face, patches=patches,
            ratios=ratios, info=info,
        )

    import cv2

    x0, y0, cw, ch = rect
    # Be forgiving if the aspect-ratio fix pushed the window slightly outside
    # the image (the original would rely on cvSetImageROI clipping).
    ix0 = min(max(x0, 0), max(w - 1, 0))
    iy0 = min(max(y0, 0), max(h - 1, 0))
    ix1 = min(max(x0 + cw, ix0 + 1), w)
    iy1 = min(max(y0 + ch, iy0 + 1), h)
    roi = bgr[iy0:iy1, ix0:ix1]
    out = cv2.resize(roi, (p.dst_width, p.dst_height), interpolation=cv2.INTER_LINEAR)
    info.update(crop={"x": x0, "y": y0, "w": cw, "h": ch})
    return Result(
        0, MESSAGES[0], image=out, source=bgr, face=face, crop=rect,
        patches=patches, ratios=ratios, info=info,
    )


def process_file(path: str | Path, p: Params, cascade) -> Result:
    if not is_jpeg_file(path):
        return Result(1, MESSAGES[1], info={"path": str(path)})
    img = read_image(path)
    if img is None:
        return Result(1, MESSAGES[1], info={"path": str(path)})
    res = process_file_image(img, p, cascade)
    res.info["path"] = str(path)
    return res


def process_file_image(bgr: np.ndarray, p: Params, cascade) -> Result:
    return process_image(bgr, p, cascade)


# --------------------------------------------------------------------------
# Saving (FUN_401f10 + FUN_401e10)
# --------------------------------------------------------------------------

def encode_output_jpeg(img_bgr: np.ndarray, p: Params) -> bytes:
    """Emulate cvSaveImage -> CxImage(q40) -> append 10-byte marker."""
    import cv2

    ok, enc = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    # CxImage decodes and re-encodes with libjpeg quality 40.
    im = Image.open(io.BytesIO(enc.tobytes())).convert("RGB")
    buf = io.BytesIO()
    # CxImage writes a JFIF header with 96x96 dpi (units=1); match it exactly.
    im.save(buf, format="JPEG", quality=p.jpeg_quality, dpi=(96, 96))
    return buf.getvalue() + MARKER


def save_output(img_bgr: np.ndarray, path: str | Path, p: Params) -> int:
    data = encode_output_jpeg(img_bgr, p)
    Path(path).write_bytes(data)
    return len(data)


def jpeg_dimensions(path: str | Path) -> Optional[Tuple[int, int]]:
    """Read SOF dimensions without fully decoding."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        i += 2 + seglen
    return None


def load_cascade(path: str | Path):
    import cv2

    c = cv2.CascadeClassifier(str(path))
    if c.empty():
        raise RuntimeError(f"cannot load Haar cascade: {path}")
    return c
