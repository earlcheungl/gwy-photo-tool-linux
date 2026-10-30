"""Command line interface for the Linux photo tool."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import load_params
from .core import encode_output_jpeg, load_cascade, process_file

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INI = APP_DIR / "assets" / "photo.ini"
DEFAULT_CASCADE = APP_DIR / "assets" / "haarcascade_frontalface_alt2.xml"
DEFAULT_OUTPUT_NAME = "报名照片.jpg"


def _fmt_patch(pt) -> str:
    return (
        f"({pt.x},{pt.y}) H={pt.h:.3f} S={pt.s:.3f} I={pt.i:.3f} "
        f"-> {'白底' if pt.kind == 0 else '蓝底' if pt.kind == 1 else '不合格'}"
    )


def _report(res, as_json: bool) -> int:
    payload = {
        "status": res.status,
        "message": res.message,
        "info": res.info,
        "face": res.face,
        "crop": res.crop,
        "ratios": res.ratios,
        "patches": [
            {
                "x": p.x, "y": p.y, "h": p.h, "s": p.s, "i": p.i, "kind": p.kind,
            }
            for p in res.patches
        ],
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if res.ok else res.status or 1

    print(f"结果：{res.message}")
    info = res.info
    if "width" in info:
        print(f"原始尺寸：{info['width']} x {info['height']}")
    bg = info.get("background")
    if bg:
        bg_name = {"white": "白色", "blue": "蓝色"}.get(bg, "不符合")
        print(f"背景判定：{bg_name}")
    for p in res.patches:
        print("  取样 " + _fmt_patch(p))
    if res.face:
        print(f"人脸区域：x={res.face[0]} y={res.face[1]} w={res.face[2]} h={res.face[3]}")
    if res.ratios:
        r = res.ratios
        if "head_ratio" in r:
            print(f"头部宽度占比：{r['head_ratio']:.3f}（要求 >= 0.32）")
        if "left" in r:
            print(
                f"裁剪边距：左={r['left']:.3f} 右={r['right']:.3f} "
                f"上={r['top']:.3f}（要求 0 ~ 0.5）"
            )
    if res.crop:
        print(f"裁剪区域：x={res.crop[0]} y={res.crop[1]} w={res.crop[2]} h={res.crop[3]}")
    return 0 if res.ok else (res.status or 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gwy-photo",
        description="国家公务员考试报名照片处理工具（Linux 移植版）",
        epilog="示例：gwy-photo 照片.jpg -o 报名照片.jpg",
    )
    parser.add_argument("photo", nargs="?", help="待处理的 JPG/JPEG 照片")
    parser.add_argument("-o", "--output", help="输出文件（默认 报名照片.jpg）")
    parser.add_argument("--ini", default=str(DEFAULT_INI), help="photo.ini 路径")
    parser.add_argument("--cascade", default=str(DEFAULT_CASCADE), help="Haar 分类器路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--no-marker", action="store_true", help="不追加 10 字节标记（调试用）")
    parser.add_argument("--gui", action="store_true", help="启动图形界面")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.gui or not args.photo:
        from .gui import run_gui

        return run_gui(args.photo if args.gui else None)

    params = load_params(args.ini)
    try:
        cascade = load_cascade(args.cascade)
    except Exception as exc:  # noqa: BLE001
        print(f"无法加载分类器：{exc}", file=sys.stderr)
        return 1

    res = process_file(args.photo, params, cascade)
    code = _report(res, args.json)

    if res.ok and args.output is not None:
        out = Path(args.output)
        if out.is_dir():
            out = out / DEFAULT_OUTPUT_NAME
        data = encode_output_jpeg(res.image, params)
        if args.no_marker:
            from .core import MARKER

            data = data[: -len(MARKER)] if data.endswith(MARKER) else data
        out.write_bytes(data)
        if not args.json:
            print(f"已保存：{out}（{len(data)} 字节，{params.dst_width}x{params.dst_height}）")
        code = 0
    elif res.ok and args.output is None and not args.json:
        print(f"提示：添加 -o {DEFAULT_OUTPUT_NAME} 即可保存处理后的照片。")

    return code


if __name__ == "__main__":
    sys.exit(main())
