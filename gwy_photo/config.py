"""Read the original photo.ini parameter file.

Defaults below are the strings compiled into PhotoProcess.exe (see ANALYSIS.md).
The ini shipped with the original package overrides s_max=0.1 and i_max=0.7.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict


@dataclass
class Params:
    min_width: int = 295
    min_height: int = 413
    dst_width: int = 295
    dst_height: int = 413
    ratio_x_min: float = 0.0
    ratio_x_max: float = 0.5
    ratio_y_min: float = 0.0
    ratio_y_max: float = 0.5
    blue_h_min: float = 2.5
    blue_h_max: float = 4.5
    red_h_min: float = 0.5
    red_h_max: float = 5.0
    i_min: float = 0.1
    i_max: float = 0.9
    s_min: float = 0.05
    s_max: float = 0.3
    # Not read from the ini by the original tool, but kept here for the port.
    head_ratio_min: float = 0.32
    face_scale: float = 1.1
    face_min_neighbors: int = 2
    face_min_size: int = 30
    crop_scale: float = 1.6181229773462784  # 1 / 0.618 (golden ratio)
    jpeg_quality: int = 40
    raw: Dict[str, str] = field(default_factory=dict)


def load_params(ini_path: str | Path | None = None) -> Params:
    p = Params()
    if not ini_path:
        return p
    path = Path(ini_path)
    if not path.is_file():
        return p
    data: Dict[str, str] = {}
    # The original uses GetPrivateProfileStringA, i.e. ANSI/GBK file, section PhotoInfo.
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "gbk", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if section != "PhotoInfo" or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip().lower()] = v.strip()
    p.raw = data

    def get_int(key: str, default: int) -> int:
        try:
            return int(float(data[key]))
        except (KeyError, ValueError):
            return default

    def get_float(key: str, default: float) -> float:
        try:
            return float(data[key])
        except (KeyError, ValueError):
            return default

    p.min_width = get_int("min_width", p.min_width)
    p.min_height = get_int("min_height", p.min_height)
    p.dst_width = get_int("dst_width", p.dst_width)
    p.dst_height = get_int("dst_height", p.dst_height)
    p.ratio_x_min = get_float("ratio_x_min", p.ratio_x_min)
    p.ratio_x_max = get_float("ratio_x_max", p.ratio_x_max)
    p.ratio_y_min = get_float("ratio_y_min", p.ratio_y_min)
    p.ratio_y_max = get_float("ratio_y_max", p.ratio_y_max)
    p.blue_h_min = get_float("blue_h_min", p.blue_h_min)
    p.blue_h_max = get_float("blue_h_max", p.blue_h_max)
    p.red_h_min = get_float("red_h_min", p.red_h_min)
    p.red_h_max = get_float("red_h_max", p.red_h_max)
    p.i_min = get_float("i_min", p.i_min)
    p.i_max = get_float("i_max", p.i_max)
    p.s_min = get_float("s_min", p.s_min)
    p.s_max = get_float("s_max", p.s_max)
    return p
